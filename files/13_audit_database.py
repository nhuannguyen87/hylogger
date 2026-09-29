"""Database-only review: source mapping, relations, schema reproduction and rollback probes.

The live database is read only. Synthetic invalid rows go to a disposable test
database and every probe rolls back. This module neither imports nor calls API code.
"""
import argparse
from datetime import datetime, date
import math
from _db_common import *
from _ingest import input_digest
from _trial_support import create_trial, drop_trial, trial_schema, fingerprints


def require(ok, message):
    if not ok:
        raise ValueError(message)


def comparable(value):
    if isinstance(value, uuid.UUID):
        return str(value)
    return value


def compare_fields(actual, source, context):
    """Compare every promoted field present in a source object, including SQL NULL."""
    checked = 0
    for key in actual.keys() & source.keys():
        a, b = actual[key], source[key]
        if isinstance(a, datetime) and isinstance(b, str):
            b = datetime.fromisoformat(b.replace('Z', '+00:00'))
        elif isinstance(a, date) and isinstance(b, str):
            b = date.fromisoformat(b)
        require(comparable(a) == comparable(b), f'{context}.{key} differs from prepared source')
        checked += 1
    return checked


def source_checks(conn):
    results = []
    # prepared_holes also checks all locked prepared-file SHA256 values.
    for h in prepared_holes():
        m = h['metadata']; rev = m['dataset_revision']['id']; count = 0
        for table, expected, key in (
            ('borehole', m['borehole'], 'id'),
            ('borehole_revision', {**m['borehole'], 'id': m['borehole']['metadata_revision_id'], 'borehole_id': m['borehole']['id'], 'normalized_metadata': m['borehole']}, 'id'),
            ('dataset', m['dataset'], 'id'),
            ('dataset_revision', {**m['dataset'], **m['dataset_revision'], 'borehole_revision_id': m['borehole']['metadata_revision_id'], 'normalized_metadata': m['dataset_revision'], 'input_digest': input_digest(h), 'mapping_version': 1}, 'id'),
        ):
            actual = conn.execute(sql.SQL('SELECT * FROM core.{} WHERE {}=%s').format(sql.Identifier(table), sql.Identifier(key)), (expected[key],)).fetchone()
            require(actual is not None, f'{table} missing')
            count += compare_fields(actual, expected, table)
        geometry = conn.execute('SELECT ST_AsGeoJSON(collar_geom,15)::jsonb AS g FROM core.borehole_revision WHERE id=%s', (m['borehole']['metadata_revision_id'],)).fetchone()['g']
        require(geometry == m['borehole']['collar_geometry'], 'Collar coordinates changed')
        for table, values, key in (
            ('scan_log', m['logs'], 'id'), ('metric_definition', m['metric_definitions'], 'metric_key'),
            ('spectral_stream', m['spectral_streams'], 'id'), ('interpretation_set', m['interpretation_sets'], 'id'),
        ):
            rows = conn.execute(sql.SQL('SELECT * FROM core.{} WHERE dataset_revision_id=%s').format(sql.Identifier(table)), (rev,)).fetchall()
            require(len(rows) == len(values), f'{table} count differs')
            bykey = {str(r[key]): r for r in rows}
            for source in values:
                actual = bykey[str(source[key])]
                count += compare_fields(actual, source, table)
                if table == 'scan_log':
                    require(actual['normalized_metadata'] == source, 'Full log metadata differs')
                    require(actual['per_sample_publication_allowed'] == source.get('per_sample_publication_allowed', False), 'Spectrum capability flag differs')
        by_source_id = {l['source_log_id']: l for l in m['logs']}
        for ref in conn.execute('SELECT * FROM core.source_reference WHERE dataset_revision_id=%s', (rev,)):
            matches = [r for r in m['source_references'] if r['role'] == ref['role'] and r['source_target_id'] == ref['source_target_id']]
            require(len(matches) == 1, 'Unexpected source reference')
            count += compare_fields(ref, matches[0], 'source_reference')
            wanted = by_source_id[ref['source_target_id']]['id'] if ref['status'] == 'resolved' else None
            require(comparable(ref['target_log_id']) == wanted, 'Source target mapping differs')
        for doc in conn.execute('SELECT * FROM core.source_document WHERE dataset_revision_id=%s', (rev,)):
            if doc['document_name'] in m['raw_metadata']:
                require(doc['content_json'] == m['raw_metadata'][doc['document_name']], 'Raw JSON document differs')
            else:
                source = inside(ETL, ETL/h['summary']['hole_id']/doc['document_name'])
                require(doc['content_text'] == source.read_text(encoding='utf-8-sig'), 'Source text document differs')
        # Explicitly verify fields omitted by the earlier representative image checks.
        for frame in read(h['folder']/'4_image_frames.json'):
            actual = conn.execute('''SELECT f.*,a.logical_path,l.source_log_id FROM core.image_frame f
                JOIN core.asset a ON a.id=f.asset_id JOIN core.scan_log l ON l.id=f.log_id WHERE f.id=%s''', (frame['id'],)).fetchone()
            count += compare_fields(actual, frame, 'image_frame')
            require(actual['logical_path'] == frame['source_path'], 'Image points to a different source file')
        results.append({'hole_id': h['summary']['hole_id'], 'typed_fields_compared': count, 'status': 'passed'})
    return results


