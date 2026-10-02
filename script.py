import sys

from loguru import logger
import numpy as np
import pandas as pd
import scorecardpy as sc
from dateutil.relativedelta import relativedelta
import os
from src.module.settings import settings
from src.score import run_pipeline
import argparse
import json
import logging
import pickle
import sys
import time
from datetime import datetime, timedelta
from pathlib import Path


logger.remove()
logger.add(sys.stderr, colorize=True, level=settings.log_level)

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
        help="Dry run — no writes to INPUT_TABLE/OUTPUT_TABLE/LOG_TABLE, just log a preview of what would be written",
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
    logging.info(f"Oracle User: {os.environ.get('ORACLE_USER')}")
    if args.p_date:
        p_date = datetime.strptime(args.p_date, "%Y-%m-%d").date()
    else:
        p_date = pd.Timestamp.now().date()

    run_pipeline(p_date=p_date, test=args.test, score_only=args.score_only)
