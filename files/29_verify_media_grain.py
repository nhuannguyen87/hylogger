"""Database-only verification, capacity measurement and atomic local activation."""
import argparse
import importlib
import time
from _media_tests import behavior_checks
from _media_reader import *
from _media_import import import_media

def legacy_fingerprints(conn):
    baseline=read(REPORTS/'20_media_baseline.json')['restore']['business_table_hashes'];result={}
    revs=[u['source_revision_id'] for u in units()];axes=[u['source_axis_id'] for u in units()]
    for table in baseline:
        cols={r['column_name'] for r in conn.execute("SELECT column_name FROM information_schema.columns WHERE table_schema='core' AND table_name=%s",(table,))}
        if table=='dataset_revision':where=sql.SQL('t.id=ANY({}::uuid[])').format(sql.Literal(revs))
        elif 'dataset_revision_id' in cols:where=sql.SQL('t.dataset_revision_id=ANY({}::uuid[])').format(sql.Literal(revs))
        elif table in ('scan_sample','core_interval'):where=sql.SQL('t.axis_id=ANY({}::uuid[])').format(sql.Literal(axes))
        elif table=='asset_location':where=sql.SQL('t.asset_id IN (SELECT id FROM core.asset WHERE dataset_revision_id=ANY({}::uuid[]))').format(sql.Literal(revs))
        elif table=='data_release':where=sql.SQL("NOT (t.manifest ? 'media_package_sha256')")
        else:where=sql.SQL('TRUE')
        q=sql.SQL('COPY (SELECT row_to_json(t)::text FROM core.{} t WHERE {} ORDER BY row_to_json(t)::text COLLATE "C") TO STDOUT').format(sql.Identifier(table),where)
        h=hashlib.sha256()
        with conn.cursor().copy(q) as cp:
            for data in cp:h.update(data)
        result[table]=h.hexdigest()
    require(result==baseline,'An original release record changed')
    return result

