# Failure workflow validation

Recorded on 2026-10-11, before the English-only documentation update.
These are preparation results, not database or AWS delivery results.

## Failed-hole diagnosis

From 153 historical normal-run records, 139 failed holes were diagnosed and
14 multi-dataset route records were excluded. The 139 holes were assigned once:

| Handler | Assigned holes |
|---|---:|
| 01 Source identity | 93 |
| 02 Tray-local sections | 18 |
| 05 Null auxiliary values | 1 |
| 06 Float32 precision | 26 |
| 08 Literal CSV text | 1 |

Issue counts overlap: 01 occurs in 139 holes, 02 in 18, 05 in 1, 06 in 32, and
08 in 3. Assignment chooses one entry point; that entry applies all diagnosed
rules for the hole. See the [assigned-hole list](failed_hole_routes.md).
This was a diagnosis of 139 holes, not a full rerun of all 139.

## Real preparation runs

Each hole first failed in the unchanged normal pipeline, then passed diagnosis
and its assigned handler. Tamala 1 used an existing official response cache.

| Hole | Rules | Passed steps | Samples | Step time, seconds |
|---|---|---|---:|---:|
| Beekeeper 1 | 01, 02, 06 | 1–6 | 3,024 | 10.984 |
| BM5346 | 01, 05, 08 | 1–6 | 10,800 | 27.375 |
| Tamala 1 | 01, 11 | 31–32 | 7,390 | 19.141 |

All three remaining-failure lists were empty. Tamala retained three independent
datasets with 6,030, 680 and 680 samples. Times sum the recorded subprocess
durations; they exclude diagnosis and copy setup and are not speedup claims.

The two single-dataset step 6 reports were `passed_with_known_limitations`.
Later spectral binding, media preparation, package acceptance and import were
outside this test. Previously deferred holes remained deferred.

## Tests

- Nine routing tests passed: failed-only selection, unknown and environment
  errors, incomplete batches, duplicate records, wrong groups, changed source
  metadata, empty lists and repeated failure with the same rules.
- Five synthetic workflow tests passed: identical interval aliases and images
  reached step 6; different intervals, different image bytes and cross-dataset
  images stayed pending. Successful fixtures produced no further failure group.
- Eleven individual adapter builds and one combined build passed. Three finite
  float32 boundary probes passed: equal, different and overflowing values.
- The original 59 Python/SQL files under `etl4/` matched their recorded hashes.

Raw data, databases and AWS were not changed. Exact steps and timings are in
[failure_workflow_summary.json](results/failure_workflow_summary.json).
See [earlier algorithm tests](validation_report.md) for the wider historical scope.

## Repeat the lightweight checks

```powershell
python special_processing/tests/test_failure_routing.py
python special_processing/tests/check_builds.py --work-dir "D:/etl_check/build_tests"
```

Use a new directory. The build check creates temporary code copies only.
