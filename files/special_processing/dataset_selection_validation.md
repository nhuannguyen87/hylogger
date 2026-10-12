# Earlier dataset input handler checks

Historical check before automatic coordinate handling. Its manual-collar
requirement has been superseded by the owner's shared-coordinate-system decision.

Date: 2026-10-11. Scope: external cases 12 and 13, failure diagnosis and input preparation.

## Result

- 25 automated tests passed: 16 input-selection tests and 9 existing routing tests.
- All 65 files in the original `etl4/` match their pre-change SHA256 values.
- All external Python files parse successfully and contain no Chinese text.
- Cached official membership was checked for nine core/cuttings groups and five
  same-name groups. All fourteen matched the local dataset metadata.

The new tests cover dataset isolation, exclusion without deleting raw files,
unchanged dataset IDs, distinct borehole identities, missing/incorrect collars,
image misassociation, missing retained files, same-size content corruption,
changed metadata, path escape, existing-output protection and failure-only routing.

## Real input check: Tamala 1

The original entry rejected the source name. Diagnosis selected case 12.
The input handler retained dataset `01cfc34a-bd8c-4520-afff-3ab8682ef70`
and excluded these two datasets with reason `out_of_scope_cuttings`:

- `9012e9c6-f584-4b90-8370-dd55f48906b` — Tamala 1_cuttings
- `b3c4bd42-a5af-4af3-9459-2d1c777af0a` — Tamala 1_cuttings2

It copied and hashed 92 retained payloads, totalling 43,043,484 bytes.
The new input then used the existing cases 01 and 06 in a separate code copy.
Steps 1-6 passed, including content validation, metadata normalization,
sample alignment, columnar output and indexed-query checks. Step 6 verified
464,364 scalar rows and 81 Parquet files. This is preparation, not database import.

## Split checks and remaining inputs

Synthetic packages verified that two same-name datasets produce separate
borehole inputs, keep their dataset IDs and never inherit the shared collar.
A valid output can proceed while another output is listed under `pending_outputs`.

For real BH01, the original entry rejected its multiple datasets. Diagnosis
selected case 13. With no reviewed collars supplied, both output holes correctly
stopped with `needs_collar`; no split input was published.

The ten approved names match the source projects in BBDD0001, BBDD0002, BH01,
BH02 and BNDD001. Their real split-and-prepare runs remain pending individual
reviewed collar records. No coordinate datum was guessed.

## Reproduce the automated checks

From an external work directory, with the handoff dependencies installed:

```powershell
python -B -m unittest discover -s "PATH/TO/HANDOFF/special_processing/tests" -p "test_*.py" -v
```

The local run reports are in `dataset_selection_validation/` in the coding
workspace. Raw source files, databases and AWS were not changed.
