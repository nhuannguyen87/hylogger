"""Independent review: read-only production inspection; fault probes in a restored disposable database."""
import importlib
import time
from concurrent.futures import ThreadPoolExecutor
from unittest.mock import patch
from _media_common import *
from _media_reader import read_sample
from _trial_support import create_trial,drop_trial,fingerprints

def json_row(row):
    return {k:Jsonb(v) if isinstance(v,(dict,list)) else v for k,v in row.items()}

def inspect_current():
    with connection('reader') as conn:
        conn.execute('BEGIN ISOLATION LEVEL REPEATABLE READ READ ONLY')
        assert_schema(conn)
        current=conn.execute('SELECT * FROM core.active_release').fetchall()
        require(len(current)==1,'Current release cardinality differs')
        release=current[0]['release_id'];catalog=conn.execute("SELECT tablename FROM pg_tables WHERE schemaname='core' ORDER BY tablename").fetchall()
        counts={r['tablename']:conn.execute(sql.SQL('SELECT count(*) n FROM core.{}').format(sql.Identifier(r['tablename']))).fetchone()['n'] for r in catalog}
        revisions=conn.execute('''SELECT d.source_dataset_id,b.source_hole_id,rd.dataset_id,rd.dataset_revision_id,a.id axis_id,a.sample_count
          FROM core.release_dataset rd JOIN core.dataset d ON d.id=rd.dataset_id JOIN core.borehole b ON b.id=d.borehole_id
          JOIN core.sample_axis a ON a.dataset_revision_id=rd.dataset_revision_id WHERE rd.release_id=%s ORDER BY b.source_hole_id''',(release,)).fetchall()
        require({r['source_hole_id'] for r in revisions}==set(ALLOWED),'Unexpected holes in active release')
        rels=conn.execute('SELECT * FROM core.data_release ORDER BY created_at').fetchall()
        mismatched_manifests=[]
        for r in rels:
            stored={(str(v['dataset_id']),str(v['dataset_revision_id'])) for v in conn.execute('SELECT * FROM core.release_dataset WHERE release_id=%s',(r['id'],))}
            claimed={(v['dataset_id'],v['dataset_revision_id']) for v in r['manifest']['dataset_revisions']}
            if stored!=claimed:mismatched_manifests.append(str(r['id']))
        relation_violations={name:conn.execute(q).fetchone()['n'] for name,q in importlib.import_module('13_audit_database').RELATION_CHECKS.items()}
        fk=conn.execute("SELECT count(*) n,count(*) FILTER(WHERE NOT convalidated) unvalidated FROM pg_constraint WHERE connamespace='core'::regnamespace AND contype='f'").fetchone()
        bad_indexes=conn.execute("SELECT c.relname FROM pg_index i JOIN pg_class c ON c.oid=i.indexrelid WHERE c.relnamespace='core'::regnamespace AND (NOT i.indisvalid OR NOT i.indisready)").fetchall()
        roles={}
        for role in ('ingest','reader'):
            user=config()['roles'][role]['user'];roles[role]=conn.execute('''SELECT has_table_privilege(%s,'core.scan_log','INSERT') insert_logs,
              has_table_privilege(%s,'core.release_dataset','INSERT') append_release_member,
              has_table_privilege(%s,'core.active_release','UPDATE') switch_release,
              has_table_privilege(%s,'core.image_region_asset','INSERT') add_row_asset''',(user,user,user,user)).fetchone()
        coverage={}
        for r in revisions:
            axis=r['axis_id'];coverage[r['source_hole_id']]=conn.execute('''SELECT count(*) n,
              count(*) FILTER(WHERE (SELECT count(*) FROM core.image_sample_mapping m WHERE m.axis_id=s.axis_id
               AND m.section_interval_id=s.section_interval_id AND s.sample_no BETWEEN m.sample_no_from AND m.sample_no_to)<>1) bad_mapping
              FROM core.scan_sample s WHERE s.axis_id=%s''',(axis,)).fetchone()
        arrays=conn.execute('''SELECT a.*,l.per_sample_publication_allowed,b.axis_id FROM core.spectral_array a JOIN core.scan_log l ON l.id=a.log_id
          JOIN core.log_axis_binding b ON b.log_id=l.id JOIN core.release_dataset rd ON rd.dataset_revision_id=a.dataset_revision_id
          WHERE rd.release_id=%s''',(release,)).fetchall()
        for a in arrays:
            blocks=conn.execute('SELECT * FROM core.spectral_block WHERE spectral_array_id=%s ORDER BY source_row_from',(a['id'],)).fetchall()
            at=0
            for b in blocks:
                require(b['source_row_from']==at and b['byte_offset']==a['data_offset_bytes']+at*a['sample_stride_bytes'],'Spectral block gap/offset mismatch')
                at=b['source_row_to_exclusive']
            require(at==a['sample_count'],'Incomplete spectral block coverage')
        assets=conn.execute('''SELECT a.id,a.sha256,a.byte_size,l.object_key,l.root_key FROM core.asset a JOIN core.asset_location l ON l.asset_id=a.id
          JOIN core.release_dataset rd ON rd.dataset_revision_id=a.dataset_revision_id
          WHERE rd.release_id=%s AND l.backend='local' AND l.access_status='verified_local' ''',(release,)).fetchall()
        unique={a['object_key']:a for a in assets};checked_bytes=0
        for a in unique.values():
            require(a['root_key']=='etl4','Unknown storage root');p=inside(ETL,ETL/a['object_key'])
            require(p.stat().st_size==a['byte_size'] and sha(p)==a['sha256'],'Current file differs from registered bytes')
            checked_bytes+=a['byte_size']
        # Inspect the ACTUAL joined DB region/asset pair, independently of work manifests.
        pairs=conn.execute('''SELECT ra.*,r.x_px,r.y_px,r.width_px region_width,r.height_px region_height,
          f.id frame_id,f.asset_id source_asset_id,a.provenance FROM core.image_region_asset ra
          JOIN core.image_region r ON r.id=ra.region_id JOIN core.image_frame f ON f.id=r.image_frame_id
          JOIN core.asset a ON a.id=ra.asset_id JOIN core.release_dataset rd ON rd.dataset_revision_id=ra.dataset_revision_id
          WHERE rd.release_id=%s''',(release,)).fetchall()
        asset_map={str(a['id']):a for a in assets};pixel_errors=[];lineage_errors=[]
        for pair in pairs:
            src=decode(ETL/asset_map[str(pair['source_asset_id'])]['object_key']);crop=decode(ETL/asset_map[str(pair['asset_id'])]['object_key'])
            x=pair['x_px'];y=pair['y_px'];w=pair['region_width'];h=pair['region_height']
            if crop.size!=(w,h) or crop.tobytes()!=src.crop((x,y,x+w,y+h)).tobytes():pixel_errors.append(str(pair['region_id']))
            if pair['provenance'].get('region_id')!=str(pair['region_id']) or pair['provenance'].get('source_frame_id')!=str(pair['frame_id']):lineage_errors.append(str(pair['region_id']))
        size=conn.execute('SELECT pg_database_size(current_database()) n').fetchone()['n']
        state=conn.execute('SELECT * FROM core.active_release').fetchall()
        validations=conn.execute('SELECT * FROM core.revision_validation ORDER BY dataset_revision_id').fetchall()
        conn.execute('COMMIT')
    return {'release_id':release,'active_state':state,'validations':validations,'table_counts':counts,'datasets':revisions,
        'releases':rels,'manifest_mismatches':mismatched_manifests,'relation_violations':relation_violations,'foreign_keys':fk,
        'invalid_indexes':bad_indexes,'role_privileges':roles,'sample_coverage':coverage,'spectral_arrays_checked':len(arrays),
        'row_images_checked':len(pairs),'crop_pixel_errors':pixel_errors,'crop_lineage_errors':lineage_errors,
        'unique_files_hashed':len(unique),'file_bytes_hashed':checked_bytes,'database_bytes':size}

