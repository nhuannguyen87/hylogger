# 04 — Duplicate image logs

**Use when:** Several logs provide the same tray images.

**Rule:** Compare every image hash, ordinal, depth bound and dataset association. Create one display sequence only when all match.

## Run

First [run normal preparation and diagnose its failures](../README.md).
Use this handler only for holes assigned to `04_duplicate_image_logs`:

```powershell
python special_processing/04_duplicate_image_logs/apply_fix.py --diagnosis "D:/etl_check/diagnosis/diagnosis.json" --work-dir "D:/etl_check/04"
```

Processes the assigned group; add `--holes "HOLE_ID"` for a subset. Other rules
diagnosed for the same hole are applied in the same run.

Runs steps 1–6. Continue at step 7 after success.
Read `special_processing_result.json` for output paths and `failed_holes.json`
for remaining failures. Raw data and the repository's `etl4/` stay unchanged.