RELATION_CHECKS = {
    'axis_count_and_extent': '''SELECT count(*) AS n FROM (SELECT a.id FROM core.sample_axis a LEFT JOIN core.scan_sample s ON s.axis_id=a.id
        GROUP BY a.id HAVING a.sample_count<>count(s.*) OR min(s.sample_no)<>0 OR max(s.sample_no)<>a.sample_count-1
        OR a.depth_min_m<>min(s.md_m) OR a.depth_max_m<>max(s.md_m)) q''',
    'sample_interval_membership': '''SELECT count(*) AS n FROM core.scan_sample s JOIN core.core_interval t ON t.id=s.tray_interval_id
        JOIN core.core_interval c ON c.id=s.section_interval_id WHERE t.interval_kind<>'tray' OR c.interval_kind<>'section'
        OR c.parent_interval_id<>t.id OR s.sample_no NOT BETWEEN t.sample_no_from AND t.sample_no_to
        OR s.sample_no NOT BETWEEN c.sample_no_from AND c.sample_no_to OR s.tray_sample_no<>s.sample_no-t.sample_no_from+1
        OR s.section_sample_no<>s.sample_no-c.sample_no_from+1''',
    'section_parent_and_extent': '''SELECT count(*) AS n FROM core.core_interval c JOIN core.core_interval p ON p.id=c.parent_interval_id
        WHERE p.interval_kind<>'tray' OR c.sample_no_from<p.sample_no_from OR c.sample_no_to>p.sample_no_to''',
    'interval_depth_extent': '''SELECT count(*) AS n FROM (SELECT i.id FROM core.core_interval i JOIN core.scan_sample s
        ON s.axis_id=i.axis_id AND s.sample_no BETWEEN i.sample_no_from AND i.sample_no_to GROUP BY i.id
        HAVING min(s.md_m) IS DISTINCT FROM i.observed_depth_min_m OR max(s.md_m) IS DISTINCT FROM i.observed_depth_max_m) q''',
    'chunk_dense_axis_binding': '''SELECT count(*) AS n FROM core.data_chunk c JOIN core.scan_log l ON l.id=c.log_id
        LEFT JOIN core.log_axis_binding b ON b.log_id=c.log_id WHERE c.coordinate_kind IN ('point_depth_m','sample_index')
        AND (b.axis_id IS DISTINCT FROM c.axis_id OR b.status IS DISTINCT FROM 'verified')''',
    'chunk_interval_axis_binding': '''SELECT count(*) AS n FROM core.data_chunk c JOIN core.scan_log l ON l.id=c.log_id
        WHERE c.coordinate_kind='sample_index_closed_interval' AND NOT EXISTS(SELECT 1 FROM core.core_interval i
        WHERE i.axis_id=c.axis_id AND i.source_log_id=l.source_log_id)''',
    'chunk_asset_log_relation': '''SELECT count(*) AS n FROM core.data_chunk c LEFT JOIN core.log_asset a
        ON (a.log_id,a.asset_id)=(c.log_id,c.asset_id) WHERE c.log_id IS NOT NULL AND (a.log_id IS NULL OR a.role<>'canonical')''',
    'chunk_row_sequence': '''SELECT count(*) AS n FROM (SELECT *,lag(source_row_to_exclusive) OVER(PARTITION BY asset_id ORDER BY row_group) previous_end,
        row_number() OVER(PARTITION BY asset_id ORDER BY row_group)-1 expected_group FROM core.data_chunk) c
        WHERE row_group<>expected_group OR (row_group=0 AND source_row_from<>0) OR (row_group>0 AND source_row_from<>previous_end)''',
    'chunk_scalar_row_count': '''SELECT count(*) AS n FROM (SELECT c.asset_id,l.observed_row_count FROM core.data_chunk c JOIN core.scan_log l ON l.id=c.log_id
        WHERE l.log_kind='scalar' GROUP BY c.asset_id,l.observed_row_count HAVING sum(c.row_count) IS DISTINCT FROM l.observed_row_count) q''',
    'chunk_profile_row_count': '''SELECT count(*) AS n FROM (SELECT c.asset_id,a.sample_count FROM core.data_chunk c JOIN core.scan_log l ON l.id=c.log_id
        JOIN core.sample_axis a ON a.id=c.axis_id WHERE l.log_kind='profile' GROUP BY c.asset_id,a.sample_count
        HAVING sum(c.row_count)<>a.sample_count) q''',
    'image_asset_and_tray_relation': '''SELECT count(*) AS n FROM core.image_frame i LEFT JOIN core.log_asset a ON (a.log_id,a.asset_id)=(i.log_id,i.asset_id)
        JOIN core.core_interval t ON t.id=i.core_interval_id JOIN core.scan_log l ON l.id=i.log_id
        WHERE a.log_id IS NULL OR t.interval_kind<>'tray' OR i.source_tray_label<>t.source_label OR l.log_kind<>'image' ''',
    'location_content_identity': '''SELECT count(*) AS n FROM core.asset_location l JOIN core.asset a ON a.id=l.asset_id WHERE l.verified_sha256<>a.sha256''',
    'log_has_explicit_binding_status': '''SELECT count(*) AS n FROM core.scan_log l LEFT JOIN core.log_axis_binding b ON b.log_id=l.id WHERE b.log_id IS NULL''',
    'unavailable_logs_have_no_payload': '''SELECT count(*) AS n FROM core.scan_log l JOIN core.log_asset a ON a.log_id=l.id WHERE l.availability_status='metadata_only' ''',
    'present_logs_have_source_payload': '''SELECT count(*) AS n FROM core.scan_log l WHERE l.availability_status='payload_present'
        AND NOT EXISTS(SELECT 1 FROM core.log_asset a WHERE a.log_id=l.id AND a.role='raw_source')''',
    'weight_not_probability': '''SELECT count(*) AS n FROM core.scan_log l JOIN core.metric_definition m
        ON (m.dataset_revision_id,m.metric_key)=(l.dataset_revision_id,l.metric_key) WHERE l.metric_code='mineral_weight' AND m.is_probability IS DISTINCT FROM false''',
    'unconfirmed_spectra_not_marked_usable': '''SELECT count(*) AS n FROM core.scan_log l
      WHERE l.log_kind='spectral' AND l.per_sample_publication_allowed AND
      (l.array_layout_status IS DISTINCT FROM 'verified_sample_major' OR NOT EXISTS
       (SELECT 1 FROM core.log_axis_binding b WHERE b.log_id=l.id AND b.status='verified' AND b.axis_id IS NOT NULL))''',
    'valid_collar_geometry': '''SELECT count(*) AS n FROM core.borehole_revision WHERE collar_geom IS NOT NULL
        AND (ST_IsEmpty(collar_geom) OR NOT ST_IsValid(collar_geom) OR ST_X(collar_geom) NOT BETWEEN -180 AND 180 OR ST_Y(collar_geom) NOT BETWEEN -90 AND 90)''',
    'unvalidated_constraints': "SELECT count(*) AS n FROM pg_constraint WHERE connamespace='core'::regnamespace AND NOT convalidated",
    'invalid_indexes': "SELECT count(*) AS n FROM pg_index i JOIN pg_class c ON c.oid=i.indrelid WHERE c.relnamespace='core'::regnamespace AND (NOT i.indisvalid OR NOT i.indisready)",
    'disabled_triggers': "SELECT count(*) AS n FROM pg_trigger t JOIN pg_class c ON c.oid=t.tgrelid WHERE c.relnamespace='core'::regnamespace AND t.tgenabled<>'O'",
}


