# 06 — Float32 text precision

**Use when:** Text representations differ beyond the old decimal tolerance.

**Rule:** After the decimal check fails, require exactly equal finite float32 values. Preserve original values; do not widen the tolerance for genuine differences.

## Run

First [run normal preparation and diagnose its failures](../README.md).
Use this handler only for holes assigned to `06_float32_precision`:

```powershell
python special_processing/06_float32_precision/apply_fix.py --diagnosis "D:/etl_check/diagnosis/diagnosis.json" --work-dir "D:/etl_check/06"
```

Processes the assigned group; add `--holes "HOLE_ID"` for a subset. Other rules
diagnosed for the same hole are applied in the same run.

Runs steps 1–6. Continue at step 7 after success.
Read `special_processing_result.json` for output paths and `failed_holes.json`
for remaining failures. Raw data and the repository's `etl4/` stay unchanged.
