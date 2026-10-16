-- Additional database-only review: reject relations that previously depended
-- on the import verifier. No scientific rows or existing columns are rewritten.
ALTER TABLE core.asset ADD CONSTRAINT asset_id_sha256_key UNIQUE (id,sha256);
ALTER TABLE core.asset_location ADD CONSTRAINT asset_location_content_identity_fkey
 FOREIGN KEY (asset_id,verified_sha256) REFERENCES core.asset(id,sha256);
ALTER TABLE core.image_frame ADD CONSTRAINT image_frame_log_asset_fkey
 FOREIGN KEY (log_id,asset_id) REFERENCES core.log_asset(log_id,asset_id);
ALTER TABLE core.data_chunk ADD CONSTRAINT data_chunk_log_asset_fkey
 FOREIGN KEY (log_id,asset_id) REFERENCES core.log_asset(log_id,asset_id);

-- Existing same-axis FKs establish existence. A statement trigger additionally
-- checks interval roles, membership and local positions. COPY is checked as one
-- set, avoiding an extra SQL lookup for each of hundreds of thousands of rows.
CREATE FUNCTION core.check_inserted_samples() RETURNS trigger
 LANGUAGE plpgsql SET search_path = pg_catalog,core AS $$
BEGIN
 IF EXISTS (
   SELECT 1 FROM inserted_samples s
   JOIN core.core_interval t ON t.id=s.tray_interval_id AND t.axis_id=s.axis_id
   JOIN core.core_interval c ON c.id=s.section_interval_id AND c.axis_id=s.axis_id
   JOIN core.sample_axis a ON a.id=s.axis_id
   WHERE t.interval_kind<>'tray' OR c.interval_kind<>'section'
      OR c.parent_interval_id IS DISTINCT FROM t.id
      OR s.sample_no NOT BETWEEN t.sample_no_from AND t.sample_no_to
      OR s.sample_no NOT BETWEEN c.sample_no_from AND c.sample_no_to
      OR s.tray_sample_no<>s.sample_no-t.sample_no_from+1
      OR s.section_sample_no<>s.sample_no-c.sample_no_from+1
      OR s.sample_no>=a.sample_count
      OR s.md_m NOT BETWEEN a.depth_min_m AND a.depth_max_m
 ) THEN
   RAISE EXCEPTION 'Sample interval kind, membership or position is inconsistent'
     USING ERRCODE='23514';
 END IF;
 RETURN NULL;
END $$;
CREATE TRIGGER validate_sample_relations AFTER INSERT ON core.scan_sample
 REFERENCING NEW TABLE AS inserted_samples FOR EACH STATEMENT
 EXECUTE FUNCTION core.check_inserted_samples();

CREATE FUNCTION core.check_inserted_intervals() RETURNS trigger
 LANGUAGE plpgsql SET search_path = pg_catalog,core AS $$
BEGIN
 IF EXISTS (
   SELECT 1 FROM inserted_intervals c
   JOIN core.core_interval p ON p.id=c.parent_interval_id AND p.axis_id=c.axis_id
   WHERE c.interval_kind='section' AND
     (p.interval_kind<>'tray' OR c.sample_no_from<p.sample_no_from OR c.sample_no_to>p.sample_no_to)
 ) THEN
   RAISE EXCEPTION 'Section must belong to a containing tray on the same axis'
     USING ERRCODE='23514';
 END IF;
 RETURN NULL;
END $$;
CREATE TRIGGER validate_interval_relations AFTER INSERT ON core.core_interval
 REFERENCING NEW TABLE AS inserted_intervals FOR EACH STATEMENT
 EXECUTE FUNCTION core.check_inserted_intervals();
REVOKE ALL ON FUNCTION core.check_inserted_samples() FROM PUBLIC;
REVOKE ALL ON FUNCTION core.check_inserted_intervals() FROM PUBLIC;

COMMENT ON CONSTRAINT asset_location_content_identity_fkey ON core.asset_location
 IS '副本声明的内容哈希必须与资产身份一致；不依赖外部接口校验。';
COMMENT ON TRIGGER validate_sample_relations ON core.scan_sample
 IS '批量校验样本的托盘、行段、轴范围和段内位置；允许重复孔深。';