def foreign_key_checks(conn):
    keys = conn.execute('''SELECT c.conname,src.relname src,dst.relname dst,c.confmatchtype,
        ARRAY(SELECT attname FROM unnest(c.conkey) WITH ORDINALITY k(att,ord) JOIN pg_attribute a ON a.attrelid=c.conrelid AND a.attnum=k.att ORDER BY ord) sc,
        ARRAY(SELECT attname FROM unnest(c.confkey) WITH ORDINALITY k(att,ord) JOIN pg_attribute a ON a.attrelid=c.confrelid AND a.attnum=k.att ORDER BY ord) dc
        FROM pg_constraint c JOIN pg_class src ON src.oid=c.conrelid JOIN pg_class dst ON dst.oid=c.confrelid
        WHERE c.connamespace='core'::regnamespace AND c.contype='f' ORDER BY src.relname,c.conname''').fetchall()
    for k in keys:
        require(k['confmatchtype'] == 's', 'New FK match semantics need review')
        nonnull = sql.SQL(' AND ').join(sql.SQL('s.{} IS NOT NULL').format(sql.Identifier(x)) for x in k['sc'])
        joined = sql.SQL(' AND ').join(sql.SQL('s.{}=d.{}').format(sql.Identifier(a), sql.Identifier(b)) for a,b in zip(k['sc'], k['dc']))
        query = sql.SQL('SELECT count(*) n FROM core.{} s WHERE {} AND NOT EXISTS(SELECT 1 FROM core.{} d WHERE {})').format(sql.Identifier(k['src']), nonnull, sql.Identifier(k['dst']), joined)
        require(conn.execute(query).fetchone()['n'] == 0, f'Orphan FK: {k["conname"]}')
    return len(keys)


