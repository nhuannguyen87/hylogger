# 01 — Source names and identity

**Use when:** Spaces, encoded directory names, or a new source_identity.json file.

**Rule:** Check directory, borehole and package identities; reject unsafe paths and conflicting IDs.

## Run

First [run normal preparation and diagnose its failures](../README.md).
Use this handler only for holes assigned to `01_source_identity`:

```powershell
python special_processing/01_source_identity/apply_fix.py --diagnosis "D:/etl_check/diagnosis/diagnosis.json" --work-dir "D:/etl_check/01"
```

Processes the assigned group; add `--holes "HOLE_ID"` for a subset. Other rules
diagnosed for the same hole are applied in the same run.

Runs steps 1–6. Continue at step 7 after success.
Read `special_processing_result.json` for output paths and `failed_holes.json`
for remaining failures. Raw data and the repository's `etl4/` stay unchanged.
