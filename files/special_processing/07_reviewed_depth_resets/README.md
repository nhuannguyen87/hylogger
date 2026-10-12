# 07 — Reviewed depth resets

**Use when:** Depth moves backwards at a previously reviewed tray boundary.

**Rule:** Allow only the registered dataset IDs and sample positions, at both tray and section starts. Preserve source order; reject new or misplaced resets.

## Run

First [run normal preparation and diagnose its failures](../README.md).
Use this handler only for holes assigned to `07_reviewed_depth_resets`:

```powershell
python special_processing/07_reviewed_depth_resets/apply_fix.py --diagnosis "D:/etl_check/diagnosis/diagnosis.json" --work-dir "D:/etl_check/07"
```

Processes the assigned group; add `--holes "HOLE_ID"` for a subset. Other rules
diagnosed for the same hole are applied in the same run.

Runs steps 1–6. Continue at step 7 after success.
Read `special_processing_result.json` for output paths and `failed_holes.json`
for remaining failures. Raw data and the repository's `etl4/` stay unchanged.