def verify_dataset(conn,u,new,release):
    axis=new['axis_id'];old=u['source_axis_id'];rev=new['dataset_revision_id']
    n=conn.execute('SELECT count(*) n FROM core.scan_sample WHERE axis_id=%s',(axis,)).fetchone()['n'];require(n==u['sample_count'],'Sample count differs')
    mismatch=conn.execute('''SELECT count(*) n FROM core.scan_sample s FULL JOIN
      (SELECT * FROM core.scan_sample WHERE axis_id=%s) o ON s.sample_no=o.sample_no
      WHERE s.axis_id=%s AND (o.sample_no IS NULL OR s.md_m IS DISTINCT FROM o.md_m OR
      s.tray_sample_no IS DISTINCT FROM o.tray_sample_no OR s.section_sample_no IS DISTINCT FROM o.section_sample_no OR
      s.section_distance_mm IS DISTINCT FROM o.section_distance_mm)''',(old,axis)).fetchone()['n']
    require(mismatch==0,'Source sample values changed')
    coverage=conn.execute('''SELECT count(*) n FROM core.scan_sample s WHERE s.axis_id=%s AND
      (SELECT count(*) FROM core.image_sample_mapping m WHERE m.axis_id=s.axis_id AND m.section_interval_id=s.section_interval_id
       AND s.sample_no BETWEEN m.sample_no_from AND m.sample_no_to)<>1''',(axis,)).fetchone()['n']
    require(coverage==0,'A real sample does not have exactly one row mapping')
    mappings=conn.execute('''SELECT m.*,r.width_px FROM core.image_sample_mapping m JOIN core.image_region r ON r.id=m.image_region_id WHERE m.axis_id=%s''',(axis,)).fetchall()
    for m in mappings:
        a=m['sample_no_from'];b=m['sample_no_to'];w=m['width_px']
        for s in {a,b,(a+b)//2}:
            out=marker(s,a,b,w);require(0<=out['x_px']<=w-1,'Indicator out of bounds')
        require(m['indicator_status']=='approximate' and m['error_px'] is None and m['error_depth_m'] is None,'False pixel calibration')
    # File catalog identity including reused Parquet metadata and all new crops.
    rows=conn.execute('''SELECT a.*,l.object_key,l.verified_sha256 FROM core.asset a JOIN core.asset_location l ON l.asset_id=a.id
      WHERE a.dataset_revision_id=%s AND l.backend='local' ''',(rev,)).fetchall()
    for a in rows:
        p=inside(ETL,ETL/a['object_key']);require(p.stat().st_size==a['byte_size'] and sha(p)==a['sha256'],'Asset bytes differ')
    for r in [r for r in stage(27)['rows'] if r['dataset_id']==u['dataset_id']]:
        im=decode(source_path(u,r['source_path']));x,y,x2,y2=r['recipe']['crop_box_half_open']
        crop_path=ROOT/stage(28)['package_directory']/r['crop_work_path'];actual=decode(crop_path)
        require(actual.tobytes()==im.crop((x,y,x2,y2)).tobytes(),'Stored crop differs from source pixels')
    # Reference queries preserve the exact interpretation variant and values.
    logs=conn.execute("SELECT id,source_log_id,source_log_name FROM core.scan_log WHERE dataset_revision_id=%s AND (log_kind='spectral' OR source_log_name IN ('Min1 sTSAS','Wt1 sTSAS','Min1 uTSAS','Wt1 uTSAS'))",(rev,)).fetchall()
    chosen=[str(l['id']) for l in logs];samples={0,n-1,n//2,129,130}
    storage=read(source_folder(u)/'5_storage_report.json')
    # Boundary samples often contain source nulls. Also exercise actual mineral
    # labels and numeric weights instead of accidentally testing nulls only.
    for log in [l for l in logs if l['source_log_name'] in ('Min1 sTSAS','Min1 uTSAS')]:
        payload=next(a for a in storage['assets'] if a.get('source_log_id')==log['source_log_id'])
        with pq.ParquetFile(source_folder(u)/payload['relative_path']) as pf:
            for batch in pf.iter_batches(columns=['sample_no','value_text']):
                valid=next((r['sample_no'] for r in batch.to_pylist() if r['value_text'] is not None),None)
                if valid is not None:samples.add(valid);break
    first=mappings[0];samples.update({first['sample_no_from'],first['sample_no_to']})
    outputs=[];times=[]
    for s in sorted(x for x in samples if x<n):
        start=time.perf_counter();out=read_sample(conn,release,u['dataset_id'],axis,s,chosen);times.append((time.perf_counter()-start)*1000)
        orig=conn.execute('SELECT md_m FROM core.scan_sample WHERE axis_id=%s AND sample_no=%s',(old,s)).fetchone()
        require(out['md_m']==orig['md_m'] and out['image_status']=='available','Sample read or linked image differs')
        for result in out['results']:
            if result['log_kind']=='scalar' and result.get('value') is not None:
                # Independently locate the original prepared file using source log
                # identity; read full file instead of reusing the row-group reader.
                a=next(a for a in read(source_folder(u)/'5_storage_report.json')['assets'] if a.get('source_log_id')==result['source_log_id'])
                with pq.ParquetFile(source_folder(u)/a['relative_path']) as pf:expected=pf.read().slice(s,1).to_pylist()[0]
                require(result['value']==expected,'Sample result differs from canonical source values')
            if result['log_kind']=='spectral' and result['status']=='available':
                a=next(a for a in stage(22)['arrays'] if a['dataset_id']==u['dataset_id'] and a['log_id']==result['source_log_id'])
                with source_path(u,a['file']).open('rb') as f:f.seek(s*a['band_count']*4);data=f.read(a['band_count']*4)
                expected=np.frombuffer(data,dtype='<f4')
                require(result['spectra']==[float(x) if np.isfinite(x) else None for x in expected],'Sample curve differs from preserved F32')
        outputs.append(out)
    if u['hole_id']=='05KCD001':
        a=next(o for o in outputs if o['sample_no']==129);b=next(o for o in outputs if o['sample_no']==130)
        require(a['images'][0]['image_region_id']!=b['images'][0]['image_region_id'] and a['images'][0]['indicator']['p']==1 and b['images'][0]['indicator']['p']==0,'129 to 130 did not change row and marker')
    # Reader's offline result for a verified official JSON sample must reproduce
    # its float32 values as well, independently of a source-only read.
    refs=0
    for a in [a for a in stage(22)['arrays'] if a['dataset_id']==u['dataset_id']]:
        log=next(l for l in logs if l['source_log_id']==a['log_id'])
        for ref in a['references']:
            for value in read(ROOT/ref['path'])['response']:
                out=read_sample(conn,release,u['dataset_id'],axis,value['sampleNo'],[str(log['id'])])['results'][0]
                require(np.asarray(out['spectra'],dtype='<f4').tobytes()==np.asarray(value['floatspectraldata'],dtype='<f4').tobytes(),'DB reader differs from official reference')
                refs+=1
    return {'hole_id':u['hole_id'],'sample_count':n,'row_count':len(mappings),'assets_checked':len(rows),
        'official_reference_spectra_compared':refs,'sample_read_ms':times,'example_selections':outputs}

def write_manifest(conn,release):
    assets=conn.execute('''SELECT a.* FROM core.asset a JOIN core.release_dataset d ON d.dataset_revision_id=a.dataset_revision_id WHERE d.release_id=%s ORDER BY a.id''',(release,)).fetchall()
    locations=conn.execute('''SELECT l.* FROM core.asset_location l JOIN core.asset a ON a.id=l.asset_id JOIN core.release_dataset d ON d.dataset_revision_id=a.dataset_revision_id WHERE d.release_id=%s ORDER BY l.id''',(release,)).fetchall()
    manifest={'release_id':str(release),'schema_sha256':schema_hash(),'assets':assets,'locations':locations,
        'deployment_performed':False,'object_storage_keys_are_relative':True,'runtime_credentials_included':False}
    write(REPORTS/f'asset_manifest_{release}.json',manifest)
    write(REPORTS/'deployment_asset_manifest.json',manifest)
    return assets,locations

def main():
    parser=argparse.ArgumentParser();parser.add_argument('--no-activate',action='store_true');args=parser.parse_args()
    staged=stage(28);release=staged['release_id'];dest=ROOT/staged['package_directory'];manifest=read(dest/'manifest.json')
    if manifest['code_sha256']!=media_code_hash():
        # Verification-only fixes do not change a prepared data revision. Check
        # the original complete code snapshot and demand that every processing,
        # import and reader file still matches; record this verifier separately.
        snapshot=read(dest/'code_snapshot'/'code_manifest.json')
        require(digest(snapshot['files'])==manifest['code_sha256'],'Original code snapshot does not match imported revision')
        for name,checksum in snapshot['files'].items():
            require(sha(dest/'code_snapshot'/name)==checksum,'Archived import code changed')
            if name not in ('29_verify_media_grain.py','_media_tests.py'):
                require(sha(ROOT/name)==checksum,'Data processing or reading code changed; stage a new revision')
    for relative,meta in manifest['files'].items():require(sha(dest/relative)==meta['sha256'],'Published package changed')
    with connection() as conn:
        require(conn.execute('SELECT pg_try_advisory_lock(%s) ok',(LOCK_KEY,)).fetchone()['ok'],'Another import or validator is active')
        try:
            assert_schema(conn);legacy=legacy_fingerprints(conn);checks=[]
            for u in units():
                print(f"Verify sample media {u['hole_id']}",flush=True)
                new=next(d for d in staged['datasets'] if d['dataset_id']==u['dataset_id']);checks.append(verify_dataset(conn,u,new,release))
            behavior=behavior_checks(conn)
            # Repeat imports must return existing revisions without any writes.
            repeat=[]
            with conn.transaction():
                for u in units():repeat.append(import_media(conn,u,staged['media_package_sha256'],dest,manifest)['action'])
            require(set(repeat)=={'already_present'},'Media import was not idempotent')
            relations={name:conn.execute(q).fetchone()['n'] for name,q in importlib.import_module('13_audit_database').RELATION_CHECKS.items()}
            require(not any(relations.values()),'Database relationship audit failed')
            tables=conn.execute("SELECT c.relname,pg_total_relation_size(c.oid) bytes FROM pg_class c JOIN pg_namespace n ON n.oid=c.relnamespace WHERE n.nspname='core' AND c.relkind='r' ORDER BY c.relname").fetchall()
            database_bytes=conn.execute('SELECT pg_database_size(current_database()) n').fetchone()['n']
            previous=str(conn.execute('SELECT release_id FROM core.active_release WHERE singleton').fetchone()['release_id'])
            if not args.no_activate:
                with conn.transaction():
                    for d in staged['datasets']:
                        conn.execute('''INSERT INTO core.revision_validation(dataset_revision_id,input_digest,verification_code_sha256,result) VALUES (%s,%s,%s,%s)
                          ON CONFLICT(dataset_revision_id) DO UPDATE SET validated_at=now(),verification_code_sha256=EXCLUDED.verification_code_sha256,result=EXCLUDED.result''',
                            (d['dataset_revision_id'],staged['media_package_sha256'],media_code_hash(),Jsonb({'status':'passed','scope':'sample-media-v1'})))
                    conn.execute('UPDATE core.active_release SET release_id=%s,switched_at=now() WHERE singleton',(release,))
            assets,locations=write_manifest(conn,release) if not args.no_activate else ([],[])
            # File deduplication capacity: shared assets are counted once per path.
            unique={l['object_key']:next(a['byte_size'] for a in assets if a['id']==l['asset_id']) for l in locations}
            require(legacy_fingerprints(conn)==legacy,'Legacy release changed during validation')
            result={'status':'passed','release_id':release,'previous_release_id':staged['previous_release_id'],
                'active_release_before_verification':previous,'activated':not args.no_activate,
                'datasets':checks,'behavior_checks':behavior,'idempotent_import':repeat,'legacy_fingerprints':legacy,'relation_violations':relations,
                'database_bytes_with_old_and_new_revisions':database_bytes,'table_sizes':tables,'lossless_crop_bytes':stage(27)['crop_bytes'],
                'unique_referenced_file_count':len(unique),'unique_referenced_file_bytes':sum(unique.values()),
                'code_sha256':media_code_hash(),'schema_sha256':schema_hash(),'cloud_operations':False,'api_operations':False}
            report('29_media_verification.json',result)
            from _schema_docs import generate
            generate(conn)
        finally:conn.execute('SELECT pg_advisory_unlock(%s)',(LOCK_KEY,))
    print(f'Verified {sum(c["sample_count"] for c in checks):,} samples and {sum(c["row_count"] for c in checks):,} image rows; activated={not args.no_activate}')

if __name__=='__main__':main_guard(main)
