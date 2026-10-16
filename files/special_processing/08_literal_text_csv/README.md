# 08 — Reviewed CSV text

**Use when:** A registered text log contains unescaped commas or unmatched quotes.

**Rule:** Use the reviewed parser for the listed log IDs only. Preserve text, rows and depth fields; retain hashes and full sample-axis checks.

## Run

First [run normal preparation and diagnose its failures](../README.md).
Use this handler only for holes assigned to `08_literal_text_csv`:

```powershell
python special_processing/08_literal_text_csv/apply_fix.py --diagnosis "D:/etl_check/diagnosis/diagnosis.json" --work-dir "D:/etl_check/08"
```

Processes the assigned group; add `--holes "HOLE_ID"` for a subset. Other rules
diagnosed for the same hole are applied in the same run.

Runs steps 1–6. Continue at step 7 after success.
Read `special_processing_result.json` for output paths and `failed_holes.json`
for remaining failures. Raw data and the repository's `etl4/` stay unchanged.