def probe(conn,name,fn):
    try:
        with conn.transaction(force_rollback=True):
            evidence=fn();conn.execute('SET CONSTRAINTS ALL IMMEDIATE')
        return {'name':name,'accepted':True,'evidence':evidence}
    except psycopg.Error as exc:
        return {'name':name,'accepted':False,'error':type(exc).__name__,'sqlstate':exc.sqlstate,'detail':str(exc)[:250]}

def negative_probes(trial,current):
    result=[];rev=current['datasets'][0]['dataset_revision_id'];release=current['release_id']
    with connection('ingest',dbname=trial) as c:
        def published_append():
            row=c.execute("SELECT * FROM core.scan_log WHERE dataset_revision_id=%s AND log_kind='spectral' AND availability_status='metadata_only' LIMIT 1",(rev,)).fetchone()
            row.update(id=uid('audit30-extra-log'),source_log_id='audit30-extra-source-log',source_log_name='AUDIT SYNTHETIC EXTRA LOG')
            insert(c,'scan_log',json_row(row))
            return {'revision_id':str(rev),'appended_log':row['id'],'revision_input_digest_changed':False}
        result.append(probe(c,'append_log_to_published_revision',published_append))
        def expand_release():
            base=c.execute('SELECT * FROM core.dataset_revision WHERE id=%s',(rev,)).fetchone();dataset=uid('audit30-dataset');dr=uid('audit30-revision')
            insert(c,'dataset',{'id':dataset,'borehole_id':base['borehole_id'],'source_dataset_id':'audit30-synthetic-dataset'})
            row={**base,'id':dr,'dataset_id':dataset,'source_snapshot_id':'audit30-synthetic','pipeline_id':'audit30-synthetic','input_digest':'audit30-synthetic'}
            insert(c,'dataset_revision',json_row(row))
            before=c.execute('SELECT count(*) n FROM core.release_dataset WHERE release_id=%s',(release,)).fetchone()['n']
            insert(c,'release_dataset',{'release_id':release,'dataset_id':dataset,'dataset_revision_id':dr})
            return {'members_before':before,'members_after':c.execute('SELECT count(*) n FROM core.release_dataset WHERE release_id=%s',(release,)).fetchone()['n'],
                'manifest_members':len(c.execute('SELECT manifest FROM core.data_release WHERE id=%s',(release,)).fetchone()['manifest']['dataset_revisions'])}
        result.append(probe(c,'append_dataset_to_published_release',expand_release))
        def empty_publish():
            new=uid('audit30-empty-release');manifest={'dataset_revisions':[],'synthetic':True}
            insert(c,'data_release',{'id':new,'manifest_sha256':digest(manifest),'manifest':manifest,'release_kind':'unvalidated'})
            c.execute('UPDATE core.active_release SET release_id=%s WHERE singleton',(new,))
            return {'active_release_id':str(c.execute('SELECT release_id FROM core.active_release').fetchone()['release_id']),
                'dataset_count':0,'validation_count':0}
        result.append(probe(c,'publish_empty_unvalidated_release',empty_publish))
        ra=c.execute('''SELECT ra.* FROM core.image_region_asset ra JOIN core.image_region r ON r.id=ra.region_id
          JOIN core.image_frame f ON f.id=r.image_frame_id WHERE ra.dataset_revision_id=%s ORDER BY f.image_ordinal,r.region_ordinal LIMIT 1''',(rev,)).fetchone()
        def wrong_crop():
            alt=c.execute('''SELECT other.* FROM core.image_region_asset other JOIN core.image_region r ON r.id=other.region_id
              JOIN core.image_region own ON own.id=%s WHERE other.log_id=%s AND other.width_px=%s AND other.height_px=%s
              AND r.image_frame_id<>own.image_frame_id LIMIT 1''',(ra['region_id'],ra['log_id'],ra['width_px'],ra['height_px'])).fetchone()
            require(alt,'Need same-size crop from another tray')
            insert(c,'image_region_asset',{**ra,'asset_id':alt['asset_id']})
            return {'region_id':str(ra['region_id']),'wrong_asset_id':str(alt['asset_id']),
                'actual_source_region_id':str(alt['region_id']),'same_image_log':True,'different_source_tray':True}
        result.append(probe(c,'attach_other_tray_crop_same_image_log',wrong_crop))
        def flip_recipe():
            # Re-registering the same physical bytes under a separate asset is
            # legitimate, but declaring a flip requires corresponding geometry.
            a=c.execute('SELECT * FROM core.asset WHERE id=%s',(ra['asset_id'],)).fetchone();new=uid('audit30-flipasset')
            insert(c,'asset',{**a,'id':new,'logical_path':'audit30/flip.png'})
            insert(c,'log_asset',{'log_id':ra['log_id'],'asset_id':new,'dataset_revision_id':rev,'role':'derived_core_row'})
            insert(c,'image_region_asset',{**ra,'asset_id':new,'transform':{**ra['transform'],'flip_x':True}})
            return {'flip_x':True,'accepted_without_pixel_transform':True}
        result.append(probe(c,'native_crop_allows_flip_recipe',flip_recipe))
        def foreign_region():
            r=c.execute('SELECT * FROM core.image_region WHERE id=%s',(ra['region_id'],)).fetchone()
            f=c.execute('SELECT id FROM core.image_frame WHERE dataset_revision_id<>%s LIMIT 1',(rev,)).fetchone()['id']
            insert(c,'image_region',{**r,'id':uid('audit30-cross-dataset'),'image_frame_id':f,'region_ordinal':9999})
        result.append(probe(c,'control_cross_dataset_frame_rejected',foreign_region))
    return result

