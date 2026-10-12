# 12. Keep core, exclude cuttings

Use this folder only for failed holes assigned to case 12 by `diagnose.py`.
It removes datasets labelled `cuttings`, `cuttings2`, etc. from a new input
package. Core datasets keep their original dataset IDs and payload bytes.
Archived raw files are not deleted.

```powershell
python special_processing/12_core_only/prepare_inputs.py --diagnosis "D:/etl_check/diagnosis/diagnosis.json" --cache-dir "D:/100demo/data/cache/wa" --work-dir "D:/etl_check/core"
python special_processing/check_baseline.py --raw-dir "D:/etl_check/core/prepared_raw" --work-dir "D:/etl_check/core_check"
```

The cache supplies official dataset-to-log ownership, including profiles.
Only retained payloads are copied and hashed. Copies need additional disk space;
the script checks available space before copying. Unknown sample types stop for review.

Read `input_selection_result.json` for retained and excluded dataset IDs.
An all-cuttings hole produces no input package. A prepared package still needs
the normal checks. Diagnose any remaining failures in `core_check/failed_holes.json`
and use its assigned handler, for example case 01 for names containing spaces.

This project analyses cored intervals only. Cuttings remain useful for continuous
lithological, mineralogical and stratigraphic comparison, but are outside this
delivery's scope. [Full selection policy](../../docs/dataset_selection.md).
