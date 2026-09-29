"""Step 12: create a real pg_dump archive and restore/verify it in an isolated DB."""
import time
from _db_common import *
from _trial_support import create_trial,drop_trial,fingerprints,OperationSampler
from _verification import verify_hole,api_checks,require

def main():
    c=config(); prepared=prepared_holes()
    with connection() as conn:
        release=conn.execute('SELECT release_id FROM core.active_release').fetchone()
        require(release is not None,'No published local release to back up')
    baseline=fingerprints(); stamp=datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%SZ')
    backup=ROOT/'backups'/f"etl4_{release['release_id']}_{stamp}.dump"; backup.parent.mkdir(exist_ok=True)
    env=os.environ.copy(); env.update(PGHOST=c['host'],PGPORT=str(c['port']),PGUSER=c['roles']['owner']['user'],PGPASSWORD=c['roles']['owner']['password'],PGCLIENTENCODING='UTF8')
    started=time.perf_counter()
    command([PG_BIN/'pg_dump.exe','--format=custom','--no-owner','--file',backup,'--dbname',c['database']],env=env)
    backup_seconds=time.perf_counter()-started; trial=create_trial('restore'); success=False
    try:
        with OperationSampler(trial) as sampler:
            started=time.perf_counter()
            command([PG_BIN/'pg_restore.exe','--no-owner','--exit-on-error','--dbname',trial,backup],env=env)
            restore_seconds=time.perf_counter()-started
        restored=fingerprints(trial); require(restored==baseline,'Restored business records differ')
        with connection(dbname=trial) as conn:
            checks=[verify_hole(conn,h) for h in prepared]
            restored_release=conn.execute('SELECT release_id FROM core.active_release').fetchone()['release_id']
            require(restored_release==release['release_id'],'Restored release pointer differs')
            restored_bytes=conn.execute('SELECT pg_database_size(current_database()) AS n').fetchone()['n']
        api=api_checks(prepared,release['release_id'],dbname=trial)
        require(fingerprints()==baseline,'Source database changed during backup/restore')
        result={'status':'passed','source_database':c['database'],'restored_database':trial,'release_id':release['release_id'],
                'backup_relative_path':backup.relative_to(ROOT).as_posix(),'backup_sha256':sha(backup),'backup_bytes':backup.stat().st_size,
                'backup_seconds':backup_seconds,'restore_seconds':restore_seconds,'business_table_hashes':baseline,'restored_hashes':restored,
                'restored_database_bytes':restored_bytes,
                'holes':checks,'http_checks':api,'operation_sampling':sampler.result(),
                'asset_restore_scope':'Database restored independently; referenced original/Parquet files revalidated at existing local ETL4 paths. No second copy of object files created.',
                'cloud_actions':'excluded by user'}
        report('12_restore.json',result)
        write(backup.with_suffix('.manifest.json'),{'backup_sha256':result['backup_sha256'],'release_id':release['release_id'],
              'asset_manifest_sha256':sha(REPORTS/'deployment_asset_manifest.json'),'asset_manifest':read(REPORTS/'deployment_asset_manifest.json'),
              'runtime_credentials_included':False,'requires':'Existing roles, PostGIS extension binaries, and separately preserved referenced files'})
        success=True; print(f"Restore verified: {len(checks)} holes; backup {backup.stat().st_size:,} bytes")
    finally:
        drop_trial(trial)
        if success:
            result['restored_trial_removed']=True; report('12_restore.json',result)

if __name__=='__main__': main_guard(main)