def overlapping_writes(trial,isolation):
    # Same restored DB, committed setup solely in the disposable audit copy.
    with connection('ingest',dbname=trial) as setup:
        f=setup.execute('SELECT f.* FROM core.image_frame f WHERE EXISTS(SELECT 1 FROM core.image_region r WHERE r.image_frame_id=f.id) LIMIT 1').fetchone();old=f['id'];frame=uid('audit30-concurrent-frame',isolation)
        f.update(id=frame,image_ordinal=900000 if isolation=='READ COMMITTED' else 900001);insert(setup,'image_frame',f)
        r=setup.execute('SELECT * FROM core.image_region WHERE image_frame_id=%s LIMIT 1',(old,)).fetchone()
        if r is None:
            r=setup.execute('SELECT * FROM core.image_region WHERE dataset_revision_id=%s LIMIT 1',(f['dataset_revision_id'],)).fetchone()
        require(r,'Concurrent fixture needs a media frame')
    a=connection('ingest',dbname=trial,autocommit=False);b=connection('ingest',dbname=trial,autocommit=False)
    try:
        a.rollback();b.rollback()
        for c in (a,b):
            c.execute(sql.SQL('SET TRANSACTION ISOLATION LEVEL '+isolation))
            c.execute('SELECT count(*) FROM core.image_region WHERE image_frame_id=%s',(frame,)).fetchone()
        row={**r,'image_frame_id':frame,'x_px':0,'y_px':0,'width_px':100,'height_px':10,'id':uid('audit30-region-a',isolation),'region_ordinal':0}
        insert(a,'image_region',row)
        def second():
            try:
                insert(b,'image_region',{**row,'id':uid('audit30-region-b',isolation),'region_ordinal':1,'y_px':5});b.commit();return {'accepted':True}
            except psycopg.Error as exc:b.rollback();return {'accepted':False,'sqlstate':exc.sqlstate,'detail':str(exc)[:180]}
        with ThreadPoolExecutor(max_workers=1) as pool:
            future=pool.submit(second);a.commit();out=future.result(timeout=15)
        return {'isolation':isolation,**out,'regions_overlap_if_accepted':True}
    finally:a.close();b.close()

