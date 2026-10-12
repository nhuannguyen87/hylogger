# 11 — Ambiguous profile membership

**Use when:** Multiple datasets have equal sample counts, or step 31 reads the wrong response collection.

**Rule:** Read the correct official collection and compare every profile minimum with dataset-specific prof_min values. Require unique membership; do not infer it from counts or join dataset depth axes.

**Project scope:** [exclude cuttings and split reviewed same-name boreholes](../../docs/dataset_selection.md)
before preparing a delivery. This handler resolves file membership; it does not
implement dataset exclusion or borehole renaming. Earlier Tamala cuttings runs
remain historical tests.

## Run

First [run normal preparation and diagnose its failures](../README.md).
Use this handler only for holes assigned to `11_multi_dataset_profile`:

```powershell
python special_processing/11_multi_dataset_profile/apply_fix.py --diagnosis "D:/etl_check/diagnosis/diagnosis.json" --work-dir "D:/etl_check/11"
```

Processes the assigned group; add `--holes "HOLE_ID"` for a subset. Other rules
diagnosed for the same hole are applied in the same run.

Runs steps 31–32. Supply `--evidence-dir` if the original failure directory
does not contain the official response cache. Continue at step 33 after success.
Read `special_processing_result.json` for output paths and `failed_holes.json`
for remaining failures. Raw data and the repository's `etl4/` stay unchanged.
