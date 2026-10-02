"""
score.py

Single-script daily lease-collection scoring pipeline, designed to be
imported by main.py for the production cron job:

    from score import run_pipeline
    run_pipeline()                    # scores today
    run_pipeline(date(2026, 7, 5))    # backfill a specific date
    run_pipeline(test=True)           # dry run — no writes, preview output
    run_pipeline(score_only=True)     # skip query/export, read INPUT_TABLE directly

No local caching: every run reads fresh from INPUT_TABLE and writes
directly to OUTPUT_TABLE / LOG_TABLE. Only model artifacts (pkl/csv
under MODEL_DIR) are read from disk — those are locked training
artifacts, not run-time cache.

Steps:
  1. Build the feature-extraction SQL for p_date (query text untouched)
  2. Run it against Oracle, export the result into INPUT_TABLE
  3. Load the data back from INPUT_TABLE (source of truth for scoring)
  4. Prepare features against locked training artifacts
  5. Score (WOE transform -> scorecard -> bin -> probability)
  6. Write scores into OUTPUT_TABLE
  7. Grant SELECT on the pipeline tables to GRANTED_USERS
  8. Write a run summary row into LOG_TABLE (always, even on failure)

CLI flags:
  --test    Dry run. ALL Oracle-mutating calls (DELETE, INSERT/export, GRANT)
            are routed through safe_execute()/safe_export() below, which log
            a preview instead of touching the database when test=True.
            Scoring logic itself is completely unchanged — only writes are
            skipped. Useful for validating a run end-to-end before it
            touches production tables.
  --score   Skip steps 1+2 (build/run the extraction query, export into
            INPUT_TABLE) entirely. Assumes INPUT_TABLE is already populated
            for p_date (e.g. from a prior full run, or backfilled some
            other way) and jumps straight to step 3 (read_data) onward.
            Useful for re-scoring after fixing a bug in score()/artifacts
            without re-running the (slow) Oracle extraction query.

  --test and --score can be combined: re-score from an already-populated
  INPUT_TABLE and preview the output without writing anywhere.
"""

import argparse
import json
import logging
import os
import pickle
import sys
import time
from datetime import datetime, timedelta
from pathlib import Path

import numpy as np
import oracledb
import pandas as pd
import scorecardpy as sc
from dateutil.relativedelta import relativedelta
from dotenv import load_dotenv
from sklearn.preprocessing import KBinsDiscretizer

from src.module.database import oracle_execute, oracle_export, oracle_import, sql_open
from src.module.settings import Settings

load_dotenv()

log_startup = logging.getLogger(__name__)  # placeholder, real logger configured below

# ── Logging setup ──────────────────────────────────────────────────────────
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s  %(levelname)-8s  %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
    handlers=[
        logging.StreamHandler(sys.stdout),
        logging.FileHandler("daily_pipeline.log", encoding="utf-8"),
    ],
)
log = logging.getLogger(__name__)
log.info("Oracle user: %s", Settings().oracle_username)

# ── Paths & config ─────────────────────────────────────────────────────────
# MODEL_DIR holds locked training artifacts (model/card/bins/kbd/features) —
# these are read-only inputs, not run-time cache, so they stay on disk.
MODEL_DIR = Path("src/models")

INPUT_TABLE = "input table"
OUTPUT_TABLE = "score output table"
LOG_TABLE = "log table"

