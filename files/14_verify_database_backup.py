"""Back up and restore the reviewed database structure without any API dependency."""
import importlib
from _db_common import *
from _trial_support import create_trial, drop_trial, fingerprints


def main():
    audit = importlib.import_module('13_audit_database')
    c = config()
    with connection() as guard:
        if not guard.execute('SELECT pg_try_advisory_lock(%s) ok',(LOCK_KEY,)).fetchone()['ok']:
            raise RuntimeError('Importer/validator is active; retry after it completes')
        assert_schema(guard)
        release = guard.execute('SELECT release_id FROM core.active_release WHERE singleton').fetchone()['release_id']
        structure = audit.schema_signature(guard)
        baseline = fingerprints()
        stamp = datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%SZ')
        backup = ROOT/'backups'/f'etl4_db_review_{release}_{stamp}.dump'
        backup.parent.mkdir(exist_ok=True)
        env = os.environ.copy()
        env.update(PGHOST=c['host'],PGPORT=str(c['port']),PGUSER=c['roles']['owner']['user'],PGPASSWORD=c['roles']['owner']['password'],PGCLIENTENCODING='UTF8')
        command([PG_BIN/'pg_dump.exe','--format=custom','--no-owner','--file',backup,'--dbname',c['database']],env=env)
        trial = create_trial('restore')
        try:
            command([PG_BIN/'pg_restore.exe','--no-owner','--exit-on-error','--dbname',trial,backup],env=env)
            restored = fingerprints(trial)
            audit.require(restored == baseline,'Restored records differ')
            with connection(dbname=trial) as conn:
                audit.require(audit.schema_signature(conn)==structure,'Restored structure differs')
                assert_schema(conn)
                audit.require(conn.execute('SELECT release_id FROM core.active_release WHERE singleton').fetchone()['release_id']==release,'Restored release differs')
                violations = {name:conn.execute(q).fetchone()['n'] for name,q in audit.RELATION_CHECKS.items()}
                audit.require(not any(violations.values()),'Restored relationship checks failed')
                foreign_keys = audit.foreign_key_checks(conn)
                restored_bytes = conn.execute('SELECT pg_database_size(current_database()) n').fetchone()['n']
            audit.require(fingerprints()==baseline,'Source data changed during backup')
            audit.require(audit.schema_signature(guard)==structure,'Source structure changed during backup')
            audit.require(guard.execute('SELECT release_id FROM core.active_release WHERE singleton').fetchone()['release_id']==release,'Source release changed during backup')
        finally:
            drop_trial(trial)
        manifest = read(REPORTS/'deployment_asset_manifest.json')
        audit.require(manifest['release_id']==str(release),'Asset manifest has a different release')
        result = {'status':'passed','scope':'database-only; no API, cloud or source-file operations',
            'code_sha256':sha(Path(__file__)),'audit_code_sha256':sha(ROOT/'13_audit_database.py'),
            'schema_sha256':schema_hash(),'release_id':release,'backup_relative_path':backup.relative_to(ROOT).as_posix(),
            'backup_sha256':sha(backup),'backup_bytes':backup.stat().st_size,'business_table_hashes':baseline,
            'restored_hashes':restored,'restored_structure_matches':True,'restored_database_bytes':restored_bytes,
            'foreign_keys_checked':foreign_keys,'relation_violations':violations,'restored_trial_removed':True}
        write(backup.with_suffix('.manifest.json'),{'backup_sha256':result['backup_sha256'],'release_id':release,
            'schema_sha256':schema_hash(),'migrations':{p.name:sha(p) for p in sorted((ROOT/'migrations').glob('*.sql'))},
            'asset_manifest_sha256':sha(REPORTS/'deployment_asset_manifest.json'),'asset_manifest':manifest,
            'runtime_credentials_included':False,'requires':'Existing roles, PostGIS extension binaries, and separately preserved referenced files'})
        report('14_database_only_restore.json',result)
        report('latest_database_backup.json',{'report':'14_database_only_restore.json','backup_relative_path':result['backup_relative_path'],
            'backup_sha256':result['backup_sha256'],'schema_sha256':schema_hash(),'release_id':release})
        print(f'Database-only restore verified: {len(baseline)} business tables, {foreign_keys} foreign keys, {result["backup_bytes"]:,} backup bytes')


if __name__=='__main__':
    main_guard(main)
