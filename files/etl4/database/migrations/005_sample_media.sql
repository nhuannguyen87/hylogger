-- Additive media grain. Existing releases, values and files remain immutable.
ALTER TABLE core.image_frame ADD UNIQUE(id,dataset_revision_id);
ALTER TABLE core.log_asset ADD UNIQUE(log_id,asset_id,dataset_revision_id);
CREATE TABLE core.spectral_array (
 id uuid PRIMARY KEY, dataset_revision_id uuid NOT NULL,
 log_id uuid NOT NULL, asset_id uuid NOT NULL, spectral_stream_id uuid NOT NULL,
 sample_count bigint NOT NULL CHECK(sample_count>0), channel_count integer NOT NULL CHECK(channel_count>0),
 dtype_code text NOT NULL CHECK(dtype_code='<f4'), matrix_order text NOT NULL CHECK(matrix_order='C'),
 data_offset_bytes bigint NOT NULL CHECK(data_offset_bytes>=0), sample_stride_bytes bigint NOT NULL,
 channel_stride_bytes integer NOT NULL CHECK(channel_stride_bytes=4),
 layout_status text NOT NULL CHECK(layout_status IN ('verified_sample_major','unconfirmed')),
 value_unit text, scaling_applied boolean NOT NULL CHECK(NOT scaling_applied),
 evidence_asset_id uuid NOT NULL, evidence jsonb NOT NULL,
 UNIQUE(id,dataset_revision_id), UNIQUE(log_id,asset_id),
 CHECK(sample_stride_bytes=channel_count::bigint*4),
 FOREIGN KEY(log_id,asset_id,dataset_revision_id) REFERENCES core.log_asset(log_id,asset_id,dataset_revision_id),
 FOREIGN KEY(spectral_stream_id,dataset_revision_id) REFERENCES core.spectral_stream(id,dataset_revision_id),
 FOREIGN KEY(evidence_asset_id,dataset_revision_id) REFERENCES core.asset(id,dataset_revision_id)
);
CREATE TABLE core.spectral_sample_map (
 id uuid PRIMARY KEY, dataset_revision_id uuid NOT NULL, spectral_array_id uuid NOT NULL, axis_id uuid NOT NULL,
 source_row_from bigint NOT NULL CHECK(source_row_from>=0), source_row_to bigint NOT NULL,
 sample_no_from bigint NOT NULL, sample_no_to bigint NOT NULL,
 mapping_kind text NOT NULL CHECK(mapping_kind='identity_range'), binding_status text NOT NULL CHECK(binding_status='verified'),
 evidence_asset_id uuid NOT NULL, basis text NOT NULL,
 CHECK(source_row_from<=source_row_to),CHECK(sample_no_from<=sample_no_to),
 CHECK(source_row_to-source_row_from=sample_no_to-sample_no_from),
 UNIQUE(spectral_array_id,source_row_from),
 FOREIGN KEY(spectral_array_id,dataset_revision_id) REFERENCES core.spectral_array(id,dataset_revision_id),
 FOREIGN KEY(axis_id,dataset_revision_id) REFERENCES core.sample_axis(id,dataset_revision_id),
 FOREIGN KEY(axis_id,sample_no_from) REFERENCES core.scan_sample(axis_id,sample_no),
 FOREIGN KEY(axis_id,sample_no_to) REFERENCES core.scan_sample(axis_id,sample_no),
 FOREIGN KEY(evidence_asset_id,dataset_revision_id) REFERENCES core.asset(id,dataset_revision_id)
);
CREATE TABLE core.spectral_block (
 spectral_array_id uuid NOT NULL REFERENCES core.spectral_array, block_no integer NOT NULL CHECK(block_no>=0),
 source_row_from bigint NOT NULL CHECK(source_row_from>=0), source_row_to_exclusive bigint NOT NULL,
 byte_offset bigint NOT NULL CHECK(byte_offset>=0), byte_length bigint NOT NULL CHECK(byte_length>0),
 sha256 text NOT NULL CHECK(sha256 ~ '^[a-f0-9]{64}$'), PRIMARY KEY(spectral_array_id,block_no),
 CHECK(source_row_to_exclusive>source_row_from), UNIQUE(spectral_array_id,source_row_from)
);
CREATE TABLE core.image_region (
 id uuid PRIMARY KEY, dataset_revision_id uuid NOT NULL, image_frame_id uuid NOT NULL,
 region_kind text NOT NULL CHECK(region_kind='core_row'), region_ordinal integer NOT NULL CHECK(region_ordinal>=0),
 x_px integer NOT NULL CHECK(x_px>=0), y_px integer NOT NULL CHECK(y_px>=0),
 width_px integer NOT NULL CHECK(width_px>0), height_px integer NOT NULL CHECK(height_px>0),
 valid_area jsonb, coordinate_basis text NOT NULL, region_status text NOT NULL CHECK(region_status IN ('candidate','reviewed','unavailable')),
 detection_method text NOT NULL, review_basis text NOT NULL, evidence_asset_id uuid NOT NULL,
 UNIQUE(id,dataset_revision_id), UNIQUE(image_frame_id,region_ordinal),
 FOREIGN KEY(image_frame_id,dataset_revision_id) REFERENCES core.image_frame(id,dataset_revision_id),
 FOREIGN KEY(evidence_asset_id,dataset_revision_id) REFERENCES core.asset(id,dataset_revision_id)
);
CREATE TABLE core.image_sample_mapping (
 id uuid PRIMARY KEY, dataset_revision_id uuid NOT NULL, image_region_id uuid NOT NULL, axis_id uuid NOT NULL,
 section_interval_id uuid NOT NULL, sample_no_from bigint NOT NULL, sample_no_to bigint NOT NULL,
 anchor_points jsonb, source_depth_direction text NOT NULL, direction_status text NOT NULL, direction_basis text NOT NULL,
 direction_evidence_asset_id uuid NOT NULL, mapping_level text NOT NULL, mapping_status text NOT NULL, mapping_method text NOT NULL,
 indicator_method text NOT NULL, indicator_version integer NOT NULL, indicator_status text NOT NULL,
 indicator_parameters jsonb NOT NULL, error_px double precision, error_depth_m double precision, evidence_asset_id uuid NOT NULL,
 UNIQUE(image_region_id,axis_id,sample_no_from), CHECK(sample_no_from<=sample_no_to),
 CHECK(error_px IS NULL OR (error_px>=0 AND error_px<'Infinity'::float8)),
 CHECK(error_depth_m IS NULL OR (error_depth_m>=0 AND error_depth_m<'Infinity'::float8)),
 CHECK(indicator_status IN ('approximate','unavailable','calibrated')),
 CHECK(indicator_method<>'sample_index_linear' OR
  (indicator_version=1 AND indicator_status='approximate' AND source_depth_direction='left_to_right'
   AND indicator_parameters='{"range_basis":"row_sample_interval","endpoint_rule":"first_last_pixel_center","single_sample_rule":"center","coordinate_space":"row_crop_pixels"}'::jsonb
   AND error_px IS NULL AND error_depth_m IS NULL)),
 FOREIGN KEY(image_region_id,dataset_revision_id) REFERENCES core.image_region(id,dataset_revision_id),
 FOREIGN KEY(axis_id,dataset_revision_id) REFERENCES core.sample_axis(id,dataset_revision_id),
 FOREIGN KEY(section_interval_id,axis_id) REFERENCES core.core_interval(id,axis_id),
 FOREIGN KEY(axis_id,sample_no_from) REFERENCES core.scan_sample(axis_id,sample_no),
 FOREIGN KEY(axis_id,sample_no_to) REFERENCES core.scan_sample(axis_id,sample_no),
 FOREIGN KEY(direction_evidence_asset_id,dataset_revision_id) REFERENCES core.asset(id,dataset_revision_id),
 FOREIGN KEY(evidence_asset_id,dataset_revision_id) REFERENCES core.asset(id,dataset_revision_id)
);
CREATE INDEX image_mapping_sample_idx ON core.image_sample_mapping(axis_id,section_interval_id,sample_no_from,sample_no_to);
CREATE TABLE core.image_region_asset (
 region_id uuid NOT NULL, dataset_revision_id uuid NOT NULL, asset_id uuid NOT NULL, log_id uuid NOT NULL,
 representation_kind text NOT NULL, width_px integer NOT NULL CHECK(width_px>0), height_px integer NOT NULL CHECK(height_px>0),
 transform jsonb NOT NULL, recipe_version integer NOT NULL CHECK(recipe_version>0),
 PRIMARY KEY(region_id,asset_id),
 FOREIGN KEY(region_id,dataset_revision_id) REFERENCES core.image_region(id,dataset_revision_id),
 FOREIGN KEY(log_id,asset_id,dataset_revision_id) REFERENCES core.log_asset(log_id,asset_id,dataset_revision_id)
);