ARTIFACT_NAMES = ["model", "card", "bins", "kbd", "features"]
META_COLS = ["p_date", "userid", "borrowerid", "loanid"] # replace with your actual columns
GRANTED_USERS = [# add if necessary]
LOG_COLS = [
    "p_date",
    "queried_loan_cnt",
    "scored_loan_cnt",
    "receivable_loan_cnt",
    "query_startedat",
    "query_completed",
    "query_insertedat",
    "scoredat",
    "started_at",
    "completed_at",
    "status",
    "error_message",
]

FEATURE_RENAME_MAP = {
   # add your feature maps
}


# ─────────────────────────────────────────────────────────────────────────────
# Centralized write gating — every Oracle-mutating call in this file (DELETE,
# INSERT/export, GRANT) MUST go through one of these two functions, never
# oracle_execute()/oracle_export() directly. This is the single place that
# decides whether test=True actually blocks a write, so a future new write
# path can't accidentally skip the check the way grant_select() once did.
# ─────────────────────────────────────────────────────────────────────────────


def safe_execute(sql: str, test: bool, preview_label: str) -> bool:
    """Guarded oracle_execute(). In test mode, logs the statement and returns
    without touching the database. Returns True on success (or on a
    test-mode no-op), False on failure."""
    if test:
        log.info("[TEST] Would execute -> %s:\n%s", preview_label, sql)
        return True
    try:
        oracle_execute(sql)
        return True
    except Exception as e:
        log.error("Execute failed (%s): %s", preview_label, e)
        return False


def safe_export(df: pd.DataFrame, table: str, test: bool, **kwargs) -> bool:
    """Guarded oracle_export(). In test mode, logs a row-count + head preview
    and returns without touching the database. Returns True on success (or
    on a test-mode no-op), False on failure."""
    if test:
        log.info(
            "[TEST] Would export %d rows -> %s (no write performed). Preview:",
            len(df), table,
        )
        log.info("\n%s", df.head(10).to_string())
        return True
    try:
        try:
            oracle_export(df, table, **kwargs)
        except TypeError:
            oracle_export(df, table)
        return True
    except Exception as e:
        log.error("Export failed -> %s: %s", table, e)
        return False


# ─────────────────────────────────────────────────────────────────────────────
# Query construction — UNCHANGED from the version provided (do not edit SQL)
# ─────────────────────────────────────────────────────────────────────────────

def grant_select(granted_users: list, test: bool = False) -> bool:
    """Grant SELECT on INPUT_TABLE/OUTPUT_TABLE/LOG_TABLE to each user.
    Fully routed through safe_execute() — in --test mode this only logs
    what would be granted and never calls oracle_execute()."""
    success = True

    for u in granted_users:
        for table in (INPUT_TABLE, OUTPUT_TABLE, LOG_TABLE):
            ok = safe_execute(
                f"GRANT SELECT ON {table} TO {u}",
                test=test,
                preview_label=f"grant SELECT on {table} to {u}",
            )
            if not ok:
                success = False
    return success


def receivable_query(p_date):
    if not hasattr(p_date, "strftime"):
        raise ValueError("p_date must be a datetime")

    p_date_py = p_date

    # ── SQL literal ──
    if hasattr(p_date, "strftime"):
        p_date_sql = p_date.strftime("%Y-%m-%d")
    else:
        p_date_sql = str(p_date)
    p_date_literal = f"DATE '{p_date_sql}'"

    query = f"""
    select distinct p_date, loan_id from toki.leasing_receivable where p_date = {p_date_literal} and overdue_days = 7
    """
    return query


def format_query(p_date):
    if not hasattr(p_date, "strftime"):
        raise ValueError("p_date must be a datetime")

    p_date_py = p_date

    # ── SQL literal ──
    if hasattr(p_date, "strftime"):
        p_date_sql = p_date.strftime("%Y-%m-%d")
    else:
        p_date_sql = str(p_date)
    p_date_literal = f"DATE '{p_date_sql}'"
    p_curr_day = p_date_py.strftime("%d")
    p_yesterday_ym = (p_date_py - timedelta(days=1)).strftime("%Y%m")
    p_yesterday_day = (p_date_py - timedelta(days=1)).strftime("%d")

    p_curr_ym_l1 = (p_date_py - relativedelta(years=1)).strftime("%Y%m")

    p_curr_month = p_date_py.strftime("%Y%m")
    p_last_month = (p_date_py.replace(day=1) - timedelta(days=1)).strftime("%Y%m")
    p_last_2month = (
        (p_date_py.replace(day=1) - timedelta(days=1)).replace(day=1)
        - timedelta(days=1)
    ).strftime("%Y%m")
    p_last_3month = (
        (
            (p_date_py.replace(day=1) - timedelta(days=1)).replace(day=1)
            - timedelta(days=1)
        ).replace(day=1)
        - timedelta(days=1)
    ).strftime("%Y%m")

    logging.info(f"Formatted date for query: {p_date_literal}")
    logging.info(f"Current month: {p_curr_month}")
    logging.info(f"Last Month: {p_last_month}, {p_last_2month}, {p_last_3month}")

    query = f"""
    select your main query from loan table
    """
    return query


# ─────────────────────────────────────────────────────────────────────────────
# Export / idempotent writes — all Oracle mutation routed through
# safe_execute() / safe_export()
# ─────────────────────────────────────────────────────────────────────────────
def delete_existing_p_date(table: str, p_date: datetime, test: bool = False) -> None:
    """Delete any existing rows for this p_date so reruns don't duplicate data.
    In --test mode, safe_execute logs what would have been deleted instead
    of executing anything."""
    date_str = p_date.strftime("%Y-%m-%d")
    ok = safe_execute(
        f"DELETE FROM {table} WHERE p_date = TO_DATE('{date_str}', 'YYYY-MM-DD')",
        test=test,
        preview_label=f"delete existing p_date={date_str} from {table}",
    )
    if ok and not test:
        log.info("Cleared existing p_date=%s rows from %s (if any)", date_str, table)
    elif not ok:
        # Table may not exist yet on first run — non-fatal, already logged by safe_execute.
        pass


def export_query(p_date, df, TARGET_TABLE, MAX_TEXT_LEN=4000, test: bool = False):
    """In --test mode, builds the exact DataFrame that would be written
    (same p_date/inserted_at columns, same text truncation) and logs a
    preview instead of writing — no delete, no insert."""
    if df is None or df.empty:
        log.info(f"No rows for {p_date}")
        return False

    # prevent ORA-12899 on the current target table
    for col in df.columns:
        if df[col].dtype == object:
            df[col] = df[col].astype(str).str.slice(0, MAX_TEXT_LEN)

    df = df.copy()
    df["p_date"] = pd.to_datetime(p_date)
    cols = df.columns.tolist()
    cols.remove("p_date")
    df = df[["p_date"] + cols]
    df["inserted_at"] = pd.to_datetime("now")

    delete_existing_p_date(TARGET_TABLE, p_date, test=test)

    start = time.time()
    ok = safe_export(df, TARGET_TABLE, test=test, index=False, if_exists="append")

    if test:
        return ok

    if ok:
        log.info(
            json.dumps(
                {
                    "date": p_date.strftime("%Y-%m-%d"),
                    "type": "base",
                    "step": "inserting",
                    "duration": time.time() - start,
                }
            )
        )
    return ok


def write_log(log_row: dict, p_date: datetime, test: bool = False) -> None:
    """Write one summary row to LOG_TABLE. Never raises — logging failures
    should not crash or mask the pipeline's real outcome. In --test mode,
    no delete and no export happen; only a preview is logged."""
    try:
        log_df = pd.DataFrame([{c: log_row.get(c) for c in LOG_COLS}])

        delete_existing_p_date(LOG_TABLE, p_date, test=test)
        ok = safe_export(log_df, LOG_TABLE, test=test, index=False, if_exists="append")

        if not test and ok:
            log.info("Log row written -> %s", LOG_TABLE)
    except Exception as e:
        log.error("Failed to write log row to %s: %s", LOG_TABLE, e)


# ─────────────────────────────────────────────────────────────────────────────
# Load data back from INPUT_TABLE (source of truth for scoring)
# ─────────────────────────────────────────────────────────────────────────────
def read_data(p_date: datetime) -> pd.DataFrame:
    """Load scoring input for p_date directly from INPUT_TABLE. No local cache."""
    date_str = p_date.strftime("%Y-%m-%d")

    query = f"""
        SELECT *
        FROM {INPUT_TABLE}
        WHERE p_date = TO_DATE('{date_str}', 'YYYY-MM-DD')
    """
    log.info("Reading input data for p_date=%s from %s", date_str, INPUT_TABLE)
    df = oracle_import(query)
    df.columns = df.columns.str.lower()

    if df.empty:
        raise RuntimeError(f"No data found in {INPUT_TABLE} for p_date={date_str}")

    log.info("Loaded %d rows, %d columns", len(df), len(df.columns))
    return df


def get_receivable_loan_count(p_date: datetime) -> int:
    """Independent check against toki.leasing_receivable for the LOG_TABLE.
    Read-only — not gated by test, since it never mutates anything."""
    try:
        df_recv = oracle_import(receivable_query(p_date))
        df_recv.columns = df_recv.columns.str.lower()
        if df_recv.empty:
            return 0
        if "loan_id" in df_recv.columns:
            return int(df_recv["loan_id"].nunique())
        return len(df_recv)
    except Exception as e:
        log.error("Receivable query failed for %s: %s", p_date, e)
        return 0


def write_scores(score_df: pd.DataFrame, p_date: datetime, test: bool = False) -> None:
    """Write scores directly to Oracle. No local CSV backup.
    In --test mode, no delete and no export happen; only a preview is logged."""
    delete_existing_p_date(OUTPUT_TABLE, p_date, test=test)

    ok = safe_export(score_df, OUTPUT_TABLE, test=test, index=False, if_exists="append")

    if test:
        return

    if ok:
        log.info("Written %d rows -> %s", len(score_df), OUTPUT_TABLE)
    else:
        raise RuntimeError(f"Oracle write to {OUTPUT_TABLE} failed for p_date={p_date}")


# ─────────────────────────────────────────────────────────────────────────────
# Grants
# ─────────────────────────────────────────────────────────────────────────────
def grant_select(granted_users: list = None, test: bool = False) -> bool:
    """Grant SELECT on INPUT_TABLE/OUTPUT_TABLE/LOG_TABLE to each user.
    Fully routed through safe_execute() — in --test mode this only logs
    what would be granted and never calls oracle_execute()."""
    granted_users = granted_users if granted_users is not None else GRANTED_USERS
    success = True

    for u in granted_users:
        for table in (INPUT_TABLE, OUTPUT_TABLE, LOG_TABLE):
            ok = safe_execute(
                f"GRANT SELECT ON {table} TO {u}",
                test=test,
                preview_label=f"grant SELECT on {table} to {u}",
            )
            if not ok:
                success = False

    return success


# ─────────────────────────────────────────────────────────────────────────────
# Artifact management
# ─────────────────────────────────────────────────────────────────────────────
def load_artifacts(model_dir: Path = MODEL_DIR) -> dict:
    loaded = {}
    for name in ARTIFACT_NAMES[:-1]:  # all except features
        path = model_dir / f"{name}.pkl"
        if not path.exists():
            raise FileNotFoundError(f"Artifact '{name}' not found in '{model_dir}'. Expected: {path}")
        with open(path, "rb") as f:
            loaded[name] = pickle.load(f)
        log.debug("Loaded %s ← %s", name, path)

    loaded["features"] = pd.read_csv(model_dir / "features.csv").iloc[:, 0].tolist()
    log.info("All artifacts loaded '%s'", model_dir)
    return loaded


# ─────────────────────────────────────────────────────────────────────────────
# Feature preparation
# ─────────────────────────────────────────────────────────────────────────────
def prepare_features(df: pd.DataFrame, feature_list: list) -> tuple[pd.DataFrame, pd.DataFrame]:
    df_feat = df.copy()
    df_feat.rename(columns=FEATURE_RENAME_MAP, inplace=True)

    missing_meta = [c for c in META_COLS if c not in df_feat.columns]
    if missing_meta:
        log.warning("Missing meta columns: %s — will be skipped", missing_meta)

    missing_feats = [f for f in feature_list if f not in df_feat.columns]
    if missing_feats:
        raise ValueError(
            f"Feature mismatch — these features are in the model but not in input data:\n"
            f"  {missing_feats}"
        )

    df_meta = df_feat[[c for c in META_COLS if c in df_feat.columns]].copy()
    df_features = df_feat[feature_list].copy()
    for c in df_features.columns:
        if c == "app_trx_recency_l90d":
            df_features[c] = df_features[c].fillna(91)
        else:
            df_features[c] = df_features[c].fillna(0)

    df_features.replace("None", 0, inplace=True)
    df_features.replace([np.inf, -np.inf], 0, inplace=True)

    log.info(
        "Features prepared: %d rows x %d features | NaN filled with 0",
        len(df_features), len(feature_list),
    )
    return df_meta, df_features


# ─────────────────────────────────────────────────────────────────────────────
# Scoring
# ─────────────────────────────────────────────────────────────────────────────
def score(df: pd.DataFrame, artifacts: dict, p_date: datetime) -> pd.DataFrame:
    model = artifacts["model"]
    card = artifacts["card"]
    locked_bins = artifacts["bins"]
    kbd = artifacts["kbd"]
    feature_list = artifacts["features"]

    df_meta, df_feat = prepare_features(df, feature_list)

    df_feat_with_placeholder = df_feat.copy()
    df_feat_with_placeholder["bad"] = 0  # placeholder — woebin_ply needs target col present
    df_woe = sc.woebin_ply(df_feat_with_placeholder, locked_bins)

    locked_woe_cols = [f + "_woe" for f in feature_list]
    missing_woe = [c for c in locked_woe_cols if c not in df_woe.columns]
    if missing_woe:
        raise ValueError(f"WOE transform produced no columns for: {missing_woe}")

    X_score = df_woe[locked_woe_cols]

    score_df = sc.scorecard_ply(df_feat, card, print_step=0)

    score_df["bins"] = (
        10 - kbd.transform(score_df[["score"]]).astype(int).flatten()
    ).clip(1, 10)  # guard against edge values outside training range

    if not hasattr(model, "multi_class"):
        model.multi_class = "auto"
    score_df["probability"] = model.predict_proba(X_score)[:, 1].round(6)

    score_df = pd.concat(
        [df_meta.reset_index(drop=True), score_df.reset_index(drop=True)], axis=1
    )
    score_df["p_date"] = p_date
    score_df["scored_at"] = datetime.now().strftime("%Y-%m-%d %H:%M:%S")

    score_df["scored_at"] = datetime.now().strftime("%Y-%m-%d %H:%M:%S")

    control_idx = score_df.sample(
        frac=0.15,
        random_state=42
    ).index
    control_mask = score_df.index.isin(control_idx)

    score_df["is_control"] = None
    score_df.loc[control_mask, "is_control"] = "Y"
    score_df.loc[~control_mask, "is_control"] = "N"


    if not score_df.empty:
        log.info(
            "Scored %d rows | score range: [%d, %d] | mean prob: %.4f",
            len(score_df), score_df["score"].min(), score_df["score"].max(),
            score_df["probability"].mean(),
        )
    return score_df


# ─────────────────────────────────────────────────────────────────────────────
# Main pipeline
# ─────────────────────────────────────────────────────────────────────────────
def run_pipeline(p_date: datetime = None, test: bool = False, score_only: bool = False) -> None:
    """
    Run the daily scoring pipeline once for the provided date.
    If p_date is None, defaults to today — intended entry point for main.py:

        from score import run_pipeline
        run_pipeline()                              # today
        run_pipeline(date(2026,7,5))                 # backfill
        run_pipeline(test=True)                      # dry run, no writes, preview output
        run_pipeline(score_only=True)                # skip query/export, read INPUT_TABLE directly

    test:       when True, every Oracle-mutating call (DELETE, INSERT/export,
                GRANT) is routed through safe_execute()/safe_export(), which
                log a preview instead of touching the database. Nothing
                about the scoring logic itself changes.
    score_only: when True, steps 1+2 (build/run the extraction query, export
                into INPUT_TABLE) are skipped entirely. Assumes INPUT_TABLE
                is already populated for p_date and jumps straight to step 3
                (read_data) onward. Useful for re-scoring after fixing a bug
                in score()/artifacts without re-running the Oracle extraction
                query.
    """
    if p_date is None:
        p_date = pd.Timestamp.now().date()

    log.info("=" * 60)
    log.info(
        "Daily pipeline starting | p_date=%s | test=%s | score_only=%s",
        p_date, test, score_only,
    )
    log.info("=" * 60)

    stats = {
        "p_date": p_date,
        "queried_loan_cnt": None,
        "scored_loan_cnt": None,
        "receivable_loan_cnt": None,
        "query_startedat": None,
        "query_completed": None,
        "query_insertedat": None,
        "scoredat": None,
        "started_at": datetime.now(),
        "completed_at": None,
        "status": "started",
        "error_message": None,
    }

    try:
        # ── Independent receivable check (doesn't block main flow) ──────
        stats["receivable_loan_cnt"] = get_receivable_loan_count(p_date)

        if not score_only:
            # ── Step 1+2: build query, run it, export into INPUT_TABLE ──
            query = format_query(p_date)
            stats["query_startedat"] = datetime.now()
            try:
                start = time.time()
                log.info("Importing query for %s...", p_date)
                df_raw = oracle_import(query)

                cols = {c.lower(): c for c in df_raw.columns}

                if 'p_date' in cols and 'loanid' in cols:
                    df_raw = df_raw.drop_duplicates(
                        subset=[cols['p_date'], cols['loanid']]
                    )

                log.info(
                    "Query imported for %s in %.2fs. Rows: %d",
                    p_date, time.time() - start, len(df_raw),
                )
                stats["query_completed"] = datetime.now()
            except Exception as exc:
                log.error("Query execution failed for %s: %s", p_date, exc)
                stats["status"] = "query_failed"
                stats["error_message"] = str(exc)
                return

            if df_raw.empty:
                log.warning("No rows returned from source query for p_date=%s — aborting", p_date)
                stats["status"] = "no_data"
                stats["queried_loan_cnt"] = 0
                return

            df_raw.columns = df_raw.columns.str.lower()

            stats["queried_loan_cnt"] = (
                int(df_raw["loanid"].nunique()) if "loanid" in df_raw.columns else len(df_raw)
            )

            if export_query(p_date, df_raw, TARGET_TABLE=INPUT_TABLE, test=test):
                log.info("Export to %s successful for %s", INPUT_TABLE, p_date)
                stats["query_insertedat"] = datetime.now()
            else:
                log.error("Export to %s failed for %s — aborting before scoring", INPUT_TABLE, p_date)
                stats["status"] = "export_failed"
                return
        else:
            log.info(
                "--score: skipping query/export step, reading directly from %s for p_date=%s",
                INPUT_TABLE, p_date,
            )

        # ── Step 3: reload from INPUT_TABLE (source of truth, not cache) ──
        df = read_data(p_date)
        df.drop_duplicates(inplace=True)

        if df.empty:
            log.warning("No rows found for p_date=%s after reload — nothing to score", p_date)
            stats["status"] = "no_data_after_reload"
            return
        log.info("%d rows to score", len(df))

        if score_only:
            # queried_loan_cnt wasn't set above since step 1+2 was skipped —
            # backfill it from what was actually read for an accurate log row.
            stats["queried_loan_cnt"] = (
                int(df["loanid"].nunique()) if "loanid" in df.columns else len(df)
            )

        # ── Step 4+5: features, artifacts, scoring ──────────────────────
        artifacts = load_artifacts()
        scored_df = score(df, artifacts, p_date)
        scored_df.drop_duplicates(inplace=True)
        stats["scoredat"] = datetime.now()

        if scored_df.empty:
            log.warning("Scoring produced 0 rows for p_date=%s — nothing to write", p_date)
            stats["status"] = "scoring_empty"
            return

        stats["scored_loan_cnt"] = (
            int(scored_df["loanid"].nunique()) if "loanid" in scored_df.columns else len(scored_df)
        )

        # ── Step 6: write to OUTPUT_TABLE ────────────────────────────────
        write_scores(scored_df, p_date, test=test)

        # ── Step 7: grant to GRANTED_USERS ────────────────────────────────
        grant_select(granted_users=GRANTED_USERS, test=test)

        stats["status"] = "success"

        log.info("=" * 60)
        log.info("Pipeline complete | p_date=%s | test=%s | score_only=%s", p_date, test, score_only)
        log.info("  Total rows scored : %d", len(scored_df))
        log.info("  Score range       : [%d, %d]", scored_df["score"].min(), scored_df["score"].max())
        log.info("  Mean probability  : %.4f", scored_df["probability"].mean())
        log.info("=" * 60)

    except Exception as exc:
        log.exception("Pipeline failed with an unhandled error for p_date=%s", p_date)
        stats["status"] = "error"
        stats["error_message"] = str(exc)
        raise

    finally:
        stats["completed_at"] = datetime.now()
        write_log(stats, p_date, test=test)


# ─────────────────────────────────────────────────────────────────────────────
# Entry point (manual / ad-hoc runs — main.py should import run_pipeline directly)
# ─────────────────────────────────────────────────────────────────────────────

def parse_args():
    parser = argparse.ArgumentParser(
        description="Lease Collection Scorecard — Daily Scoring Pipeline"
    )
    parser.add_argument(
        "--p_date",
        type=str,
        default=None,
        help="Scoring date in YYYY-MM-DD format (default: today)",
    )
    parser.add_argument(
        "--test",
        action="store_true",
        help="Dry run — no writes to INPUT_TABLE/OUTPUT_TABLE/LOG_TABLE and no GRANTs, "
             "just log a preview of what would be written/executed",
    )
    parser.add_argument(
        "--score",
        action="store_true",
        dest="score_only",
        help="Skip querying Oracle and exporting to INPUT_TABLE; read directly from INPUT_TABLE "
             "(must already be populated for --p_date) and score",
    )
    return parser.parse_args()


if __name__ == "__main__":
    args = parse_args()
    log.info("Oracle user (env): %s", os.environ.get("ORACLE_USER"))
    if args.p_date:
        p_date = datetime.strptime(args.p_date, "%Y-%m-%d").date()
    else:
        p_date = pd.Timestamp.now().date()

    run_pipeline(p_date=p_date, test=args.test, score_only=args.score_only)
