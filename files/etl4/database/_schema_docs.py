"""Generate field and source descriptions from the database schema.

根据数据库实际结构生成字段与来源说明。
"""

from _db_common import schema_hash
from _db_files import write
from _settings import settings

SOURCES = {'schema_migration': ('Migration SHA256; step 8', 'Database schema change history'),
 'borehole': ('Step 3 borehole', 'Stable borehole identity; source IDs are unique within provider_code'),
 'borehole_revision': ('Step 3 borehole; metadata_revision_id -> id',
                       'Borehole metadata snapshot; organization names retain their source roles'),
 'dataset': ('Step 3 dataset', 'A borehole may contain multiple datasets; this does not establish branches'),
 'dataset_revision': ('Step 3/32 dataset + dataset_revision; step 37 complete revision and input_digest',
                      'Scan and processing revision; changing organization and instrument attributes belong '
                      'to the revision'),
 'sample_axis': ('Step 4 alignment_report', 'Validated common sample axis and alignment evidence'),
 'scan_sample': ('Step 5 sample_axis.parquet + step 4 core_intervals',
                 'Sample position within an axis; sample_no starts at 0 and repeated depths are valid'),
 'core_interval': ('Step 4 core_intervals.json',
                   'Tray and section intervals with inclusive sample bounds; source_label retains leading '
                   'zeros'),
 'spectral_stream': ('Step 3 spectral_streams',
                     'Physical VSWIR/TIR wavelength axes, separate from interpretation regions'),
 'interpretation_set': ('Step 3 interpretation_sets',
                        'Algorithm family, VNIR/SWIR/TIR region, and s/u result variant; no inferred '
                        'upstream processing batch'),
 'interpretation_input': ('Step 3 interpretation_inputs; empty in this batch',
                          'Only evidence-backed input relationships; similar regions do not establish a '
                          'relationship'),
 'metric_definition': ('Step 3 metric_definitions',
                       'Metric semantics; unknown or inapplicable probability attributes may be NULL, and Wt '
                       'is not a probability'),
 'scan_log': ('Step 3/32 logs; matching fields map directly and extra source attributes remain in '
              'normalized_metadata',
              'All source logs, including duplicate names and metadata-only records, belong to their own '
              'dataset revision'),
 'log_axis_binding': ('Step 4 axis_bindings and spectral sample maps; step 37 records the final binding '
                      'state',
                      'Step 3 pending_step_4 is not treated as the final binding state'),
 'asset': ('Step 5 assets/raw_assets; processing evidence locked by step 7',
           'File identity, bytes, and hash; semantic state is independent of file existence'),
 'asset_location': ('Step 37 local paths; step 40 S3 locations from upload receipts',
                    'An asset may have multiple stored copies; credentials are never stored in the database'),
 'log_asset': ('Step 3 payload_paths + step 5 source_log_id',
               'Links logs to source and canonical files; metadata_only logs may have no record'),
 'data_chunk': ('Step 5/32 assets[].row_groups / logical_group_sha256',
                'Parquet row-group bounds and integrity indexes'),
 'image_frame': ('Step 4 image_frames; source_log_id/source_path map to internal foreign keys',
                 'Thumbnails and trays retain both depth sets and their differences; no pixel-level '
                 'calibration'),
 'source_document': ('Step 3 raw_metadata + original text files',
                     '13 original JSON documents per borehole and text files; original XML remains inside '
                     'the source datasets.json document'),
 'source_reference': ('Step 3 source_references',
                      'Exact source ID resolution; unresolved references remain queryable'),
 'quality_issue': ('Step 2-5 issues', 'Source issues, evidence, and affected scope'),
 'ingest_run': ('Step 37 local import; step 40 cloud merge',
                'Run records; repeated runs reuse existing business entities'),
 'revision_validation': ('Step 37 second local validation; step 40 receives validation for the same content',
                         'Revision validation results and validator code hash; validation may be repeated'),
 'data_release': ('Step 37 complete local batch; step 40 cumulative cloud dataset_revision selection',
                  'Release manifest; local publication follows validation, and cloud activation uses an '
                  'explicit final member selection'),
 'release_dataset': ('data_release.manifest at each stage',
                     'Each release selects exactly one revision for each dataset'),
 'active_release': ('Step 37 after local validation; step 40 after explicit finalize',
                    'Single current-release reference; historical releases remain readable, and database '
                    'validation has no API dependency'),
 'borehole_branch': ('Future evidence-backed branch records; currently empty',
                     'Parent-child branches are not inferred from the number of datasets'),
 'dataset_segment': ('Step 37; complete sample interval of each independent dataset',
                     'Keep complete segments for each dataset; composite_part separately defines 3D display '
                     'selections'),
 'survey_station': ('Future survey records; currently empty',
                    'Survey revision, measured depth, and angle conventions; no fabricated trajectory'),
 'spectral_array': ('Steps 20-22 or 33; imported by 37',
                    'Validated original F32 matrices, channels, and byte strides; complete values remain in '
                    'the source file'),
 'spectral_sample_map': ('Step 21/33 official sampleNo JSON comparison; imported by 37',
                         'Inclusive mapping between source spectral rows and real database samples; no depth '
                         'interpolation'),
 'spectral_block': ('Step 22/33; imported by 37',
                    'Contiguous source F32 byte ranges and block hashes for bounded reads'),
 'image_region': ('Steps 23-25 or 33; imported by 37',
                  'Visible row region in a dataset tray image; variable row count and native pixel '
                  'coordinates'),
 'image_sample_mapping': ('Step 26/33; imported by 37',
                          'Source Section and actual sample interval; sample_index_linear v1 indicators are '
                          'approximate'),
 'image_region_asset': ('Step 27/33; imported by 37',
                        'Row region, lossless PNG, source log, and crop recipe; pixels match the source '
                        'image'),
 'dataset_succession': ('multi_dataset_decisions.json; step 37; step 40 records cross-batch relationships '
                        'from explicit selections',
                        'User-confirmed display succession, actual sample switch depth, and evidence; no '
                        'inferred branch trajectory'),
 'borehole_composite': ('Steps 37/40; per release and borehole',
                        '3D composite definition; no automatic gap filling or fallback to old paths, while '
                        '2D retains each complete dataset'),
 'borehole_composite_part': ('Steps 37/40; confirmed order and actual sample depths of each dataset',
                             '3D selection windows and inclusive source sample intervals; sequence_no also '
                             'defines the 2D order')}
