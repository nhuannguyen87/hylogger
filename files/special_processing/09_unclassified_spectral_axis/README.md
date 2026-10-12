# 09 — Unclassified wavelength axes

**Use when:** A source wavelength axis does not fit a preset region.

**Rule:** Use UNCLASSIFIED and preserve the actual coordinates and matrices. Spectral size, order and sample binding still require later media checks.

## Run

First [run normal preparation and diagnose its failures](../README.md).
Use this handler only for holes assigned to `09_unclassified_spectral_axis`:

```powershell
python special_processing/09_unclassified_spectral_axis/apply_fix.py --diagnosis "D:/etl_check/diagnosis/diagnosis.json" --work-dir "D:/etl_check/09"
```

Processes the assigned group; add `--holes "HOLE_ID"` for a subset. Other rules
diagnosed for the same hole are applied in the same run.

Runs steps 1–6. Continue at step 7 after success.
Read `special_processing_result.json` for output paths and `failed_holes.json`
for remaining failures. Raw data and the repository's `etl4/` stay unchanged.
