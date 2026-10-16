# 10 — Explicitly unavailable profiles

**Use when:** The source explicitly declares that no profile payload is available.

**Rule:** Retain metadata without inventing a file. Require consistent unavailable status, zero bytes and records, no hash and no contradictory payload.

## Run

First [run normal preparation and diagnose its failures](../README.md).
Use this handler only for holes assigned to `10_unavailable_profile`:

```powershell
python special_processing/10_unavailable_profile/apply_fix.py --diagnosis "D:/etl_check/diagnosis/diagnosis.json" --work-dir "D:/etl_check/10"
```

Processes the assigned group; add `--holes "HOLE_ID"` for a subset. Other rules
diagnosed for the same hole are applied in the same run.

Runs steps 1–6. Continue at step 7 after success.
Read `special_processing_result.json` for output paths and `failed_holes.json`
for remaining failures. Raw data and the repository's `etl4/` stay unchanged.