SPECIAL = {'id': 'Stable internal UUID; run audits use separate UUIDs. Never substitute a source name.',
 'normalized_metadata': 'Complete normalized JSONB object, including source attributes without dedicated '
                        'columns. Original documents remain in source_document and source files.',
 'input_digest': 'Meaning depends on the table: dataset_revision hashes prepared data, sample bindings, and '
                 'media; revision_validation binds the same digest; ingest_run identifies the batch. '
                 'Different content for the same data revision is rejected.',
 'source_log_id': 'Original log identifier, preserved as text rather than forced into a PostgreSQL UUID.',
 'metric_key': 'Metric key within a revision; known metrics use their semantics, while auxiliary metrics use '
               'source_log:<source ID>.',
 'collar_geom': 'Two-dimensional EPSG:4326 Point from confirmed longitude and latitude; not a borehole '
                'trajectory.',
 'is_probability': 'true/false/NULL; NULL preserves unknown or inapplicable values and is not zero.',
 'source_is_public': "Upstream public flag, independent of s/u, identification quality, or this site's "
                     'authorization.',
 'per_sample_publication_allowed': 'Whether per-sample spectral access is validated. Validated physical F32 '
                                   'data uses true; metadata-only logs remain false.',
 'selection_from_md_m': 'Inclusive start of a 3D selection window in meters; does not change source depths.',
 'selection_to_md_m': 'End of a 3D selection window in meters; upper_inclusive controls endpoint inclusion.',
 'upper_inclusive': 'Endpoint inclusion: the preceding segment excludes the start of its successor; a '
                    'complete segment may include its final sample.',
 'predecessor_relation_id': 'Confirmed succession evidence for the selected later segment; NULL for the '
                            'first segment.',
 'confirmation_basis': 'Project-owner confirmation of succession order, switch rules, and original wording; '
                       'separate from measured branch survey records.',
 'switch_md_m': 'Depth of the first actual sample in the later segment, used as the display switch; not an '
                'inferred physical wedge depth.',
 'selection_status': 'selected means samples are selected; fully_superseded retains the dataset but selects '
                     'no samples in this composite.',
 'object_key': 'Path/object key relative to root_key; no absolute paths, parent traversal, or signed URLs.',
 'semantic_status': 'Semantic/calibration state, separate from payload_status and access_status.',
 'payload_status': 'Whether a physical file exists; metadata-only logs do not create fictitious file '
                   'records.',
 'access_status': 'Whether a copy is accessible: verified_local locally, or verified_remote from cloud '
                  'upload receipts.',
 'coordinate_kind': 'Distinguishes depth in meters, sample indexes, and interval indexes; identical numeric '
                    'names must not mix units.',
 'source_row_to_exclusive': 'Exclusive upper source-row bound, unlike inclusive tray/section sample_no_to.',
 'source_interval_convention': 'Currently closed: both sample_no_from and sample_no_to are included.',
 'actual_drill_date_precision': 'Confirmed precision of the actual date; a supplied date alone does not '
                                'confirm day-level precision.',
 'scope': 'Fields, source logs, or boreholes affected by an issue.',
 'evidence': 'Original or normalized processing evidence; does not infer missing facts.',
 'sha256': 'SHA256 of file or document bytes, not an identification confidence score.',
 'logical_sha256': 'Step 5 row-group digest of values, nulls, and source row order.',
 'mapping_version': 'Database field-mapping version; complete revisions currently use '
                    '_ingest.MAPPING_VERSION and do not overwrite old snapshots.',
 'source_row_from': 'Zero-based row within the source payload; complete F32 rows match sample_no in this '
                    'batch and are still read through validated mappings.',
 'source_row_to': 'Inclusive final source spectral row, unlike source_row_to_exclusive.',
 'sample_no_from': 'Zero-based inclusive start of the real sample interval; samples at repeated depths '
                   'remain distinct.',
 'sample_no_to': 'Inclusive final real sample in the interval.',
 'image_frame_id': 'Identity of a tray image within a particular dataset revision and image log.',
 'region_ordinal': 'Zero-based top-to-bottom row order within one image; not globally unique or fixed in '
                   'count.',
 'x_px': 'Horizontal crop origin from the top-left; the rectangle has exclusive upper bounds.',
 'y_px': 'Vertical crop origin from the top-left; the rectangle has exclusive upper bounds.',
 'coordinate_basis': 'Native decoded pixels, top-left origin, xy coordinates, and a crop rectangle with '
                     'exclusive upper bounds.',
 'valid_area': 'Reserved for future nonrectangular valid regions; NULL for current rectangular regions.',
 'region_status': 'Whether the crop region passed checks; separate from sample binding and file '
                  'accessibility.',
 'anchor_points': 'Reserved for measured pixel-to-sample/depth anchors; remains NULL without measurements.',
 'source_depth_direction': "left_to_right: the project's NVCL source convention increases depth from left to "
                           'right within each row.',
 'direction_basis': 'Recorded direction evidence; current inputs use the project-confirmed NVCL row order, '
                    'while existing packages retain their original confirmation.',
 'mapping_level': 'Currently section_interval for images; must not be described as pixel-level calibration.',
 'mapping_status': 'Currently metadata_associated, using source Section order and layout checks.',
 'indicator_method': 'Currently sample_index_linear: p=(s-a)/(b-a) within a row, or p=0.5 for one sample.',
 'indicator_version': 'Current algorithm version 1: x=p*(W-1), with W the native selected row-image width.',
 'indicator_status': 'Currently approximate; unmeasured error must not be recorded as zero or calibrated.',
 'indicator_parameters': 'Versioned interval, endpoint pixel-center, single-sample centering, and '
                         'crop-coordinate conventions.',
 'error_px': 'Measured pixel error; NULL because no calibration was performed.',
 'error_depth_m': 'Measured depth error; NULL because no calibration was performed.',
 'dtype_code': 'Currently <f4, IEEE float32 little-endian.',
 'matrix_order': 'Currently C order, with consecutive wavelength channels for each sample.',
 'sample_stride_bytes': 'Byte stride between sample starts; channel_count*4 in this batch.',
 'channel_stride_bytes': 'Byte stride between wavelength channels; 4 in this batch.',
 'byte_offset': 'Zero-based starting byte offset of a block in the original F32 file.',
 'byte_length': 'Complete contiguous block length in bytes, verifiable with its own SHA256.',
 'scaling_applied': 'false in this run; reads preserve source values without normalization or percentage '
                    'conversion.',
 'transform': 'Crop rectangle with exclusive upper bounds, source hash, direction/rotation, and '
              'pixel-equality validation.',
 'evidence_asset_id': 'Evidence asset registered within the same dataset revision.'}


