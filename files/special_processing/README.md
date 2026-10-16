# Special processing

Run normal preparation first. Then one command diagnoses the failed inputs,
applies supported special handling and runs preparation again. The repository's
`etl4/` and archived raw data stay unchanged.

## 1. Run normal preparation

From the repository root, with `requirements.txt` installed:

```powershell
python special_processing/check_baseline.py --raw-dir "D:/100demo/data/raw" --work-dir "D:/etl_check/normal"
```

The entry chooses single or multiple dataset preparation for each hole.
Add `--holes "HOLE_ID"` to limit the batch. Each failed hole stops independently.
Use a new work directory outside raw data and this repository.

## 2. Handle failures automatically

```powershell
python special_processing/run_failed.py --failures "D:/etl_check/normal/failed_holes.json" --work-dir "D:/etl_check/automatic"
```

This diagnoses failures and calls their assigned handlers. Cases 12-13 create
selected input packages and return them to normal steps 1-6. Recognized later
failures automatically receive the applicable existing rules. Each retry must
add a new rule; unresolved or repeated failures stop and remain in the report.

For split holes, coordinates come from each dataset under the owner's shared
coordinate-system decision. Missing optional attributes stay null. No manually
filled collar file is required. The downloader cache is found beside the raw
directory; supply `--cache-dir` only if it has moved.

## 3. Read the result

- `automation_result.md`: source hole, output hole, result and remaining problem.
- `automation_result.json`: code, raw and work directories, applied rules and next step.
- Successful single-dataset preparation continues at step 7; multi-dataset
  preparation continues at step 33. Use the code and work paths in the report.

No database import or AWS upload happens here. Unknown source relationships and
unsupported errors remain visible; decisions are never skipped after a timeout.

## Handler folders

| Folder | Problem |
|---|---|
| [01](01_source_identity/README.md) | Source names and identity |
| [02](02_tray_local_sections/README.md) | Sections numbered within each tray |
| [03](03_duplicate_interval_logs/README.md) | Duplicate interval logs |
| [04](04_duplicate_image_logs/README.md) | Duplicate image logs |
| [05](05_nullable_auxiliary_values/README.md) | Null auxiliary values |
| [06](06_float32_precision/README.md) | Float32 text precision |
| [07](07_reviewed_depth_resets/README.md) | Reviewed depth resets |
| [08](08_literal_text_csv/README.md) | Reviewed CSV text |
| [09](09_unclassified_spectral_axis/README.md) | Unclassified wavelength axes |
| [10](10_unavailable_profile/README.md) | Explicitly unavailable profiles |
| [11](11_multi_dataset_profile/README.md) | Ambiguous profile membership |
| [12](12_core_only/README.md) | Exclude cuttings; keep core |
| [13](13_split_boreholes/README.md) | Split reviewed same-name boreholes |

The individual `diagnose.py`, `apply_fix.py` and `prepare_inputs.py` commands
remain available for targeted investigation. Cases 07 and 08 use reviewed IDs;
case 13 uses its reviewed split configuration. A new source type may require a
new rule; the automation does not guess associations.

[Five-group validation](split_automation_validation.md) ·
[Dataset policy](../docs/dataset_selection.md) ·
[Earlier validation](dataset_selection_validation.md) ·
[Historical investigation](../docs/special_processing_cases.md)
