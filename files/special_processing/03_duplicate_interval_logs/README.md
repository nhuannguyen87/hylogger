# 03 — Duplicate interval logs

**Use when:** Several log IDs describe the same Tray or Section intervals.

**Rule:** Accept aliases only when their content matches the dataset-declared log. Keep all source IDs and files.

## Run

First [run normal preparation and diagnose its failures](../README.md).
Use this handler only for holes assigned to `03_duplicate_interval_logs`:

```powershell
python special_processing/03_duplicate_interval_logs/apply_fix.py --diagnosis "D:/etl_check/diagnosis/diagnosis.json" --work-dir "D:/etl_check/03"
```

Processes the assigned group; add `--holes "HOLE_ID"` for a subset. Other rules
diagnosed for the same hole are applied in the same run.

Runs steps 1–6. Continue at step 7 after success.
Read `special_processing_result.json` for output paths and `failed_holes.json`
for remaining failures. Raw data and the repository's `etl4/` stay unchanged.