def schema_signature(conn):
    return {
        'columns': conn.execute('''SELECT c.table_name,c.column_name,c.data_type,c.udt_name,c.is_nullable,c.column_default,
            format_type(a.atttypid,a.atttypmod) exact_type,c.is_identity,c.is_generated,c.generation_expression
            FROM information_schema.columns c JOIN pg_namespace n ON n.nspname=c.table_schema
            JOIN pg_class r ON r.relnamespace=n.oid AND r.relname=c.table_name
            JOIN pg_attribute a ON a.attrelid=r.oid AND a.attname=c.column_name
            WHERE c.table_schema='core' ORDER BY c.table_name,c.ordinal_position''').fetchall(),
        'constraints': conn.execute("SELECT r.relname,c.conname,pg_get_constraintdef(c.oid) definition FROM pg_constraint c JOIN pg_class r ON r.oid=c.conrelid WHERE c.connamespace='core'::regnamespace ORDER BY 1,2").fetchall(),
        'indexes': conn.execute("SELECT tablename,indexname,indexdef FROM pg_indexes WHERE schemaname='core' ORDER BY 1,2").fetchall(),
        'triggers': conn.execute("SELECT c.relname,t.tgname,pg_get_triggerdef(t.oid) definition FROM pg_trigger t JOIN pg_class c ON c.oid=t.tgrelid WHERE c.relnamespace='core'::regnamespace AND NOT t.tgisinternal ORDER BY 1,2").fetchall(),
        'functions': conn.execute("SELECT p.proname,pg_get_functiondef(p.oid) definition FROM pg_proc p WHERE p.pronamespace='core'::regnamespace ORDER BY 1").fetchall(),
    }


def rollback_probe(conn, name, query, params=(), should_reject=True):
    try:
        with conn.transaction() as tx:
            if callable(query):
                query()
            else:
                conn.execute(query, params)
            conn.execute('SET CONSTRAINTS ALL IMMEDIATE')
            # Roll back accepted bad fixtures too; never retain diagnostic records.
            raise psycopg.Rollback(tx)
    except psycopg.Error as exc:
        return {'name': name, 'database_rejected': True, 'sqlstate': exc.sqlstate, 'should_reject':should_reject, 'passed':should_reject}
    return {'name': name, 'database_rejected': False, 'sqlstate': None, 'should_reject':should_reject, 'passed':not should_reject}


