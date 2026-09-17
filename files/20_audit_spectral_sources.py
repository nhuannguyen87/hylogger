"""Lock five approved dataset contexts and audit every source asset and spectrum."""
from _media_common import *

def main():
    selected=prepared_holes(); datasets=[]; spectra=[]
    for h in selected:
        m=h['metadata']; hole=h['summary']['hole_id']; d=m['dataset']; r=m['dataset_revision']
        # This adapter consumes the already verified v1 artifacts. It explicitly
        # rejects ambiguous input; the downstream contract is dataset-scoped.
        raw=read(ETL/hole/'datasets.json')
        require(len(raw)==1,'Legacy prepared adapter cannot consume a multi-dataset package; prepare separate dataset contexts')
        for a in h['storage']['raw_assets']:
            p=inside(ETL/hole,ETL/hole/a['source_relative_path'])
            require(p.stat().st_size==a['byte_size'] and sha(p)==a['sha256'],f'Changed raw asset: {hole}/{a["source_relative_path"]}')
        u={'hole_id':hole,'dataset_id':d['id'],'source_dataset_id':d['source_dataset_id'],
           'source_revision_id':r['id'],'source_snapshot_id':r['source_snapshot_id'],
           'source_axis_id':h['alignment']['axis_id'],'sample_count':h['alignment']['sample_count'],
           'prepared_directory':h['folder'].relative_to(PREPARED).as_posix()}
        datasets.append(u)
        logs={l['source_log_id']:l for l in m['logs'] if l['log_kind']=='spectral'}
        streams={s['id']:s for s in m['spectral_streams']}
        for a in read(ETL/hole/'spectral_manifest.json'):
            l=logs[a['log_id']]; st=streams[l['spectral_stream_id']]
            require(a['band_count']==st['wavelength_count'],'Channel count mismatch')
            if a['file']:
                require(a['dtype']=='float32' and a['byte_order']=='little','Unsupported declared dtype')
                require(a['nbytes']==a['sample_count']*a['band_count']*4,'Spectral byte size mismatch')
            spectra.append({**u,**a,'source_log_pk':l['id'],'spectral_stream_id':st['id'],
                'region_code':st['region_code'],'wavelengths':st['wavelengths'],'wavelength_unit':st['wavelength_unit'],
                'value_unit':l['unit'],'script_raw':l['source_script_raw'],
                'layout_status':'unconfirmed','binding_status':'unconfirmed'})
    with connection() as conn:
        release=str(conn.execute('SELECT release_id FROM core.active_release WHERE singleton').fetchone()['release_id'])
        for u in datasets:
            require(conn.execute('SELECT 1 FROM core.release_dataset WHERE release_id=%s AND dataset_id=%s AND dataset_revision_id=%s',
                    (release,u['dataset_id'],u['source_revision_id'])).fetchone(),'Baseline release does not match locked prepared inputs')
    backup=read(REPORTS/'latest_database_backup.json')
    require(backup['release_id']==release,'Baseline backup belongs to another release')
    result={'status':'passed','source_release_id':release,'input_lock_sha256':sha(REPORTS/'7_input_lock.json'),
        'datasets':datasets,'spectra':spectra,'baseline_backup':backup,'direction':DIRECTION}
    save_stage(20,result)
    if not (REPORTS/'20_media_baseline.json').exists():
        write(REPORTS/'20_media_baseline.json',{'audit':result,'restore':read(REPORTS/'14_database_only_restore.json')})

if __name__=='__main__': main_guard(main)