def reproducibility_review():
    imp=importlib.import_module('_media_import');state=stage(28);dest=ROOT/state['package_directory'];manifest=read(dest/'manifest.json')
    changed={}
    snapshot=read(dest/'code_snapshot'/'code_manifest.json')
    for name,checksum in snapshot['files'].items():
        if sha(ROOT/name)!=checksum:changed[name]={'old':checksum,'new':sha(ROOT/name)}
    new={**manifest,'code_sha256':media_code_hash()}
    # Probe the public import helper without a DB connection: it attempts WORK
    # reads before checking the already-imported revision or frozen package.
    attempted=[]
    def unavailable(n):attempted.append(n);raise FileNotFoundError('Synthetic missing work directory')
    try:
        with patch.object(imp,'stage',side_effect=unavailable):imp.import_media(None,units()[0],state['media_package_sha256'],dest,manifest)
    except FileNotFoundError:requires_work=True
    else:requires_work=False
    with connection('reader') as c:
        active=str(c.execute('SELECT release_id FROM core.active_release').fetchone()['release_id'])
        old_matches=[bool(c.execute('SELECT 1 FROM core.release_dataset WHERE release_id=%s AND dataset_id=%s AND dataset_revision_id=%s',
            (active,u['dataset_id'],u['source_revision_id'])).fetchone()) for u in units()]
    return {'frozen_package_sha256':state['media_package_sha256'],'only_current_code_changes':changed,
        'same_data_rerun_package_sha256':digest(new),'rerun_would_change_all_five_revision_ids':digest(new)!=state['media_package_sha256'],
        'frozen_import_requires_mutable_work':requires_work,'work_steps_attempted':attempted,
        'step20_current_active_matches_original_baseline':old_matches,
        'mutable_work_hashes_match_frozen_steps_now':{str(n):sha(WORK/f'{n}.json')==manifest['files'][f'steps/{n}.json']['sha256'] for n in range(20,28)}}

