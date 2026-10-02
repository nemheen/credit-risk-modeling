# Lease Collection Scorecard

Production scoring pipeline for daily lease-collection risk scoring.

The pipeline extracts eligible lease borrowers from Oracle, prepares features using locked training artifacts, applies the WOE-based scorecard, generates a risk score and probability, and writes the results back to Oracle.

---

## Architecture

```text
                    Oracle
                       │
                       │ Feature SQL
                       ▼
              ┌──────────────────┐
              │  Source Tables   │
              └────────┬─────────┘
                       │
                       ▼
              ┌──────────────────┐
              │   INPUT_TABLE    │
              │  Daily Features  │
              └────────┬─────────┘
                       │
                       ▼
              ┌──────────────────┐
              │ Feature Prepare  │
              │  - rename        │
              │  - missing value │
              │  - validation    │
              └────────┬─────────┘
                       │
                       ▼
              ┌──────────────────┐
              │ Locked Artifacts │
              │  WOE / Scorecard │
              │  Model / Bins    │
              └────────┬─────────┘
                       │
                       ▼
              ┌──────────────────┐
              │     Scoring      │
              │ WOE → Scorecard  │
              │ → Probability    │
              └────────┬─────────┘
                       │
                       ▼
              ┌──────────────────┐
              │  OUTPUT_TABLE    │
              │ Scores / Risk    │
              └────────┬─────────┘
                       │
                       ▼
              ┌──────────────────┐
              │    LOG_TABLE     │
              │ Pipeline Summary │
              └──────────────────┘
```

---

# Docker Deployment

The project is deployed as a Docker image on the production server.

The production workflow is:

```text
GitHub
   │
   │ Push version tag
   ▼
GitHub Actions
   │
   │ Build
   ▼
Production Server
   │
   │ Docker image
   ▼
lease_collection_score:vX.X.X
   │
   ▼
Scheduled execution
   │
   ▼
Oracle
```

## Create a Release

A new production Docker image is built automatically when a version tag is pushed to the remote repository.

For example:

```bash
git tag v0.1.0
git push origin v0.1.0
```

The tag becomes the Docker image version:

```text
lease_collection_score:v0.1.0
```

Use a new tag for every production release:

```bash
git tag v0.1.1
git push origin v0.1.1
```

Avoid reusing an existing production tag.

---

## Test Dockerfile

The development container can access the host server's Docker installation.

To check available Docker images:

```bash
docker images
```

To verify that Docker is accessible:

```bash
docker version
```

This allows the Docker image to be tested from the development environment before deployment.

---

## Run Docker Image

### 1. Create the environment file

Create:

```text
$HOME/envs/lease_collection_score.env
```

The file should contain the required Oracle configuration, for example:

```text
ORACLE_USER=...
ORACLE_PASSWORD=...
ORACLE_DSN=...
```

Use the project's `.env.template` as the reference for required variables.

**Do not commit this file to Git.**

### 2. Run the image

For release `v0.1.0`:

```bash
docker run --rm \
  --env-file=$HOME/envs/lease_collection_score.env \
  lease_collection_score:v0.1.0
```

`--rm` removes the container after the process finishes. The model artifacts are packaged with the Docker image, while database credentials are provided through the environment file.

---

## Production Scheduling

The Docker image is intended to be executed by a scheduled job on the production server.

Example:

```bash
docker run --rm \
  --env-file=$HOME/envs/lease_collection_score.env \
  lease_collection_score:v0.1.0
```

The scheduler should reference a specific image version rather than an unversioned image such as `latest`.

This makes the production version explicit and allows an older release to be executed if rollback is required.

---

# Scoring Pipeline

For each scoring date (`p_date`), the pipeline performs:

1. Build the feature-extraction SQL.
2. Execute the query against Oracle.
3. Export the extracted data into `INPUT_TABLE`.
4. Reload data from `INPUT_TABLE` as the scoring source of truth.
5. Validate and prepare model features.
6. Load locked model artifacts.
7. Apply the WOE transformation.
8. Generate the scorecard score.
9. Convert the score to a score bin.
10. Generate the predicted probability.
11. Assign a 15% control group using a fixed random seed.
12. Write scores to `OUTPUT_TABLE`.
13. Grant `SELECT` access to configured users.
14. Write a run summary to `LOG_TABLE`.

The pipeline does not use local runtime caching. Model artifacts under `src/models/` are read-only training artifacts.

---

## Repository Structure

```text
lease_collection_score/
│
├── .github/
├── .devcontainer/
│
├── data/
├── notebooks/
├── references/
├── reports/
│
├── src/
│   ├── models/              # Locked model artifacts
│   │   ├── model.pkl
│   │   ├── card.pkl
│   │   ├── bins.pkl
│   │   ├── kbd.pkl
│   │   └── features.csv
│   │
│   └── module/
│       ├── database.py       # Oracle connection/query utilities
│       └── settings.py       # Environment/configuration
│
├── script.py                 # Main scoring entry point
├── Dockerfile
├── environment.yml
├── requirements.txt
├── pyproject.toml
├── .env.template
├── .gitignore
└── README.md
```

---

# Scoring

The production scoring process uses locked training artifacts:

```text
Raw Features
     │
     ▼
Feature Preparation
     │
     ▼
WOE Transformation
     │
     ▼
Scorecard
     │
     ├──────────────► Score
     │
     ▼
Score Binning
     │
     ▼
Probability
```