def sample_fixture(conn, revision, alteration=None, repeated=False):
    """A fresh axis isolates each failure from primary-key/endpoint collisions."""
    axis,tray,section = [str(uuid.uuid4()) for _ in range(3)]
    n = 2 if repeated else 1
    insert(conn,'sample_axis',{'id':axis,'dataset_revision_id':revision,'sample_count':n,'depth_min_m':10.,'depth_max_m':10.,'alignment_evidence':{}})
    for iid,kind,parent in ((tray,'tray',None),(section,'section',tray)):
        insert(conn,'core_interval',{'id':iid,'axis_id':axis,'interval_kind':kind,'ordinal':0,'source_label':'0001','parent_interval_id':parent,
            'sample_no_from':0,'sample_no_to':n-1,'observed_depth_min_m':10.,'observed_depth_max_m':10.,'source_log_id':'fixture','source_data_row':0,'source_interval_convention':'closed'})
    if alteration == 'parent':
        another_tray=str(uuid.uuid4())
        insert(conn,'core_interval',{'id':another_tray,'axis_id':axis,'interval_kind':'tray','ordinal':1,'source_label':'0002',
            'sample_no_from':0,'sample_no_to':0,'source_log_id':'fixture','source_data_row':1,'source_interval_convention':'closed'})
        tray=another_tray
    if alteration == 'kind':
        tray,section=section,tray
    # Real bulk COPY exercises the transition-table trigger as well as INSERT.
    with conn.cursor().copy('COPY core.scan_sample(axis_id,sample_no,md_m,tray_interval_id,section_interval_id,tray_sample_no,section_sample_no) FROM STDIN') as copy:
        for i in range(n):
            copy.write_row((axis,i,10.,tray,section,2 if alteration=='offset' else i+1,i+1))


