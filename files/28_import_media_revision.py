"""Stage a new complete media release; step 29 validates before activation."""
import importlib
from _media_import import *

def main():
    # Verify locked inputs again immediately before deriving database revisions.
    prepared_holes()
    for a in stage(22)['arrays']:require(sha(source_path(a,a['file']))==a['sha256'],'Spectral source changed')
    importlib.import_module('8_apply_schema').main()
    key,dest,manifest=prepare_package();results=[]
    with connection() as conn:
        require(conn.execute('SELECT pg_try_advisory_lock(%s) ok',(LOCK_KEY,)).fetchone()['ok'],'Another import is active')
        try:
            # All five revisions and the release membership commit atomically.
            with conn.transaction():
                for u in units():
                    print(f"Import media {u['hole_id']}",flush=True);results.append(import_media(conn,u,key,dest,manifest))
                release_manifest={'dataset_revisions':[pick(r,'dataset_id','dataset_revision_id') for r in results],
                    'source_release_id':stage(20)['source_release_id'],'media_package_sha256':key,'schema_sha256':schema_hash()}
                release=uid('media-release',digest(release_manifest))
                if not conn.execute('SELECT 1 FROM core.data_release WHERE id=%s',(release,)).fetchone():
                    insert(conn,'data_release',{'id':release,'manifest_sha256':digest(release_manifest),'release_kind':'local_media_staged','manifest':release_manifest})
                    for r in results:insert(conn,'release_dataset',{'release_id':release,**pick(r,'dataset_id','dataset_revision_id')})
        finally:conn.execute('SELECT pg_advisory_unlock(%s)',(LOCK_KEY,))
    save_stage(28,{'status':'staged','release_id':release,'datasets':results,'media_package_sha256':key,
        'package_directory':dest.relative_to(ROOT).as_posix(),'previous_release_id':stage(20)['source_release_id']})
    report('28_media_import.json',stage(28))

if __name__=='__main__':main_guard(main)
