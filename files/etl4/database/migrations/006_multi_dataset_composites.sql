-- Source datasets remain independent; these tables describe a display selection.
ALTER TABLE core.release_dataset ADD CONSTRAINT release_dataset_exact_revision_key
 UNIQUE(release_id,dataset_id,dataset_revision_id);

CREATE TABLE core.dataset_succession (
 id uuid PRIMARY KEY,
 borehole_id uuid NOT NULL REFERENCES core.borehole,
 predecessor_revision_id uuid NOT NULL,
 successor_revision_id uuid NOT NULL,
 relation_kind text NOT NULL CHECK(relation_kind='confirmed_display_successor'),
 switch_md_m double precision NOT NULL CHECK(switch_md_m>=0 AND switch_md_m<'Infinity'::float8),
 switch_basis text NOT NULL CHECK(switch_basis='successor_first_actual_sample_md_m'),
 confirmation_basis jsonb NOT NULL,
 CHECK(predecessor_revision_id<>successor_revision_id),
 UNIQUE(predecessor_revision_id,successor_revision_id),
 FOREIGN KEY(predecessor_revision_id,borehole_id) REFERENCES core.dataset_revision(id,borehole_id),
 FOREIGN KEY(successor_revision_id,borehole_id) REFERENCES core.dataset_revision(id,borehole_id)
);

CREATE TABLE core.borehole_composite (
 id uuid PRIMARY KEY,
 release_id uuid NOT NULL REFERENCES core.data_release,
 borehole_id uuid NOT NULL REFERENCES core.borehole,
 composition_kind text NOT NULL CHECK(composition_kind IN ('single_dataset','confirmed_successor_chain')),
 boundary_rule text NOT NULL CHECK(boundary_rule='successor_first_actual_sample_md_m'),
 missing_policy text NOT NULL CHECK(missing_policy='preserve_no_fallback'),
 trajectory_kind text NOT NULL CHECK(trajectory_kind='display_composite_not_surveyed_path'),
 definition_sha256 text NOT NULL CHECK(definition_sha256 ~ '^[a-f0-9]{64}$'),
 evidence jsonb NOT NULL,
 UNIQUE(release_id,borehole_id), UNIQUE(id,release_id,borehole_id)
);

CREATE TABLE core.borehole_composite_part (
 composite_id uuid NOT NULL,
 release_id uuid NOT NULL,
 borehole_id uuid NOT NULL,
 sequence_no integer NOT NULL CHECK(sequence_no>=0),
 dataset_id uuid NOT NULL,
 dataset_revision_id uuid NOT NULL,
 axis_id uuid NOT NULL,
 predecessor_relation_id uuid REFERENCES core.dataset_succession,
 selection_from_md_m double precision NOT NULL CHECK(selection_from_md_m>=0 AND selection_from_md_m<'Infinity'::float8),
 selection_to_md_m double precision NOT NULL CHECK(selection_to_md_m>=0 AND selection_to_md_m<'Infinity'::float8),
 upper_inclusive boolean NOT NULL,
 sample_no_from bigint,
 sample_no_to bigint,
 selection_status text NOT NULL CHECK(selection_status IN ('selected','fully_superseded')),
 PRIMARY KEY(composite_id,sequence_no), UNIQUE(composite_id,dataset_id),
 CHECK(selection_to_md_m>=selection_from_md_m),
 CHECK((selection_status='selected' AND sample_no_from IS NOT NULL AND sample_no_to IS NOT NULL AND sample_no_from>=0 AND sample_no_from<=sample_no_to)
    OR (selection_status='fully_superseded' AND sample_no_from IS NULL AND sample_no_to IS NULL)),
 CHECK((sequence_no=0 AND predecessor_relation_id IS NULL) OR (sequence_no>0 AND predecessor_relation_id IS NOT NULL)),
 FOREIGN KEY(composite_id,release_id,borehole_id) REFERENCES core.borehole_composite(id,release_id,borehole_id),
 FOREIGN KEY(release_id,dataset_id,dataset_revision_id) REFERENCES core.release_dataset(release_id,dataset_id,dataset_revision_id),
 FOREIGN KEY(dataset_revision_id,borehole_id) REFERENCES core.dataset_revision(id,borehole_id),
 FOREIGN KEY(axis_id,dataset_revision_id) REFERENCES core.sample_axis(id,dataset_revision_id),
 FOREIGN KEY(axis_id,sample_no_from) REFERENCES core.scan_sample(axis_id,sample_no),
 FOREIGN KEY(axis_id,sample_no_to) REFERENCES core.scan_sample(axis_id,sample_no)
);
CREATE INDEX composite_part_axis_idx ON core.borehole_composite_part(axis_id,sample_no_from,sample_no_to);