def constraint_probes(conn):
    b,d,r,axis,tray,section,log,asset = [str(uuid.uuid4()) for _ in range(8)]
    with conn.transaction():
        insert(conn,'borehole',{'id':b,'provider_code':'synthetic_audit','source_hole_id':'fixture'})
        insert(conn,'borehole_revision',{'id':r,'borehole_id':b,'source_name':'fixture','actual_drill_date_precision':'unconfirmed','trajectory_status':'unavailable','normalized_metadata':{}})
        insert(conn,'dataset',{'id':d,'borehole_id':b,'source_dataset_id':'fixture'})
        insert(conn,'dataset_revision',{'id':r,'dataset_id':d,'borehole_id':b,'borehole_revision_id':r,'source_snapshot_id':'fixture','pipeline_id':'fixture','input_digest':'fixture','mapping_version':1,'normalized_metadata':{}})
        insert(conn,'sample_axis',{'id':axis,'dataset_revision_id':r,'sample_count':1,'depth_min_m':10.,'depth_max_m':10.,'alignment_evidence':{}})
        for iid,kind,parent in ((tray,'tray',None),(section,'section',tray)):
            insert(conn,'core_interval',{'id':iid,'axis_id':axis,'interval_kind':kind,'ordinal':0,'source_label':'0001','parent_interval_id':parent,
                'sample_no_from':0,'sample_no_to':0,'observed_depth_min_m':10.,'observed_depth_max_m':10.,'source_log_id':'fixture','source_data_row':0,'source_interval_convention':'closed'})
        insert(conn,'scan_sample',{'axis_id':axis,'sample_no':0,'md_m':10.,'tray_interval_id':tray,'section_interval_id':section,'tray_sample_no':1,'section_sample_no':1})
        insert(conn,'scan_log',{'id':log,'dataset_revision_id':r,'source_log_id':'fixture','source_log_name':'fixture','log_kind':'image','availability_status':'payload_present','definition_status':'unconfirmed','source_metadata_file':'fixture','normalized_metadata':{}})
        insert(conn,'asset',{'id':asset,'dataset_revision_id':r,'asset_kind':'fixture','representation':'raw','logical_path':'fixture','sha256':'a'*64,'byte_size':1,'media_type':'application/octet-stream','semantic_status':'fixture','provenance':{}})
    probes = [
        rollback_probe(conn,'duplicate_sample', 'INSERT INTO core.scan_sample SELECT * FROM core.scan_sample WHERE axis_id=%s',(axis,)),
        rollback_probe(conn,'sample_nan_depth', 'INSERT INTO core.scan_sample SELECT axis_id,1,%s,tray_interval_id,section_interval_id,1,1,NULL FROM core.scan_sample WHERE axis_id=%s', (float('nan'),axis)),
        rollback_probe(conn,'sample_wrong_interval_kind',lambda: sample_fixture(conn,r,alteration='kind')),
        rollback_probe(conn,'sample_wrong_parent_tray',lambda: sample_fixture(conn,r,alteration='parent')),
        rollback_probe(conn,'sample_wrong_local_offset',lambda: sample_fixture(conn,r,alteration='offset')),
        rollback_probe(conn,'valid_repeated_depth_bulk_copy',lambda: sample_fixture(conn,r,repeated=True),should_reject=False),
        rollback_probe(conn,'location_checksum_mismatch', '''INSERT INTO core.asset_location(id,asset_id,backend,root_key,object_key,access_status,verified_sha256)
            VALUES (%s,%s,'local','etl4','fixture','verified_local',%s)''',(str(uuid.uuid4()),asset,'b'*64)),
        rollback_probe(conn,'location_path_traversal', '''INSERT INTO core.asset_location(id,asset_id,backend,root_key,object_key,access_status,verified_sha256)
            VALUES (%s,%s,'local','etl4','../outside','verified_local',%s)''',(str(uuid.uuid4()),asset,'a'*64)),
        rollback_probe(conn,'image_asset_not_linked_to_log', '''INSERT INTO core.image_frame(id,dataset_revision_id,axis_id,core_interval_id,log_id,asset_id,image_kind,image_ordinal,source_tray_label,
            width_px,height_px,depth_from_m,depth_to_m,depth_from_difference_m,depth_to_difference_m,depth_comparison_tolerance_m,association_basis)
            VALUES (%s,%s,%s,%s,%s,%s,'thumbnail',0,'0001',1,1,10,10,0,0,0.01,'synthetic')''',(str(uuid.uuid4()),r,axis,tray,log,asset)),
        rollback_probe(conn,'chunk_asset_not_linked_to_log', '''INSERT INTO core.data_chunk(asset_id,dataset_revision_id,axis_id,log_id,row_group,source_row_from,source_row_to_exclusive,row_count,coordinate_kind,coordinate_min,coordinate_max,logical_sha256)
            VALUES (%s,%s,%s,%s,0,0,1,1,'sample_index',0,0,%s)''',(asset,r,axis,log,'a'*64)),
    ]
    def second_dataset():
        d2,r2 = str(uuid.uuid4()),str(uuid.uuid4())
        insert(conn,'dataset',{'id':d2,'borehole_id':b,'source_dataset_id':'second_fixture'})
        insert(conn,'dataset_revision',{'id':r2,'dataset_id':d2,'borehole_id':b,'borehole_revision_id':r,'source_snapshot_id':'fixture','pipeline_id':'fixture','input_digest':'fixture','mapping_version':1,'normalized_metadata':{}})
        sample_fixture(conn,r2)
    probes.append(rollback_probe(conn,'valid_same_hole_second_dataset_overlapping_depth',second_dataset,should_reject=False))
    probes.append(rollback_probe(conn,'section_parent_is_not_tray', '''INSERT INTO core.core_interval(id,axis_id,interval_kind,ordinal,source_label,parent_interval_id,
        sample_no_from,sample_no_to,source_log_id,source_data_row,source_interval_convention)
        VALUES (%s,%s,'section',1,'0002',%s,0,0,'fixture',1,'closed')''',(str(uuid.uuid4()),axis,section)))
    return probes


