# Earlier algorithm validation

Historical record, 2026-10-11. For current commands, use the [README](README.md).
For failure-list routing, see [workflow validation](failure_workflow_validation.md).

## Normal pipeline results

The raw directory contained 153 holes. All 139 single-dataset holes first failed
at step 1 because of directory syntax or the unregistered `source_identity.json`.
The 14 multi-dataset holes were recorded as `requires_multi_route`, not corrupt
data. [Original run records](results/baseline_results.json) capture only the first
failure per hole; later errors could still exist.

Source inspection found overlapping cases: 01 in 139 holes, 02 in 18, 05 in 1,
06 in 32, and 08 in 3. See [historical routes](current_hole_routes.md) and
[inspection evidence](results/current_raw_classification.json).

## Real-hole preparation

| Hole | Failure after applying only case 01 | Combined rules result |
|---|---|---|
| Beekeeper 1 | Step 4: tray-local numbering | Steps 1–6 passed; 3,024 samples |
| BM5346 | Step 2: literal CSV text; also null auxiliary values | Steps 1–6 passed; 10,800 samples |
| ARMCDD001 | Step 2: literal CSV text | Steps 1–6 passed; 68,162 samples |
| BB1 | Control passed steps 1–4 | Steps 1–6 passed; 536 samples |

These four runs used a copy with all adapters to check their compatibility.
They were not a full rerun of all 139 holes. Tamala 1 separately used cases 01
and 11 with cached official evidence and passed steps 31–32: 6,030 + 680 + 680
samples. Step 33, database import and upload were not rerun in this test.

## Synthetic cases

Independent BB1 copies exercised 11 cases; all matched their expected outcomes:

| Input | Expected outcome |
|---|---|
| Identical interval aliases | Baseline rejects; case 03 passes through step 6 |
| Different intervals with the same name | Case 03 rejects at step 3 |
| Identical images and source associations | Baseline rejects; case 04 passes through step 6 |
| Different image bytes | Case 04 rejects at step 4 |
| Image belongs to another dataset | Case 04 rejects at step 4 |
| Partly null virtual_section, correct remaining values | Case 05 passes through step 6 |
| Incorrect non-null virtual_section | Case 05 rejects at step 4 |
| Explicit unavailable profile without payload | Case 10 passes through step 6 |
| Unavailable profile contradicts an existing payload | Case 10 rejects at step 1 |
| prof_min text differs but finite float32 values match | Case 06 passes through step 6 |
| prof_min values genuinely differ | Case 06 rejects at step 4 |

Finite float32 equality, inequality and overflow probes also passed.
[Validation summary](results/validation_summary.json) retains step codes and times.

Cases 07 and 09 used previously reviewed methods; their historical real holes
were not rerun here. Original `etl4/` files, raw data, databases and AWS stayed
unchanged. Synthetic inputs were not delivered as real data.
