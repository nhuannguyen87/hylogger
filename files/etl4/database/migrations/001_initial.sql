CREATE SCHEMA core;
CREATE TABLE core.schema_migration(name text PRIMARY KEY, sha256 text NOT NULL, applied_at timestamptz NOT NULL DEFAULT now());
CREATE TABLE core.borehole (
 id uuid PRIMARY KEY, provider_code text NOT NULL, source_hole_id text NOT NULL, source_identifier text,
 UNIQUE(provider_code,source_hole_id)
);
CREATE TABLE core.borehole_revision (
 id uuid PRIMARY KEY, borehole_id uuid NOT NULL REFERENCES core.borehole, source_name text NOT NULL,
 provider_code text, custodian_name text, driller_name text, operator_name text, project_name text,
 drilling_method text, reported_length_m double precision, drill_start_date date, drill_end_date date,
 actual_drill_date_precision text NOT NULL, collar_geom public.geometry(Point,4326),
 coordinate_basis text, horizontal_crs text, vertical_crs text, elevation_m double precision,
 azimuth_deg double precision, inclination_deg double precision, inclination_convention text,
 orientation_missing_reason text, orientation_missing_reason_source text, trajectory_status text NOT NULL,
 normalized_metadata jsonb NOT NULL, UNIQUE(id,borehole_id)
);
CREATE INDEX borehole_collar_gist ON core.borehole_revision USING gist(collar_geom);
CREATE TABLE core.dataset (
 id uuid PRIMARY KEY, borehole_id uuid NOT NULL REFERENCES core.borehole, source_dataset_id text NOT NULL,
 UNIQUE(borehole_id,source_dataset_id), UNIQUE(id,borehole_id)
);
CREATE TABLE core.dataset_revision (
 id uuid PRIMARY KEY, dataset_id uuid NOT NULL, borehole_id uuid NOT NULL, borehole_revision_id uuid NOT NULL,
 source_dataset_name text, source_borehole_uri text, source_project_name text, instrument_name text, owner_name text,
 source_snapshot_id text NOT NULL, pipeline_id text NOT NULL, input_digest text NOT NULL, mapping_version integer NOT NULL,
 source_created_at timestamptz, source_modified_at timestamptz, source_download_started_at timestamptz, source_download_finished_at timestamptz,
 normalized_metadata jsonb NOT NULL,
 UNIQUE(dataset_id,source_snapshot_id,pipeline_id,mapping_version), UNIQUE(id,borehole_id), UNIQUE(id,dataset_id),
 FOREIGN KEY(dataset_id,borehole_id) REFERENCES core.dataset(id,borehole_id),
 FOREIGN KEY(borehole_revision_id,borehole_id) REFERENCES core.borehole_revision(id,borehole_id)
);
CREATE TABLE core.sample_axis (
 id uuid PRIMARY KEY, dataset_revision_id uuid NOT NULL REFERENCES core.dataset_revision,
 sample_count bigint NOT NULL CHECK(sample_count>0), depth_min_m double precision NOT NULL, depth_max_m double precision NOT NULL,
 coordinate_kind text NOT NULL DEFAULT 'measured_depth_m', alignment_evidence jsonb NOT NULL,
 UNIQUE(id,dataset_revision_id), CHECK(depth_min_m<=depth_max_m)
);
CREATE TABLE core.core_interval (
 id uuid PRIMARY KEY, axis_id uuid NOT NULL REFERENCES core.sample_axis, interval_kind text NOT NULL CHECK(interval_kind IN ('tray','section')),
 ordinal integer NOT NULL CHECK(ordinal>=0), source_label text NOT NULL, parent_interval_id uuid,
 sample_no_from bigint NOT NULL CHECK(sample_no_from>=0), sample_no_to bigint NOT NULL,
 observed_depth_min_m double precision, observed_depth_max_m double precision,
 source_log_id text NOT NULL, source_data_row bigint NOT NULL,
 source_interval_convention text NOT NULL CHECK(source_interval_convention='closed'),
 UNIQUE(id,axis_id), UNIQUE(axis_id,interval_kind,ordinal), CHECK(sample_no_from<=sample_no_to),
 CHECK((interval_kind='tray' AND parent_interval_id IS NULL) OR (interval_kind='section' AND parent_interval_id IS NOT NULL)),
 FOREIGN KEY(parent_interval_id,axis_id) REFERENCES core.core_interval(id,axis_id)
);
CREATE TABLE core.scan_sample (
 axis_id uuid NOT NULL REFERENCES core.sample_axis, sample_no bigint NOT NULL CHECK(sample_no>=0), md_m double precision NOT NULL,
 tray_interval_id uuid NOT NULL, section_interval_id uuid NOT NULL,
 tray_sample_no bigint NOT NULL CHECK(tray_sample_no>0), section_sample_no bigint NOT NULL CHECK(section_sample_no>0), section_distance_mm double precision,
 PRIMARY KEY(axis_id,sample_no), FOREIGN KEY(tray_interval_id,axis_id) REFERENCES core.core_interval(id,axis_id),
 FOREIGN KEY(section_interval_id,axis_id) REFERENCES core.core_interval(id,axis_id),
 CHECK(md_m>'-Infinity'::float8 AND md_m<'Infinity'::float8)
);
CREATE INDEX sample_depth_idx ON core.scan_sample(axis_id,md_m,sample_no);
ALTER TABLE core.core_interval ADD FOREIGN KEY(axis_id,sample_no_from) REFERENCES core.scan_sample(axis_id,sample_no) DEFERRABLE INITIALLY DEFERRED;
ALTER TABLE core.core_interval ADD FOREIGN KEY(axis_id,sample_no_to) REFERENCES core.scan_sample(axis_id,sample_no) DEFERRABLE INITIALLY DEFERRED;
CREATE TABLE core.spectral_stream (
 id uuid PRIMARY KEY, dataset_revision_id uuid NOT NULL REFERENCES core.dataset_revision,
 region_code text NOT NULL, wavelength_unit text NOT NULL, wavelength_count integer NOT NULL CHECK(wavelength_count>0),
 wavelengths double precision[] NOT NULL, classification_basis text NOT NULL,
 UNIQUE(id,dataset_revision_id), CHECK(cardinality(wavelengths)=wavelength_count)
);
CREATE TABLE core.interpretation_set (
 id uuid PRIMARY KEY, dataset_revision_id uuid NOT NULL REFERENCES core.dataset_revision,
 source_set_code text NOT NULL, algorithm_family text, output_region text, variant_code text,
 algorithm_version text, actual_mineral_library text, parameters jsonb, grouping_basis text NOT NULL, input_association_status text NOT NULL,
 UNIQUE(dataset_revision_id,source_set_code), UNIQUE(id,dataset_revision_id)
);
CREATE TABLE core.interpretation_input (
 interpretation_set_id uuid NOT NULL, spectral_stream_id uuid NOT NULL, dataset_revision_id uuid NOT NULL,
 evidence jsonb NOT NULL, PRIMARY KEY(interpretation_set_id,spectral_stream_id),
 FOREIGN KEY(interpretation_set_id,dataset_revision_id) REFERENCES core.interpretation_set(id,dataset_revision_id),
 FOREIGN KEY(spectral_stream_id,dataset_revision_id) REFERENCES core.spectral_stream(id,dataset_revision_id)
);
CREATE TABLE core.metric_definition (
 dataset_revision_id uuid NOT NULL REFERENCES core.dataset_revision, metric_key text NOT NULL,
 display_name text NOT NULL, value_type text, unit text, definition_status text NOT NULL, is_probability boolean NOT NULL,
 PRIMARY KEY(dataset_revision_id,metric_key)
);
CREATE TABLE core.scan_log (
 id uuid PRIMARY KEY, dataset_revision_id uuid NOT NULL REFERENCES core.dataset_revision,
 source_log_id text NOT NULL, source_log_name text NOT NULL, log_kind text NOT NULL,
 source_log_type text, source_algorithm_id text, source_mask_log_id text, source_is_public boolean,
 source_created_at timestamptz, source_modified_at timestamptz, source_status text,
 metric_key text, metric_code text, component_rank integer, interpretation_set_id uuid, spectral_stream_id uuid,
 coordinate_kind text, observed_row_count bigint, observed_value_kind text, unit text, origin text,
 availability_status text NOT NULL, omission_reason text, definition_status text NOT NULL,
 array_layout_status text, per_sample_publication_allowed boolean NOT NULL DEFAULT false,
 declared_channel_count integer, observed_channel_count integer, physical_geometry_status text, null_tokens jsonb,
 source_metadata_file text NOT NULL, normalized_metadata jsonb NOT NULL,
 UNIQUE(dataset_revision_id,log_kind,source_log_id), UNIQUE(id,dataset_revision_id),
 FOREIGN KEY(dataset_revision_id,metric_key) REFERENCES core.metric_definition,
 FOREIGN KEY(interpretation_set_id,dataset_revision_id) REFERENCES core.interpretation_set(id,dataset_revision_id),
 FOREIGN KEY(spectral_stream_id,dataset_revision_id) REFERENCES core.spectral_stream(id,dataset_revision_id),
 CHECK(component_rank IS NULL OR component_rank>0)
);
CREATE INDEX scan_log_filter_idx ON core.scan_log(dataset_revision_id,log_kind,interpretation_set_id,metric_key);
CREATE TABLE core.log_axis_binding (
 log_id uuid PRIMARY KEY, dataset_revision_id uuid NOT NULL, axis_id uuid, status text NOT NULL, basis text NOT NULL,
 FOREIGN KEY(log_id,dataset_revision_id) REFERENCES core.scan_log(id,dataset_revision_id),
 FOREIGN KEY(axis_id,dataset_revision_id) REFERENCES core.sample_axis(id,dataset_revision_id),
 CHECK(status<>'verified' OR axis_id IS NOT NULL)
);
CREATE TABLE core.asset (
 id uuid PRIMARY KEY, dataset_revision_id uuid NOT NULL REFERENCES core.dataset_revision,
 asset_kind text NOT NULL, representation text NOT NULL CHECK(representation IN ('raw','canonical','evidence')),
 logical_path text NOT NULL, sha256 text NOT NULL CHECK(sha256 ~ '^[a-f0-9]{64}$'), byte_size bigint NOT NULL CHECK(byte_size>=0),
 media_type text NOT NULL, payload_status text NOT NULL DEFAULT 'present', semantic_status text NOT NULL,
 provenance jsonb NOT NULL,
 UNIQUE(dataset_revision_id,representation,logical_path), UNIQUE(id,dataset_revision_id)
);
CREATE TABLE core.asset_location (
 id uuid PRIMARY KEY, asset_id uuid NOT NULL REFERENCES core.asset, backend text NOT NULL, root_key text NOT NULL,
 object_key text NOT NULL, access_status text NOT NULL, verified_sha256 text NOT NULL,
 UNIQUE(asset_id,backend,root_key,object_key), CHECK(object_key !~ '(^[/\\]|(^|[/\\])\.\.([/\\]|$)|:)')
);
CREATE TABLE core.log_asset (
 log_id uuid NOT NULL, asset_id uuid NOT NULL, dataset_revision_id uuid NOT NULL, role text NOT NULL,
 PRIMARY KEY(log_id,asset_id), FOREIGN KEY(log_id,dataset_revision_id) REFERENCES core.scan_log(id,dataset_revision_id),
 FOREIGN KEY(asset_id,dataset_revision_id) REFERENCES core.asset(id,dataset_revision_id)
);
CREATE TABLE core.data_chunk (
 asset_id uuid NOT NULL, dataset_revision_id uuid NOT NULL, axis_id uuid NOT NULL, log_id uuid,
 row_group integer NOT NULL CHECK(row_group>=0), source_row_from bigint NOT NULL CHECK(source_row_from>=0), source_row_to_exclusive bigint NOT NULL,
 row_count integer NOT NULL CHECK(row_count>0), coordinate_kind text NOT NULL, coordinate_min double precision NOT NULL, coordinate_max double precision NOT NULL,
 logical_sha256 text NOT NULL, PRIMARY KEY(asset_id,row_group),
 CHECK(source_row_to_exclusive-source_row_from=row_count), CHECK(coordinate_min<=coordinate_max),
 FOREIGN KEY(asset_id,dataset_revision_id) REFERENCES core.asset(id,dataset_revision_id),
 FOREIGN KEY(axis_id,dataset_revision_id) REFERENCES core.sample_axis(id,dataset_revision_id),
 FOREIGN KEY(log_id,dataset_revision_id) REFERENCES core.scan_log(id,dataset_revision_id)
);
CREATE INDEX chunk_range_idx ON core.data_chunk(log_id,coordinate_min,coordinate_max);
CREATE TABLE core.image_frame (
 id uuid PRIMARY KEY, dataset_revision_id uuid NOT NULL, axis_id uuid NOT NULL, core_interval_id uuid NOT NULL,
 log_id uuid NOT NULL, asset_id uuid NOT NULL, image_kind text NOT NULL, image_ordinal integer NOT NULL,
 source_tray_label text NOT NULL, width_px integer NOT NULL CHECK(width_px>0), height_px integer NOT NULL CHECK(height_px>0),
 depth_from_m double precision NOT NULL, depth_to_m double precision NOT NULL,
 depth_from_difference_m double precision NOT NULL, depth_to_difference_m double precision NOT NULL, depth_comparison_tolerance_m double precision NOT NULL,
 association_basis text NOT NULL, pixel_depth_mapping jsonb,
 UNIQUE(log_id,image_ordinal), CHECK(depth_from_m<=depth_to_m),
 FOREIGN KEY(core_interval_id,axis_id) REFERENCES core.core_interval(id,axis_id),
 FOREIGN KEY(axis_id,dataset_revision_id) REFERENCES core.sample_axis(id,dataset_revision_id),
 FOREIGN KEY(log_id,dataset_revision_id) REFERENCES core.scan_log(id,dataset_revision_id),
 FOREIGN KEY(asset_id,dataset_revision_id) REFERENCES core.asset(id,dataset_revision_id)
);
CREATE TABLE core.source_document (
 id uuid PRIMARY KEY, dataset_revision_id uuid NOT NULL, asset_id uuid NOT NULL, document_name text NOT NULL,
 content_json jsonb, content_text text,
 UNIQUE(dataset_revision_id,document_name), FOREIGN KEY(asset_id,dataset_revision_id) REFERENCES core.asset(id,dataset_revision_id),
 CHECK(content_json IS NOT NULL OR content_text IS NOT NULL)
);
CREATE TABLE core.source_reference (
 id uuid PRIMARY KEY, dataset_revision_id uuid NOT NULL REFERENCES core.dataset_revision,
 role text NOT NULL, source_target_id text NOT NULL, target_log_id uuid, status text NOT NULL, evidence jsonb NOT NULL,
 FOREIGN KEY(target_log_id,dataset_revision_id) REFERENCES core.scan_log(id,dataset_revision_id),
 CHECK(status<>'resolved' OR target_log_id IS NOT NULL)
);
CREATE TABLE core.quality_issue (
 id uuid PRIMARY KEY, dataset_revision_id uuid NOT NULL REFERENCES core.dataset_revision,
 processing_step integer NOT NULL, code text NOT NULL, severity text NOT NULL, scope text NOT NULL, detail text NOT NULL, evidence jsonb,
 impact text NOT NULL
);
CREATE TABLE core.ingest_run (
 id uuid PRIMARY KEY, started_at timestamptz NOT NULL DEFAULT now(), finished_at timestamptz,
 input_digest text NOT NULL, code_sha256 text NOT NULL, schema_sha256 text NOT NULL, holes jsonb NOT NULL,
 status text NOT NULL CHECK(status IN ('running','staged','failed')), detail jsonb NOT NULL DEFAULT '{}'
);
CREATE TABLE core.revision_validation (
 dataset_revision_id uuid PRIMARY KEY REFERENCES core.dataset_revision, validated_at timestamptz NOT NULL DEFAULT now(),
 input_digest text NOT NULL, verification_code_sha256 text NOT NULL, result jsonb NOT NULL
);
CREATE TABLE core.data_release (
 id uuid PRIMARY KEY, manifest_sha256 text NOT NULL UNIQUE, created_at timestamptz NOT NULL DEFAULT now(),
 release_kind text NOT NULL DEFAULT 'local_verified', manifest jsonb NOT NULL
);
CREATE TABLE core.release_dataset (
 release_id uuid NOT NULL REFERENCES core.data_release, dataset_id uuid NOT NULL, dataset_revision_id uuid NOT NULL,
 PRIMARY KEY(release_id,dataset_id), FOREIGN KEY(dataset_revision_id,dataset_id) REFERENCES core.dataset_revision(id,dataset_id)
);
CREATE TABLE core.active_release (
 singleton boolean PRIMARY KEY DEFAULT true CHECK(singleton), release_id uuid NOT NULL REFERENCES core.data_release,
 switched_at timestamptz NOT NULL DEFAULT now()
);
CREATE TABLE core.borehole_branch (
 id uuid PRIMARY KEY, borehole_id uuid NOT NULL REFERENCES core.borehole, parent_branch_id uuid,
 source_branch_id text, branch_depth_m double precision, evidence jsonb NOT NULL,
 UNIQUE(id,borehole_id), FOREIGN KEY(parent_branch_id,borehole_id) REFERENCES core.borehole_branch(id,borehole_id),
 CHECK(parent_branch_id IS NULL OR parent_branch_id<>id)
);
CREATE TABLE core.dataset_segment (
 id uuid PRIMARY KEY, dataset_revision_id uuid NOT NULL, borehole_id uuid NOT NULL, branch_id uuid,
 axis_id uuid NOT NULL, sample_no_from bigint NOT NULL, sample_no_to bigint NOT NULL, sequence_no integer, evidence jsonb NOT NULL,
 FOREIGN KEY(dataset_revision_id,borehole_id) REFERENCES core.dataset_revision(id,borehole_id),
 FOREIGN KEY(branch_id,borehole_id) REFERENCES core.borehole_branch(id,borehole_id),
 FOREIGN KEY(axis_id,dataset_revision_id) REFERENCES core.sample_axis(id,dataset_revision_id),
 FOREIGN KEY(axis_id,sample_no_from) REFERENCES core.scan_sample(axis_id,sample_no),
 FOREIGN KEY(axis_id,sample_no_to) REFERENCES core.scan_sample(axis_id,sample_no), CHECK(sample_no_from<=sample_no_to)
);
CREATE TABLE core.survey_station (
 id uuid PRIMARY KEY, borehole_id uuid NOT NULL REFERENCES core.borehole, branch_id uuid, survey_version text NOT NULL,
 md_m double precision NOT NULL, azimuth_deg double precision NOT NULL, inclination_deg double precision NOT NULL,
 angle_convention text NOT NULL, evidence jsonb NOT NULL,
 FOREIGN KEY(branch_id,borehole_id) REFERENCES core.borehole_branch(id,borehole_id), CHECK(azimuth_deg>=0 AND azimuth_deg<360)
);
CREATE FUNCTION core.reject_mutation() RETURNS trigger LANGUAGE plpgsql AS $$
BEGIN RAISE EXCEPTION 'Immutable prepared record: create a new revision instead' USING ERRCODE='55000'; END $$;
DO $$ DECLARE t text; BEGIN
 FOREACH t IN ARRAY ARRAY['borehole','borehole_revision','dataset','dataset_revision','sample_axis','scan_sample','core_interval',
 'spectral_stream','interpretation_set','interpretation_input','metric_definition','scan_log','log_axis_binding','asset','log_asset',
 'data_chunk','image_frame','source_document','source_reference','quality_issue','data_release','release_dataset','borehole_branch','dataset_segment','survey_station'] LOOP
 EXECUTE format('CREATE TRIGGER immutable_record BEFORE UPDATE OR DELETE ON core.%I FOR EACH ROW EXECUTE FUNCTION core.reject_mutation()',t);
 END LOOP;
END $$;
REVOKE ALL ON SCHEMA core FROM PUBLIC;
REVOKE ALL ON ALL TABLES IN SCHEMA core FROM PUBLIC;
REVOKE ALL ON ALL FUNCTIONS IN SCHEMA core FROM PUBLIC;