def audit(label):
    result = {'status':'running','scope':'database only; no API calls, no cloud operations','code_sha256':sha(Path(__file__))}
    with connection('reader') as conn, conn.transaction():
        conn.execute('SET TRANSACTION ISOLATION LEVEL REPEATABLE READ READ ONLY')
        assert_schema(conn)
        result['release_id'] = str(conn.execute('SELECT release_id FROM core.active_release WHERE singleton').fetchone()['release_id'])
        result['schema'] = schema_signature(conn)
        result['source_checks'] = source_checks(conn)
        result['relation_checks'] = {}
        for name,query in RELATION_CHECKS.items():
            n = conn.execute(query).fetchone()['n']
            result['relation_checks'][name] = {'violations':n}
            require(n == 0, f'{name}: {n} violations')
        result['foreign_keys_checked'] = foreign_key_checks(conn)
        result['tables'] = []
        for row in conn.execute("SELECT tablename FROM pg_tables WHERE schemaname='core' ORDER BY tablename").fetchall():
            t = row['tablename']
            result['tables'].append({'table':t,'rows':conn.execute(sql.SQL('SELECT count(*) n FROM core.{}').format(sql.Identifier(t))).fetchone()['n'],
                'bytes':conn.execute('SELECT pg_total_relation_size(%s::regclass) n',('core.'+t,)).fetchone()['n']})
        result['stream_summary'] = conn.execute('SELECT region_code,wavelength_unit,wavelength_count,count(*) FROM core.spectral_stream GROUP BY 1,2,3 ORDER BY 1').fetchall()
        result['variant_summary'] = conn.execute('SELECT algorithm_family,output_region,variant_code,count(*) FROM core.interpretation_set GROUP BY 1,2,3 ORDER BY 1,2,3').fetchall()
        result['weight_logs_checked'] = conn.execute("SELECT count(*) n FROM core.scan_log WHERE metric_code='mineral_weight'").fetchone()['n']
        require(result['weight_logs_checked'] == 62, 'Approved five-hole weight-log scope changed')
        for s in conn.execute('SELECT * FROM core.spectral_stream'):
            require(len(s['wavelengths'])==s['wavelength_count'] and all(math.isfinite(v) and v>0 for v in s['wavelengths']), 'Invalid wavelength axis')
            require(all(a<b for a,b in zip(s['wavelengths'],s['wavelengths'][1:])), 'Nonascending wavelength axis')
        for release in conn.execute('SELECT * FROM core.data_release'):
            require(digest(release['manifest'])==release['manifest_sha256'], 'Release manifest digest differs')
            rows=conn.execute('SELECT dataset_id,dataset_revision_id FROM core.release_dataset WHERE release_id=%s',(release['id'],)).fetchall()
            require({(str(r['dataset_id']),str(r['dataset_revision_id'])) for r in rows}=={(r['dataset_id'],r['dataset_revision_id']) for r in release['manifest']['dataset_revisions']}, 'Release membership differs')
        result['database_bytes'] = conn.execute('SELECT pg_database_size(current_database()) n').fetchone()['n']
        reader = config()['roles']['reader']['user']; ingest = config()['roles']['ingest']['user']
        result['permissions'] = conn.execute('''SELECT t.tablename,has_table_privilege(%s,'core.'||quote_ident(t.tablename),'SELECT') reader_select,
            has_table_privilege(%s,'core.'||quote_ident(t.tablename),'INSERT,UPDATE,DELETE,TRUNCATE') reader_any_write,
            has_table_privilege(%s,'core.'||quote_ident(t.tablename),'DELETE,TRUNCATE') ingest_destructive
            FROM pg_tables t WHERE t.schemaname='core' ORDER BY 1''',(reader,reader,ingest)).fetchall()
        require(all(r['reader_select'] and not r['reader_any_write'] and not r['ingest_destructive'] for r in result['permissions']), 'Role permissions differ from contract')
    # Compare all 26 business tables to the already-restored baseline, including
    # every sample row; no second scan of original binary payloads is necessary.
    result['business_hashes'] = fingerprints()
    baseline = read(REPORTS/'12_restore.json')
    require(result['business_hashes']==baseline['business_table_hashes'], 'Business records differ from restored delivery baseline')
    result['restored_baseline_matches'] = True
    trial = create_trial('test')
    try:
        trial_schema(trial)
        with connection(dbname=trial) as conn:
            require(schema_signature(conn)==result['schema'], 'Live schema differs from a fresh migration build')
            result['schema_reproduction'] = 'passed'
            result['constraint_probes'] = constraint_probes(conn)
            if label != 'before':
                require(all(p['passed'] for p in result['constraint_probes']), 'Constraint probes did not match the strengthened contract')
    finally:
        drop_trial(trial)
    result['trial_database_removed'] = True
    result['status'] = 'passed_existing_data'
    result['schema_sha256'] = schema_hash()
    report(f'13_database_audit_{label}.json',result)
    print(encoded({k:result[k] for k in ('status','release_id','foreign_keys_checked','schema_reproduction','restored_baseline_matches','constraint_probes')}).decode())


def main():
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument('--label',default='latest',choices=('before','after','latest'))
    audit(p.parse_args().label)


if __name__ == '__main__':
    main_guard(main)