CREATE FUNCTION core.check_media_insert() RETURNS trigger LANGUAGE plpgsql SET search_path=pg_catalog,core AS $$
DECLARE f core.image_frame; r core.image_region; s core.core_interval; a core.spectral_array; n bigint; st core.spectral_stream;
BEGIN
 IF TG_TABLE_NAME='image_region' THEN
  SELECT * INTO STRICT f FROM core.image_frame WHERE id=NEW.image_frame_id;
  PERFORM pg_advisory_xact_lock(hashtextextended(f.id::text,5));
  IF NEW.x_px::bigint+NEW.width_px>f.width_px OR NEW.y_px::bigint+NEW.height_px>f.height_px THEN
   RAISE EXCEPTION 'Region exceeds source image'; END IF;
  IF EXISTS(SELECT 1 FROM core.image_region z WHERE z.image_frame_id=f.id AND
   z.x_px<NEW.x_px::bigint+NEW.width_px AND NEW.x_px<z.x_px::bigint+z.width_px AND
   z.y_px<NEW.y_px::bigint+NEW.height_px AND NEW.y_px<z.y_px::bigint+z.height_px) THEN
   RAISE EXCEPTION 'Overlapping visible core rows'; END IF;
 ELSIF TG_TABLE_NAME='image_sample_mapping' THEN
  SELECT * INTO STRICT r FROM core.image_region WHERE id=NEW.image_region_id;
  SELECT * INTO STRICT f FROM core.image_frame WHERE id=r.image_frame_id;
  SELECT * INTO STRICT s FROM core.core_interval WHERE id=NEW.section_interval_id;
  PERFORM pg_advisory_xact_lock(hashtextextended(r.id::text,6));
  IF f.axis_id<>NEW.axis_id OR s.interval_kind<>'section' OR s.parent_interval_id<>f.core_interval_id OR
    NEW.sample_no_from<s.sample_no_from OR NEW.sample_no_to>s.sample_no_to THEN
   RAISE EXCEPTION 'Image mapping crosses source tray, section or axis'; END IF;
  IF (SELECT count(*) FROM core.scan_sample WHERE axis_id=NEW.axis_id AND sample_no BETWEEN NEW.sample_no_from AND NEW.sample_no_to
     AND section_interval_id=NEW.section_interval_id)<>NEW.sample_no_to-NEW.sample_no_from+1 THEN
   RAISE EXCEPTION 'Image mapping crosses a missing or differently bound sample'; END IF;
  IF NEW.indicator_method='sample_index_linear' AND (NEW.sample_no_from<>s.sample_no_from OR NEW.sample_no_to<>s.sample_no_to OR
     EXISTS(SELECT 1 FROM core.image_sample_mapping WHERE image_region_id=r.id)) THEN
   RAISE EXCEPTION 'Index indicator requires one complete contiguous row range'; END IF;
 ELSIF TG_TABLE_NAME='image_region_asset' THEN
  SELECT * INTO STRICT r FROM core.image_region WHERE id=NEW.region_id;
  SELECT * INTO STRICT f FROM core.image_frame WHERE id=r.image_frame_id;
  IF f.log_id<>NEW.log_id THEN RAISE EXCEPTION 'Derived row has a different source image log'; END IF;
  IF NEW.representation_kind='native_lossless_png' AND (NEW.width_px<>r.width_px OR NEW.height_px<>r.height_px OR
   (NEW.transform->'crop_box_half_open') IS DISTINCT FROM jsonb_build_array(r.x_px,r.y_px,r.x_px+r.width_px,r.y_px+r.height_px) OR
   (NEW.transform->>'source_sha256') IS DISTINCT FROM (SELECT sha256 FROM core.asset WHERE id=f.asset_id)) THEN
   RAISE EXCEPTION 'Crop recipe or source content differs from region'; END IF;
 ELSIF TG_TABLE_NAME='spectral_array' THEN
  SELECT * INTO STRICT st FROM core.spectral_stream WHERE id=NEW.spectral_stream_id;
  IF st.wavelength_count<>NEW.channel_count OR
   (SELECT spectral_stream_id FROM core.scan_log WHERE id=NEW.log_id) IS DISTINCT FROM NEW.spectral_stream_id THEN
   RAISE EXCEPTION 'Spectrum wavelength/log mismatch'; END IF;
  SELECT byte_size INTO STRICT n FROM core.asset WHERE id=NEW.asset_id;
  IF n<>NEW.data_offset_bytes+NEW.sample_count*NEW.sample_stride_bytes THEN RAISE EXCEPTION 'Spectral byte extent differs'; END IF;
 ELSIF TG_TABLE_NAME='spectral_block' THEN
  SELECT * INTO STRICT a FROM core.spectral_array WHERE id=NEW.spectral_array_id;
  PERFORM pg_advisory_xact_lock(hashtextextended(a.id::text,7));
  IF NEW.source_row_to_exclusive>a.sample_count OR NEW.byte_offset<>a.data_offset_bytes+NEW.source_row_from*a.sample_stride_bytes OR
   NEW.byte_length<>(NEW.source_row_to_exclusive-NEW.source_row_from)*a.sample_stride_bytes THEN
   RAISE EXCEPTION 'Invalid spectral block byte range'; END IF;
  IF EXISTS(SELECT 1 FROM core.spectral_block b WHERE b.spectral_array_id=a.id AND b.source_row_from<NEW.source_row_to_exclusive AND NEW.source_row_from<b.source_row_to_exclusive) THEN
   RAISE EXCEPTION 'Overlapping spectral blocks'; END IF;
 ELSIF TG_TABLE_NAME='spectral_sample_map' THEN
  SELECT * INTO STRICT a FROM core.spectral_array WHERE id=NEW.spectral_array_id;
  PERFORM pg_advisory_xact_lock(hashtextextended(a.id::text,8));
  IF NEW.source_row_to>=a.sample_count OR a.layout_status<>'verified_sample_major' OR
   NOT EXISTS(SELECT 1 FROM core.log_axis_binding WHERE log_id=a.log_id AND axis_id=NEW.axis_id AND status='verified') THEN
   RAISE EXCEPTION 'Unverified or invalid spectral axis'; END IF;
  IF (SELECT count(*) FROM core.scan_sample WHERE axis_id=NEW.axis_id AND sample_no BETWEEN NEW.sample_no_from AND NEW.sample_no_to)<>NEW.sample_no_to-NEW.sample_no_from+1 THEN
   RAISE EXCEPTION 'Spectral mapping crosses a sample gap'; END IF;
  IF EXISTS(SELECT 1 FROM core.spectral_sample_map m WHERE m.spectral_array_id=a.id AND
   (m.source_row_from<=NEW.source_row_to AND NEW.source_row_from<=m.source_row_to OR
    m.axis_id=NEW.axis_id AND m.sample_no_from<=NEW.sample_no_to AND NEW.sample_no_from<=m.sample_no_to)) THEN
   RAISE EXCEPTION 'Ambiguous spectral mapping'; END IF;
 END IF;
 RETURN NEW;
END $$;
DO $$ DECLARE t text; BEGIN
 FOREACH t IN ARRAY ARRAY['spectral_array','spectral_sample_map','spectral_block','image_region','image_sample_mapping','image_region_asset'] LOOP
  EXECUTE format('CREATE TRIGGER validate_media BEFORE INSERT ON core.%I FOR EACH ROW EXECUTE FUNCTION core.check_media_insert()',t);
  EXECUTE format('CREATE TRIGGER immutable_record BEFORE UPDATE OR DELETE ON core.%I FOR EACH ROW EXECUTE FUNCTION core.reject_mutation()',t);
 END LOOP;
END $$;
REVOKE ALL ON ALL FUNCTIONS IN SCHEMA core FROM PUBLIC;