def main():
    print('Inspect current release, actual table relations and published file bytes',flush=True)
    baseline=fingerprints();current=inspect_current();backup=read(REPORTS/'latest_database_backup.json');path=ROOT/backup['backup_relative_path']
    require(sha(path)==backup['backup_sha256'],'Backup bytes changed')
    trial=create_trial('test');out={'trial_database':trial}
    try:
        c=config();env=os.environ.copy();env.update(PGHOST=c['host'],PGPORT=str(c['port']),PGUSER=c['roles']['owner']['user'],PGPASSWORD=c['roles']['owner']['password'],PGCLIENTENCODING='UTF8')
        command([PG_BIN/'pg_restore.exe','--no-owner','--exit-on-error','--dbname',trial,path],env=env)
        require(fingerprints(trial)==baseline,'Restored backup differs from current business data')
        print('Probe bad writes only in isolated restored database',flush=True)
        out['negative_probes']=negative_probes(trial,current)
        # Choose a media revision, since the restored database also contains old frames.
        out['concurrency']=[]
        for isolation in ('READ COMMITTED','REPEATABLE READ'):
            try:out['concurrency'].append(overlapping_writes(trial,isolation))
            except (ValueError,psycopg.Error) as exc:out['concurrency'].append({'isolation':isolation,'probe_error':str(exc)[:200]})
    finally:drop_trial(trial);out['trial_removed']=True
    require(fingerprints()==baseline,'Production business records changed during audit')
    with connection('reader') as c:
        require(c.execute('SELECT * FROM core.active_release').fetchall()==current['active_state'],'Current release changed during audit')
        require(c.execute('SELECT * FROM core.revision_validation ORDER BY dataset_revision_id').fetchall()==current['validations'],'Validation records changed during audit')
    result={'status':'audit_complete','production_modified':False,'schema_sha256':schema_hash(),'current':current,
        'restore_and_fault_probes':out,'reproducibility':reproducibility_review(),'source_business_hashes':baseline,
        'script_sha256':sha(Path(__file__)),'api_reviewed':False,'cloud_operations':False}
    report('30_media_database_audit.json',result)
    print('Audit complete; production unchanged; see reports/30_media_database_audit.json')

if __name__=='__main__':main_guard(main)
