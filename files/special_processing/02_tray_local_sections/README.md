# 02 — Sections numbered within each tray

**Use when:** virtual_section restarts at 1 for each tray.

**Rule:** Compare every value with tray-local numbering. Preserve source values and require complete intervals and position logs.

## Run

First [run normal preparation and diagnose its failures](../README.md).
Use this handler only for holes assigned to `02_tray_local_sections`:

```powershell
python special_processing/02_tray_local_sections/apply_fix.py --diagnosis "D:/etl_check/diagnosis/diagnosis.json" --work-dir "D:/etl_check/02"
```

Processes the assigned group; add `--holes "HOLE_ID"` for a subset. Other rules
diagnosed for the same hole are applied in the same run.

Runs steps 1–6. Continue at step 7 after success.
Read `special_processing_result.json` for output paths and `failed_holes.json`
for remaining failures. Raw data and the repository's `etl4/` stay unchanged.