The WOE transformation uses the previously trained and locked bins. The scorecard is then applied using the locked scorecard artifact, followed by probability prediction from the trained model.

### Missing Values

During feature preparation:

* `app_trx_recency_l90d` → missing values filled with `91`
* Other missing numeric features → `0`
* `"None"` → `0`
* `±inf` → `0`

The production feature list is loaded from `src/models/features.csv`, and the pipeline validates that every required model feature exists in the input data.

---

## Control Group

A random 15% control group is assigned during scoring:

```python
score_df.sample(
    frac=0.15,
    random_state=42
)
```

The resulting column is:

```text
is_control
├── Y → Control group
└── N → Non-control group
```

The fixed `random_state=42` makes the assignment reproducible for the same input ordering.

---

# Running the Pipeline

The pipeline can be run directly with Python for local testing or debugging.

### Daily scoring

```bash
python script.py
```

### Score a specific date

```bash
python script.py --p_date 2026-07-05
```

### Dry run

Use `--test` to validate the pipeline without modifying Oracle tables:

```bash
python script.py --test
```

or:

```bash
python script.py --p_date 2026-07-05 --test
```

In test mode, Oracle-mutating operations such as `DELETE`, `INSERT/export`, and `GRANT` are replaced with logged previews. The scoring logic itself remains unchanged.

### Score-only mode

Use `--score` when the input table has already been populated:

```bash
python script.py --p_date 2026-07-05 --score
```

This skips:

```text
Feature SQL
    ↓
Oracle query
    ↓
INPUT_TABLE export
```

and starts directly from:

```text
INPUT_TABLE
    ↓
Feature preparation
    ↓
Scoring
    ↓
OUTPUT_TABLE
```

This is useful when re-scoring after fixing scoring code or replacing model artifacts without repeating the potentially expensive feature extraction query.

### Dry-run + score-only

The two modes can be combined:

```bash
python script.py --p_date 2026-07-05 --score --test
```

This reads existing data from `INPUT_TABLE`, performs scoring, and previews the writes without modifying Oracle.

---

# Python API

The pipeline can also be imported directly:

```python
from score import run_pipeline

# Score today
run_pipeline()

# Backfill a specific date
run_pipeline(date(2026, 7, 5))

# Dry run
run_pipeline(test=True)

# Re-score existing INPUT_TABLE data
run_pipeline(score_only=True)
```

`run_pipeline()` is the main production entry point.

---

# Idempotency

The pipeline deletes existing records for the requested `p_date` before inserting new results.

This prevents duplicate records when a date is reprocessed.

```text
Existing p_date
      │
      ▼
   DELETE
      │
      ▼
   INSERT
      │
      ▼
 Latest result
```

The same pattern is used for the input, output, and log tables.

---

# Logging

Logs are written to:

```text
stdout
daily_pipeline.log
```

Each run records information such as:

* scoring date
* number of queried loans
* number of scored loans
* receivable loan count
* query timestamps
* scoring timestamp
* completion timestamp
* pipeline status
* error message when applicable

A summary row is written to `LOG_TABLE` even when the pipeline fails.

---

# Model Artifacts

The production model depends on the following locked artifacts:

```text
src/models/
├── model.pkl
├── card.pkl
├── bins.pkl
├── kbd.pkl
└── features.csv
```

The pipeline loads the serialized model, scorecard, WOE bins, score-bin transformer, and feature list at runtime.

These artifacts should be treated as **versioned production dependencies**. Changing them can change production scores even when the scoring code itself is unchanged.

---

# Configuration

Create a local environment file based on:

```text
.env.template
```

For production Docker execution, use:

```text
$HOME/envs/lease_collection_score.env
```

Do **not** commit production credentials or environment files to Git.

---

# Requirements

Install dependencies with:

```bash
pip install -r requirements.txt
```

or use the provided Conda environment:

```bash
conda env create -f environment.yml
```

The pipeline uses, among others:

* Python
* pandas
* NumPy
* scikit-learn
* scorecardpy
* oracledb
* python-dotenv

---

# Safety

Before running against production Oracle:

1. Verify the environment variables.
2. Verify `INPUT_TABLE`, `OUTPUT_TABLE`, and `LOG_TABLE`.
3. Verify model artifacts under `src/models/`.
4. Run with `--test`.
5. Check the generated logs and preview.
6. Run the production pipeline only after validation.

`--test` is specifically designed to prevent Oracle-mutating operations while still exercising the scoring flow.

---

# Versioning

Production releases are versioned using Git tags.

Example:

```bash
git add .
git commit -m "Fix score bin assignment"

git push

git tag v0.1.0
git push origin v0.1.0
```

The release tag triggers the production Docker image build.

Keep model artifacts and corresponding code versions traceable so that historical scoring results can be reproduced.

---

# Development

When making changes:

1. Develop and test locally.
2. Run the pipeline with `--test`.
3. Test the Docker image.
4. Commit and push changes.
5. Create a new version tag.
6. Verify that the production image was built.
7. Run the new image on the production server.

For example:

```bash
python script.py --p_date 2026-07-05 --test

git add .
git commit -m "Update scoring pipeline"
git push

git tag v0.1.0
git push origin v0.1.0
```

Do not modify an existing release tag. Create a new version for each production change.
