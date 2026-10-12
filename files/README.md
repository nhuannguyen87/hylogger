# ETL4 Handoff

Code for data preparation, local database import, backup and restore verification, and cloud batch delivery, organized under `etl4/`.

**Project scope:** core intervals only. Exclude cuttings and split reviewed
same-name boreholes by source, using `-unnamed` when the source name is empty.
See [dataset selection and naming](docs/dataset_selection.md). Failed inputs use
external [case 12](special_processing/12_core_only/README.md) for core selection or
[case 13](special_processing/13_split_boreholes/README.md) for splitting, then return
to normal preparation. The original `etl4/` code is unchanged.

Use `special_processing/run_failed.py --failures <failed_holes.json> --work-dir <new-directory>`
to diagnose and prepare failed inputs automatically. Split holes use their own
dataset coordinates under the owner's shared-coordinate-system decision;
unknown optional attributes remain null. No manual collar file is required.

## Processing Order

Numbers refer to scripts under `etl4/processing/` and `etl4/database/`. Continue only after each step succeeds.

1. **Raw file precheck**: 0, classify boreholes into single dataset, multiple datasets, and those requiring attention.
2. **Data and media preparation**: For single datasets, run 1–6 → 7 `--inputs-only` → 20 `--files-only` → 21–27; for multiple datasets, run 31–33.
3. **Prepared package verification**: 28, seal the prepared package and complete the first acceptance check.
4. **Database setup**: 8, apply SQL migrations and configure permissions.
5. **Complete import**: 37 `--batch <batch-manifest.json>`, read the prepared packages, complete the second acceptance check, and publish the local release.

Run as needed: 14 for backup and restore verification; 38 → 39 → 40 → 41 for batch export, upload, cloud merge, and local cleanup. Step 40 switches the active release only when a selection manifest is supplied with `--finalize`. Step 41 previews cleanup by default and deletes local files only with `--apply`.

### Preparation

- In steps 1 and 31, select boreholes with `--holes HOLE_ID_1 HOLE_ID_2` or `--holes-file <borehole-list.txt>`.
- `7 --inputs-only` registers inputs without starting the database.
- In steps 21, 31, and 33, add `--fetch-reference` when official reference data is not cached.
- Steps 25 and 33 first generate images for review. Inspect and confirm them, then rerun the step with `--record-reviewed`.
- Run step 28 separately for single and multiple dataset packages. For multiple datasets, use:

```text
--media-work-dir <work-dir>/database/multi_work/media
--evidence-dir <work-dir>/database/multi_work/evidence
```

### Batch Manifest and Import

Use the [single dataset example](examples/batch.single.example.json) or [multiple dataset example](examples/batch.multi.example.json), fill in the values, and save it as `batch.json` in the working directory:

- `packages[].directory`: The `package_object_key` from the step 28 report at `database/reports/28_media_package.json`, or the absolute path to the sealed package. Record this value after each package is sealed; for a mixed batch, include both packages in `packages`.
- `packages[].prepared_work_dir`: The working directory used to prepare that package.
- `decisions.orders`: The confirmed order of source dataset IDs for each borehole with multiple datasets. It must match the actual dataset membership.

After step 8 sets up the database, pass `--batch <work-dir>/batch.json` to step 37.

Step 37 automatically generates `deployment_asset_manifest.json` under `database/reports/` in the working directory; it does not need to be supplied beforehand. Database backups do not contain image, spectral, or Parquet files. Preserve the corresponding assets when handing over processed data.

## Reference files

- [Special processing](special_processing/README.md): diagnose and handle failed holes without changing the original `etl4/`.
- [Historical cases](docs/special_processing_cases.md) and [source instances](docs/special_case_instances.md).
- [Migrations](etl4/database/migrations/): SQL migrations 001–006.
- [Official spectral references](etl4/database/media_evidence/official/): format documentation and original license.
- [Examples](examples/): connection settings and batch manifests.

Use `--help` to view all arguments for each script.
