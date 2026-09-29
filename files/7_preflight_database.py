"""Step 7: verify frozen prepared inputs and start an isolated local cluster."""
import argparse
import getpass
import secrets
import socket
from _db_common import *

def freeze_inputs():
    pointer=read(PREPARED/'latest_verified.json'); summary_path=inside(PREPARED,PREPARED/pointer['summary_json'])
    if sha(summary_path)!=pointer['summary_sha256']: raise ValueError('Verified summary hash mismatch')
    summary=read(summary_path)
    if not summary['all_five_verified'] or tuple(summary['active_holes'])!=ALLOWED: raise ValueError('Input is not the verified five-hole release')
    prepared_hashes={}; raw_hashes={}
    for h in summary['holes']:
        hole=h['hole_id']; folder=inside(PREPARED,PREPARED/h['directory']); hashes=dict(h['report_sha256'])
        last=None
        for name,expected in h['report_sha256'].items():
            if sha(folder/name)!=expected: raise ValueError(f'Report changed: {hole}/{name}')
        report_names=['1_inventory.json','2_integrity.json','3_metadata_report.json','4_alignment_report.json','5_storage_report.json']
        for name in report_names:
            r=read(folder/name)
            if r['status'] not in ('passed','passed_with_issues') or r['pipeline_id']!=summary['pipeline_id']: raise ValueError('Invalid prerequisite')
            if last and r['previous_report_sha256']!=sha(folder/last): raise ValueError('Broken report chain')
            last=name
        metadata=read(folder/'3_metadata_report.json'); alignment=read(folder/'4_alignment_report.json'); storage=read(folder/'5_storage_report.json')
        hashes['3_normalized_metadata.json']=metadata['normalized_metadata_sha256']; hashes.update(alignment['artifacts'])
        hashes.update({a['relative_path']:a['sha256'] for a in storage['assets']})
        for relative,expected in hashes.items():
            if sha(inside(folder,folder/relative))!=expected: raise ValueError(f'Prepared hash mismatch: {hole}/{relative}')
        raw_hashes[hole]={}
        for a in storage['raw_assets']:
            path=inside(ETL/hole,ETL/hole/a['source_relative_path'])
            if sha(path)!=a['sha256'] or path.stat().st_size!=a['byte_size']: raise ValueError(f'Raw asset changed: {hole}/{a["source_relative_path"]}')
            raw_hashes[hole][a['source_relative_path']]=a['sha256']
        prepared_hashes[hole]=hashes
        print(f'{hole}: frozen {len(hashes)} prepared files and {len(raw_hashes[hole])} raw files',flush=True)
    lock={'summary_path':pointer['summary_json'],'summary_sha256':pointer['summary_sha256'],'pipeline_id':summary['pipeline_id'],
          'holes':list(ALLOWED),'prepared_hashes':prepared_hashes,'raw_hashes':raw_hashes,'totals':summary['totals']}
    existing=REPORTS/'7_input_lock.json'
    if existing.exists() and read(existing)!=lock: raise ValueError('Frozen input differs; explicitly review a new input lock before replacing it')
    write(existing,lock)
    return lock

def local_runtime(action):
    pgdata=inside(RUNTIME,RUNTIME/'pgdata')
    if action=='stop':
        if not (pgdata/'ETL4_OWNED_CLUSTER').exists(): raise ValueError('Not an ETL4-owned cluster')
        command([PG_BIN/'pg_ctl.exe','-D',pgdata,'stop','-m','fast','-w','-t','30']); print('Local ETL4 cluster stopped'); return
    RUNTIME.mkdir(exist_ok=True)
    if not (RUNTIME/'local_config.json').exists():
        c={'host':'127.0.0.1','port':55432,'database':'etl4_core','pg_bin':str(PG_BIN),'roles':{
            k:{'user':v,'password':secrets.token_urlsafe(32)} for k,v in [('owner','etl4_owner'),('ingest','etl4_ingest'),('reader','etl4_reader')]}}
        write(RUNTIME/'local_config.json',c)
        # Keep the workspace ACL, including the desktop sandbox's restricted SID.
        # Removing inherited ACL entries would lock the executing sandbox out.
    c=config()
    if not (pgdata/'PG_VERSION').exists():
        if pgdata.exists() and any(pgdata.iterdir()): raise ValueError('Refusing initialization in a nonempty directory')
        password_file=RUNTIME/'init_password.tmp'; write(password_file,c['roles']['owner']['password']+'\n')
        try:
            output=command([PG_BIN/'initdb.exe','-D',pgdata,'-U',c['roles']['owner']['user'],'--pwfile',password_file,
                            '--auth','scram-sha-256','--encoding','UTF8','--locale','C','--data-checksums'])
        finally: password_file.unlink(missing_ok=True)
        write(pgdata/'ETL4_OWNED_CLUSTER',{'purpose':'ETL4 isolated local database','created_at':now()})
        with (pgdata/'postgresql.conf').open('a',encoding='utf-8') as f:
            f.write("\n# ETL4 isolated local trial\nlisten_addresses = '127.0.0.1'\nport = 55432\nmax_connections = 30\nshared_buffers = '128MB'\nlog_min_error_statement = 'panic'\n")
    if not (pgdata/'ETL4_OWNED_CLUSTER').exists(): raise ValueError('Existing cluster lacks ownership marker')
    status=subprocess.run([str(PG_BIN/'pg_ctl.exe'),'-D',str(pgdata),'status'],capture_output=True)
    if status.returncode:
        with socket.socket() as s:
            if s.connect_ex((c['host'],c['port']))==0: raise ValueError('Configured local port is already in use')
        command([PG_BIN/'pg_ctl.exe','-D',pgdata,'-l',RUNTIME/'postgresql.log','start','-w','-t','30'])
    with connection(dbname='postgres') as conn:
        # Windows PostgreSQL exposes this filesystem GUC in the system code page.
        raw=bytes.fromhex(conn.execute("SELECT encode(current_setting('data_directory')::bytea,'hex') AS path_hex").fetchone()['path_hex'])
        try: decoded=raw.decode('utf-8')
        except UnicodeDecodeError: decoded=raw.decode('mbcs')
        actual=Path(decoded).resolve()
        if actual!=pgdata.resolve(): raise ValueError('Connected to an unexpected database cluster')
        version=conn.execute('SELECT version() AS version').fetchone()['version']
    report('7_environment.json',{'status':'passed','postgres_version':version,'host':c['host'],'port':c['port'],'cluster_path':str(pgdata),
                               'postgis_control':(PG_BIN.parent/'share/extension/postgis.control').read_text(encoding='utf-8')})
    print('Isolated PostgreSQL is ready on 127.0.0.1:55432; credentials remain in ignored runtime config',flush=True)

def main():
    p=argparse.ArgumentParser(); p.add_argument('--runtime-only',action='store_true'); p.add_argument('--stop',action='store_true'); args=p.parse_args()
    if args.stop: local_runtime('stop'); return
    if not args.runtime_only: freeze_inputs()
    local_runtime('start')

if __name__=='__main__': main_guard(main)
