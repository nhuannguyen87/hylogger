# Five-group split validation

Validated on 2026-10-11. All five reviewed source groups produced two independent
holes each. All ten outputs passed preparation steps 1-6, the file-only input
registration in step 7, and media processing/package acceptance in steps 20-28.
The real final-version input loader also accepted every package and constructed
its final IDs and verified spectral bindings. No database transaction or AWS
operation was performed. No split-related processing failure was found.

## Results

| Source group | Output holes | Samples | Tray images |
|---|---:|---:|---:|
| BBDD0001 | 2 | 147,910 | 259 |
| BBDD0002 | 2 | 146,436 | 250 |
| BH01 | 2 | 60,924 | 113 |
| BH02 | 2 | 70,500 | 131 |
| BNDD001 | 2 | 68,082 | 98 |
| **Total** | **10** | **493,852** | **851** |

The prepared data also contains 20 spectral arrays and 10 profile payloads.
All 78,509 repeated-depth samples remain present; depth is not used as a unique
sample identifier. `BH01-unnamed` and `BH02-unnamed` each contain 1,340 samples
and one declared tray image. Both are valid inputs and passed normally.

## Completed media checks

- **Spectra:** all 20 arrays passed official reference comparisons and received
  verified sample bindings and byte-range indexes. The 96 reference requests
  covered 192 sample spectra across first, section-boundary, middle and last
  positions, comparing every channel. This checks the official ordering contract
  with reference samples; it is not an independent redownload of every spectrum.
- **Images:** all 851 source tray images produced 3,690 lossless row images.
  Each PNG was read back and compared with the corresponding source pixels.
  Sorted row intervals cover all 493,852 samples exactly once, without gaps or
  overlap. Repeated depths and empty sections remain present.
- **Visual review:** 99 layout representatives across 14 contact sheets were
  inspected before recording step 25 review. No boundary correction was required.
- **Packages:** all ten passed step 28 local acceptance, including source-file,
  prepared-file, media-file and package identity checks.
- **Final import inputs:** the actual step 37 input loader constructed ten distinct
  borehole/dataset/version/axis identities without inserting records. All spectral
  bindings target the corresponding final sample axis; image rows retain their
  own dataset and source-frame associations.

The first official reference request failed because the restricted execution
environment could not resolve external hosts. With network access approved, all
reference requests and comparisons passed. No data rule or original ETL code was
changed to bypass that failure.

## Automatic workflow exercised

1. Run normal preparation and retain its failure report. The original multi-dataset
   route stopped at its required membership-cache input for these five groups.
2. Diagnose the failures as the reviewed same-name splits. Read the downloader's
   existing official collection response to assign files by dataset ID.
3. Create ten independent input packages, with their own coordinates and names.
   Copy and hash the selected payloads; keep the original raw packages unchanged.
4. Run normal steps 1-6 on each new input. If a recognized processing failure
   appears, apply the corresponding existing adapter and retry automatically.
5. Record the final code directory, work directory and next step for each output.
   Unknown problems stay pending; there is no timeout that approves a decision.

The initial complete run passed all ten outputs. After correcting the split
metadata labels described below, all ten were processed again with the final
code, reusing the selected raw packages. The subsequent media run continued these
final preparation results through package acceptance.

## Problems found and handled

| Problem | Handling and observed result |
|---|---|
| Shared collar metadata can describe the other hole | Each output uses its own dataset Longitude/Latitude. No shared company, drilling date or hole length is copied. |
| Datum absent or differently labelled | Use values unchanged as EPSG:4326 under the owner's explicit shared-system assumption. Preserve the original datum and record the assumption; no coordinate transformation is claimed. |
| Unknown optional metadata | Company, custodian, drilling dates, method, hole length, elevation and orientation remain null. This does not block preparation. |
| Missing orientation was labelled confidential by the normal default | External working copies now label it `not_provided_in_selected_collar`; the original ETL is untouched. |
| Lake Roe-Bombara source identity and tray-local section numbering | Automatically apply cases 01 and 02 to both BBDD outputs from this project. |
| Floating-point representation differences | Automatically apply case 06 to both Bannockburn outputs and both Behemoth outputs. |
| Only one tray image | Accept the declared count and verify its presence and association. No arbitrary minimum number of images is imposed. |
| One output fails | Keep its reason separately and continue the other output. Unit tests cover missing coordinates and a corrupt selected payload. |

BNDD001-Stanley, BNDD001-Binneringie, BH01-unnamed and BH02-unnamed needed no
additional processing adapter after splitting. All outputs use an external
working copy for the split metadata policy.

## Checks and limits

- All ten source dataset IDs are retained and map to ten distinct borehole IDs.
- Every normalized coordinate equals its own dataset value. The assumption and
  original datum survive in the resulting metadata/provenance.
- All 851 tray images and 3,690 section ranges passed automated row geometry and
  sample association checks. Row slices cover the original pixels without gaps.
  Eight boundaries used the existing equal-height fallback. The subsequent visual
  review and crop encoding checks also passed. Pixel-to-depth position within a
  section remains approximate, as in the normal workflow.
- All 30 unit tests passed, including invalid coordinates, changed or conflicting
  sources, incomplete selections, corrupt payloads, independent output failure,
  rule accumulation and unresolved-error stopping.
- All 65 original `etl4/` files retain their pre-run SHA-256 hashes. All 25 external
  Python files parse and contain no Chinese characters.

The historical step 6 reports retain `passed_with_known_limitations`; they describe
that earlier stage. Steps 21-22 and the final import contexts now contain verified
spectral bindings. Database insertion itself has not been exercised in this check.
Source domain references, physical profile calibration and borehole trajectory
remain unavailable or unresolved; this change does not invent them.
No new split-related blocking problem was found in these five reviewed groups.
New, unreviewed same-name groups still require a grouping decision before being
added to `13_split_boreholes/reviewed_splits.json`.

## Evidence and use

The local verification workspace is named `split_automation_check_20261011`.
Its `final_validation/result.json` contains the ten final processing results and
absolute output paths. `final_validation/audit.json` records coordinates, counts,
applied rules, geometry results and validation limits. `audit_final.py` reproduces
the preparation audit. `media_validation/audit.json` contains the completed media
and final import input results, including every package path. The phase reports,
`visual_review.json`, `continue_media_check.py` and `audit_media.py` preserve the
continuation and review evidence. Large generated data is kept outside this
handoff repository.

For subsequent runs, use the two commands in [README.md](README.md). Read the
generated `automation_result.md` first, then use the successful entry's recorded
code and work directories to continue at step 7. The normal failure handler stops
at step 6; the additional media validation described here was explicitly run on
these ten outputs. Their completed packages are now ready for step 37.
