# 05 — Null auxiliary values

**Use when:** Some virtual_section or prof_min values are null.

**Rule:** Preserve nulls and compare every available value. Require complete sample positions; reject infinity, wrong values and entirely absent comparison evidence.

## Run

First [run normal preparation and diagnose its failures](../README.md).
Use this handler only for holes assigned to `05_nullable_auxiliary_values`:

```powershell
python special_processing/05_nullable_auxiliary_values/apply_fix.py --diagnosis "D:/etl_check/diagnosis/diagnosis.json" --work-dir "D:/etl_check/05"
```

Processes the assigned group; add `--holes "HOLE_ID"` for a subset. Other rules
diagnosed for the same hole are applied in the same run.

Runs steps 1–6. Continue at step 7 after success.
Read `special_processing_result.json` for output paths and `failed_holes.json`
for remaining failures. Raw data and the repository's `etl4/` stay unchanged.
