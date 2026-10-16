# 13. Split same-name boreholes

The five reviewed groups become ten independent holes. Names use
`original-hole-name-source-name`; an empty project becomes `unnamed`.
The reviewed dataset IDs and labels are stored in `reviewed_splits.json`.

All ten outputs passed preparation, media processing and package acceptance in the
[five-group validation](../split_automation_validation.md).

## Run automatically

After normal preparation records failures, run from the repository root:

```powershell
python special_processing/run_failed.py --failures "D:/etl_check/normal/failed_holes.json" --work-dir "D:/etl_check/automatic"
```

The program diagnoses failures, splits the selected inputs, runs steps 1-6,
and automatically applies existing special rules when a failure identifies them.
Read `automation_result.md` for results and `automation_result.json` for output
paths. A successful result can continue at step 7 using its reported code and
work directories. This command does not import a database or upload to AWS.

## What happens automatically

- Official cached dataset membership assigns logs, images, spectra and profiles.
- Each dataset supplies its own `Longitude` and `Latitude`.
- The project owner has instructed that these coordinates share the system used
  by this project. Values are used unchanged as EPSG:4326. The original datum,
  including a missing datum, and this assumption are recorded in provenance.
- Unknown company, drilling dates, hole length and other optional collar fields
  stay null. They do not block preparation and are never copied from another hole.
- A dataset with one declared tray image is valid if that one image is present.
- Dataset IDs remain unchanged; different output hole names yield distinct hole IDs.
- Each output is committed separately. One failed output does not cancel another.

The cache defaults to `cache/<provider>` beside the original raw directory.
Use `--cache-dir` only if it has moved. No collar file is required.
`--collars` is an optional override for independently supplied source records.

| Original | Output names |
|---|---|
| BBDD0001 | BBDD0001-Bannockburn; BBDD0001-Lake Roe-Bombara |
| BBDD0002 | BBDD0002-Bannockburn; BBDD0002-Lake Roe-Bombara |
| BH01 | BH01-Behemoth; BH01-unnamed |
| BH02 | BH02-Behemoth; BH02-unnamed |
| BNDD001 | BNDD001-Stanley; BNDD001-Binneringie |

## Boundaries

The original `etl4/` and archived raw files are unchanged. New input packages
contain independent payload copies, so additional disk space is required.
Missing or corrupt payloads, conflicting ownership, invalid coordinates and
unsupported processing errors remain failures. Unknown issues are reported;
there is no timeout that automatically approves or skips a decision.

To generate only the new inputs, use `13_split_boreholes/prepare_inputs.py`
with `--diagnosis` and `--work-dir`. The automatic entry above is recommended.