CREATE FUNCTION core.check_composite_part() RETURNS trigger LANGUAGE plpgsql SET search_path=pg_catalog,core AS $$
DECLARE lo bigint; hi bigint; n bigint; r core.dataset_succession; previous_rev uuid;
BEGIN
 PERFORM pg_advisory_xact_lock(hashtextextended(NEW.composite_id::text,9));
 SELECT min(sample_no),max(sample_no),count(*) INTO lo,hi,n FROM core.scan_sample
 WHERE axis_id=NEW.axis_id AND md_m>=NEW.selection_from_md_m
 AND (md_m<NEW.selection_to_md_m OR (NEW.upper_inclusive AND md_m=NEW.selection_to_md_m));
 IF lo IS DISTINCT FROM NEW.sample_no_from OR hi IS DISTINCT FROM NEW.sample_no_to OR
    (n>0 AND (NEW.selection_status<>'selected' OR n<>hi-lo+1)) OR
    (n=0 AND NEW.selection_status<>'fully_superseded') THEN
  RAISE EXCEPTION 'Composite selection does not exactly match its source sample window';
 END IF;
 IF NEW.predecessor_relation_id IS NOT NULL THEN
  SELECT * INTO STRICT r FROM core.dataset_succession WHERE id=NEW.predecessor_relation_id;
  SELECT dataset_revision_id INTO previous_rev FROM core.borehole_composite_part
    WHERE composite_id=NEW.composite_id AND sequence_no=NEW.sequence_no-1;
  IF r.borehole_id<>NEW.borehole_id OR r.successor_revision_id<>NEW.dataset_revision_id OR
     previous_rev IS DISTINCT FROM r.predecessor_revision_id OR r.switch_md_m<>NEW.selection_from_md_m THEN
    RAISE EXCEPTION 'Composite successor relation differs from ordered source datasets';
  END IF;
 END IF;
 IF EXISTS(SELECT 1 FROM core.borehole_composite_part p
  WHERE p.composite_id=NEW.composite_id AND p.sequence_no<>NEW.sequence_no
    AND p.selection_status='selected' AND NEW.selection_status='selected'
    AND (p.selection_from_md_m<NEW.selection_to_md_m OR (NEW.upper_inclusive AND p.selection_from_md_m=NEW.selection_to_md_m))
    AND (NEW.selection_from_md_m<p.selection_to_md_m OR (p.upper_inclusive AND NEW.selection_from_md_m=p.selection_to_md_m))) THEN
  RAISE EXCEPTION 'Composite source selection windows overlap';
 END IF;
 RETURN NEW;
END $$;
CREATE TRIGGER composite_part_source_check BEFORE INSERT OR UPDATE ON core.borehole_composite_part
 FOR EACH ROW EXECUTE FUNCTION core.check_composite_part();

CREATE VIEW core.v_borehole_composite_samples AS
 SELECT p.composite_id,p.release_id,p.borehole_id,p.sequence_no,p.dataset_id,p.dataset_revision_id,
        s.axis_id,s.sample_no,s.md_m,s.tray_interval_id,s.section_interval_id
 FROM core.borehole_composite_part p JOIN core.scan_sample s
   ON s.axis_id=p.axis_id AND s.sample_no BETWEEN p.sample_no_from AND p.sample_no_to
 WHERE p.selection_status='selected';

CREATE VIEW core.v_borehole_dataset_full AS
 SELECT p.composite_id,p.release_id,p.borehole_id,p.sequence_no,p.dataset_id,p.dataset_revision_id,p.axis_id,
        d.source_dataset_id,r.source_dataset_name,a.sample_count,a.depth_min_m,a.depth_max_m,
        p.selection_status AS overview_selection_status
 FROM core.borehole_composite_part p JOIN core.dataset d ON d.id=p.dataset_id
 JOIN core.dataset_revision r ON r.id=p.dataset_revision_id JOIN core.sample_axis a ON a.id=p.axis_id;

COMMENT ON TABLE core.dataset_succession IS '用户确认的展示接替关系及其依据；不声称已恢复物理分支轨迹。';
COMMENT ON TABLE core.borehole_composite IS '发布版本内的单孔 3D 展示组合；原始 dataset、图像和样本完整保留。';
COMMENT ON TABLE core.borehole_composite_part IS '3D 取样窗口与来源样本闭区间；2D 用 axis_id 读取完整样本，不应用这里的裁选。';
COMMENT ON VIEW core.v_borehole_dataset_full IS '2D 多列共享真实孔深标尺的数据入口，保留每个 dataset 的完整范围与独立图像关联。';