def unit(column):
    if column.endswith("_mm"):
        return "mm"
    if column.endswith("_m") or column in ("depth_min_m", "depth_max_m"):
        return "m"
    if column.endswith("_deg"):
        return "degree; inclination conventions recorded separately"
    if column.endswith("_px"):
        return "pixel"
    if column == "byte_size":
        return "byte"
    if column in ("wavelengths", "wavelength_count"):
        return "see wavelength_unit / band count"
    if column == "unit":
        return "metric unit; NULL when absent"
    return "- / state or identifier"


def generate(conn):
    cols = conn.execute(
        """SELECT c.table_name,c.column_name,c.ordinal_position,c.is_nullable,c.column_default,
          format_type(a.atttypid,a.atttypmod) AS data_type FROM information_schema.columns c
          JOIN pg_namespace n ON n.nspname=c.table_schema JOIN pg_class p ON p.relnamespace=n.oid AND p.relname=c.table_name
          JOIN pg_attribute a ON a.attrelid=p.oid AND a.attname=c.column_name
          WHERE c.table_schema='core' ORDER BY c.table_name,c.ordinal_position"""
    ).fetchall()
    constraints = conn.execute(
        """SELECT r.relname AS table_name,c.conname,pg_get_constraintdef(c.oid) AS definition
          FROM pg_constraint c JOIN pg_class r ON r.oid=c.conrelid JOIN pg_namespace n ON n.oid=r.relnamespace
          WHERE n.nspname='core' ORDER BY r.relname,c.conname"""
    ).fetchall()
    indexes = conn.execute(
        "SELECT tablename,indexname,indexdef FROM pg_indexes WHERE schemaname='core' ORDER BY tablename,indexname"
    ).fetchall()
    triggers = conn.execute(
        """SELECT r.relname AS table_name,t.tgname AS trigger_name,pg_get_triggerdef(t.oid) AS definition
          FROM pg_trigger t JOIN pg_class r ON r.oid=t.tgrelid
          WHERE r.relnamespace='core'::regnamespace AND NOT t.tgisinternal ORDER BY r.relname,t.tgname"""
    ).fetchall()
    functions = conn.execute(
        "SELECT proname AS function_name,pg_get_functiondef(oid) AS definition FROM pg_proc WHERE pronamespace='core'::regnamespace ORDER BY proname"
    ).fetchall()
    views = conn.execute(
        "SELECT viewname,definition FROM pg_views WHERE schemaname='core' ORDER BY viewname"
    ).fetchall()
    lines = [
        "# Complete database field dictionary",
        "",
        f"Generated from the PostgreSQL catalog; schema digest `{schema_hash()}`. Types, nullability, defaults, and constraints come from the created database.",
        "SQL NULL means missing, unconfirmed, or inapplicable; state and evidence columns record the reason. Common fields use explicit types, and original/normalized objects are retained in full.",
        "",
    ]
    for table, (source, purpose) in SOURCES.items():
        lines.extend(
            [
                f"**{table}**",
                "",
                purpose + ". Source: " + source + ".",
                "",
                "| Field | PostgreSQL type | Nullable | Unit | Meaning/source | Default |",
                "|---|---|---|---|---|---|",
            ]
        )
        for c in [c for c in cols if c["table_name"] == table]:
            name = c["column_name"]
            meaning = SPECIAL.get(
                name,
                (
                    "Foreign key or ownership identifier; see the constraints below."
                    if name.endswith("_id")
                    else "Matching field from the table's listed source; see the table description and source mapping."
                ),
            )
            lines.append(
                f"| `{name}` | `{c['data_type']}` | {c['is_nullable']} | {unit(name)} | {meaning} | `{c['column_default'] or 'None'}` |"
            )
        lines.extend(["", "Constraints:", ""])
        lines.extend(
            f"- `{c['conname']}`: `{c['definition']}`"
            for c in constraints
            if c["table_name"] == table
        )
        lines.extend(["", "Indexes:", ""])
        lines.extend(f"- `{i['indexdef']}`" for i in indexes if i["tablename"] == table)
        lines.extend(["", "Write validation and immutability triggers:", ""])
        lines.extend(
            f"- `{t['trigger_name']}`: `{t['definition']}`"
            for t in triggers
            if t["table_name"] == table
        )
        if not any(t["table_name"] == table for t in triggers):
            lines.append("No user-defined triggers; the constraints and role permissions above still apply.")
        lines.append("")
    lines.extend(
        [
            "**Read views**",
            "",
            "Views neither copy nor remove source records. 3D reads composite_samples; 2D retrieves complete axes through dataset_full.",
            "",
        ]
    )
    for v in views:
        lines.extend([f"**{v['viewname']}**", "", "| Field | PostgreSQL type |", "|---|---|"])
        lines.extend(
            f"| `{c['column_name']}` | `{c['data_type']}` |"
            for c in cols
            if c["table_name"] == v["viewname"]
        )
        lines.extend(["", "```sql", v["definition"], "```", ""])
    lines.extend(
        [
            "**Trigger function definitions**",
            "",
            "These definitions come from the database and include bulk sample/interval checks and immutability rules for existing records.",
            "",
        ]
    )
    for f in functions:
        lines.extend(["```sql", f["definition"], "```", ""])
    write(settings.database_dir / "schema_dictionary.md", "\n".join(lines) + "\n")
    write(
        settings.reports_dir / "schema_catalog.json",
        {
            "schema_sha256": schema_hash(),
            "columns": cols,
            "constraints": constraints,
            "indexes": indexes,
            "triggers": triggers,
            "functions": functions,
            "views": views,
        },
    )
    mapping = [
        "# Source field to database mapping",
        "",
        "Step 7 registers the selected inputs; steps 31-33 prepare multiple datasets, and step 37 imports and validates complete revisions. Original XML remains intact in the source datasets.json document.",
        "",
        "| Database table | Input source | Mapping and semantics |",
        "|---|---|---|",
    ]
    mapping.extend(f"| `{t}` | {s} | {p} |" for t, (s, p) in SOURCES.items())
    mapping += [
        "",
        "Field transformations to note:",
        "",
        "- `borehole.metadata_revision_id` -> `borehole_revision.id`; stable borehole identity is separate from attribute history.",
        "- Attributes such as `dataset.instrument_name/owner_name/source_dataset_name` belong to `dataset_revision`, preserving the complete revision object.",
        "- Step 3 `axis_id=null` and `axis_binding_status=pending_step_4` remain in normalized evidence; API and canonical relationships use step 4 `log_axis_binding`.",
        "- Sample Parquet `tray_index/section_index` map to interval foreign keys using step 4 ordinals on the same axis; ordinals are not tray labels.",
        "- Image `source_log_id/source_path` resolve to `log_id/asset_id`; source fields in logs and assets retain the original values.",
        "- Parquet row-group `source_row_to_exclusive` is exclusive; tray and section `sample_no_to` is inclusive.",
        "- `is_probability` may be NULL for text or undefined metrics; Wt stores false. Numeric payloads remain in Parquet without renormalization or clipping.",
        "- Each original `raw_metadata` document has one `source_document`; asset tables retain the original path, byte count, and SHA256.",
        "- In addition to source and canonical files, locked processing reports and alignment evidence are registered as evidence assets; failed development outputs are not automatically published.",
        "- `interpretation_input`, `borehole_branch`, and `survey_station` do not invent data to fill foreign keys. dataset_segment stores complete source segments; composite_part only describes display selections.",
        "- 2D obtains complete dataset ranges through v_borehole_dataset_full and reads samples/images by independent axis_id; 3D uses v_borehole_composite_samples.",
        "- Multi-dataset image depth comparisons allow original decimal endpoints to reconstruct identical IEEE float32 bits; actual differences are retained without uniform rounding or source rewriting.",
        "",
    ]
    write(settings.database_dir / "source_to_table_mapping.md", "\n".join(mapping))
