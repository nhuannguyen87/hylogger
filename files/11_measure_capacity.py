"""Step 11: measure PostgreSQL, file sets and representative local read costs."""
import argparse
import time
from collections import defaultdict
from fastapi.testclient import TestClient
from _db_common import *
from api.app import create_app,verify_file

def measure(label='five_holes'):
    prepared=prepared_holes()
    with connection() as conn:
        assert_schema(conn)
        active=conn.execute('SELECT release_id FROM core.active_release').fetchone()
        if not active: raise ValueError('No verified local release')
        conn.execute('ANALYZE')
        tables=conn.execute('''SELECT c.relname AS table_name,pg_table_size(c.oid) AS table_bytes,
             pg_indexes_size(c.oid) AS index_bytes,pg_total_relation_size(c.oid) AS total_bytes,
             CASE WHEN c.reltoastrelid=0 THEN 0 ELSE pg_total_relation_size(c.reltoastrelid) END AS toast_bytes_included
             FROM pg_class c JOIN pg_namespace n ON n.oid=c.relnamespace WHERE n.nspname='core' AND c.relkind='r' ORDER BY c.relname''').fetchall()
        for t in tables: t['row_count']=conn.execute(sql.SQL('SELECT count(*) AS n FROM core.{}').format(sql.Identifier(t['table_name']))).fetchone()['n']
        system=conn.execute('''SELECT current_database() AS database,version() AS postgres_version,postgis_full_version() AS postgis_version,
            pg_database_size(current_database()) AS database_bytes,pg_current_wal_lsn()::text AS wal_lsn,
            (SELECT sum(size)::bigint FROM pg_ls_waldir()) AS current_wal_directory_bytes,
            (SELECT temp_bytes FROM pg_stat_database WHERE datname=current_database()) AS cumulative_query_temp_bytes''').fetchone()
        assets=conn.execute('''SELECT a.* FROM core.asset a JOIN core.release_dataset rd ON rd.dataset_revision_id=a.dataset_revision_id
             WHERE rd.release_id=%s ORDER BY a.representation,a.logical_path''',(active['release_id'],)).fetchall()
        # This is a deployable manifest, not a cloud upload request.
        categories=defaultdict(lambda:{'logical_asset_count':0,'logical_bytes':0,'unique':{}})
        for a in assets:
            if a['representation']=='canonical': category='canonical_payloads'
            elif a['representation']=='evidence': category='processing_evidence'
            elif a['logical_path'].endswith('.jpg'): category='tray_thumbnails'
            elif a['logical_path'].endswith('.f32'): category='original_spectral_arrays'
            else: category='other_original_archive'
            c=categories[category]; c['logical_asset_count']+=1; c['logical_bytes']+=a['byte_size']; c['unique'][a['sha256']]=a['byte_size']
        totals={}
        for k,v in categories.items(): totals[k]={k2:v2 for k2,v2 in v.items() if k2!='unique'}|{'unique_files_by_sha256':len(v['unique']),'unique_bytes_by_sha256':sum(v['unique'].values())}
        plans=[]
        for h in (prepared[0],prepared[1]):
            rev=h['metadata']['dataset_revision']['id']; axis=h['alignment']['axis_id']; q=h['summary']['depth_range_query']
            explain=conn.execute('''EXPLAIN (ANALYZE,BUFFERS,FORMAT JSON) SELECT * FROM core.scan_sample
               WHERE axis_id=%s AND md_m BETWEEN %s AND %s ORDER BY md_m,sample_no''',(axis,q['from_m'],q['to_m'])).fetchone()
            plans.append({'hole_id':h['summary']['hole_id'],'sample_query_plan':explain['QUERY PLAN']})
    benchmarks=[]
    with TestClient(create_app()) as client:
        for h in (prepared[0],prepared[1]):
            log=next(l for l in h['metadata']['logs'] if l['source_log_name']=='Min1 sTSAS'); q=h['summary']['depth_range_query']
            asset=next(a for a in h['storage']['assets'] if a.get('source_log_id')==log['source_log_id'])
            for name,depths in [('narrow',(q['from_m'],q['to_m'])),('wide',(h['alignment']['observed_depth_min_m'],h['alignment']['observed_depth_max_m']))]:
                verify_file.cache_clear()
                for iteration in range(2):
                    start=time.perf_counter()
                    response=client.get(f"/v1/logs/{log['id']}/values",params={'release_id':str(active['release_id']),'from_m':depths[0],'to_m':depths[1],'limit':5000})
                    if response.status_code!=200: raise ValueError(response.text[:500])
                    data=response.json()
                    benchmarks.append({'hole_id':h['summary']['hole_id'],'range':name,'iteration':iteration,
                        'checksum_cache':'empty' if iteration==0 else 'warm','os_cache':'uncontrolled; not a physical cold-disk measurement',
                        'checksum_bytes_read':asset['byte_size'] if iteration==0 else 0,'elapsed_ms':(time.perf_counter()-start)*1000,
                        'returned_rows':len(data['items']),'response_bytes':len(response.content),'pagination_required':data['next_after_row'] is not None,
                        **data['read_stats']})
    empty=read(REPORTS/'8_empty_database.json')
    result={'status':'measured_local_only','label':label,'release_id':active['release_id'],'system':system,'empty_extension_database_bytes':empty['database_bytes'],
        'increase_over_empty_bytes':system['database_bytes']-empty['database_bytes'],'tables':tables,'core_total_relation_bytes':sum(t['total_bytes'] for t in tables),
        'asset_categories':totals,'asset_count':len(assets),'all_asset_logical_bytes':sum(a['byte_size'] for a in assets),
        'all_asset_unique_bytes_by_sha256':sum({a['sha256']:a['byte_size'] for a in assets}.values()),
        'minimum_scalar_thumbnail_payload_bytes':sum(totals[k]['logical_bytes'] for k in ('canonical_payloads','tray_thumbnails')),
        'benchmarks':benchmarks,'query_plans':plans,
        'limitations':['WAL directory is a current allocation, not an observed historical peak','Query temp_bytes does not cover every index-build or OS temporary allocation',
        'First timed reads include full-file checksum validation; payload byte counters exclude that separate pass','No cloud transfer, latency or egress has been measured',
        'Five holes do not determine the final size of 2000 holes; full original archive retention and revision retention change storage needs']}
    report('11_capacity_'+label+'.json',result)
    write(REPORTS/'deployment_asset_manifest.json',{'release_id':active['release_id'],'assets':[pick(a,'id','dataset_revision_id','representation','asset_kind','logical_path','sha256','byte_size','media_type','semantic_status') for a in assets],
         'upload_status':'not_started; cloud selection and deployment excluded by user'})
    lines=['# 本地数据库容量实测','',f"数据库：{system['database_bytes']:,} 字节；其中 core 表及索引合计 {result['core_total_relation_bytes']:,} 字节。",
        f"空库及 PostGIS 基线：{empty['database_bytes']:,} 字节；导入后的总增量：{result['increase_over_empty_bytes']:,} 字节。",'',
        '| 文件类别 | 逻辑文件数 | 逻辑字节 | 按 SHA256 去重字节 |','|---|---:|---:|---:|']
    lines.extend(f"| {k} | {v['logical_asset_count']} | {v['logical_bytes']:,} | {v['unique_bytes_by_sha256']:,} |" for k,v in totals.items())
    lines+=['',f"全部登记资产合计 {result['all_asset_logical_bytes']:,} 字节。规范载荷与缩略图合计 {result['minimum_scalar_thumbnail_payload_bytes']:,} 字节；此数不包含完整来源归档。",'',
        '数据库大小已经包含库内表、索引等，不与逐表总量重复相加。WAL、导出备份和对象文件另列。JSON 报告保存逐表行数、TOAST、索引、查询计划及局部读取测量。',
        '当前 WAL 目录大小与累计查询临时字节不是导入过程的完整峰值；首次文件读取校验 SHA256 会额外完整读取该文件，报告将这一开销单独列出。',
        '未进行云端选型、上传或部署。约 2000 孔的容量仍需根据真实样本量、日志数、图片数和版本保留策略更新。']
    write(REPORTS/('11_capacity_'+label+'.md'), '\n'.join(lines)+'\n')
    return result

def main():
    p=argparse.ArgumentParser(); p.add_argument('--label',choices=['five_holes','after_restore'],default='five_holes'); a=p.parse_args()
    r=measure(a.label); print(f"Database {r['system']['database_bytes']:,} bytes; all asset references {r['all_asset_logical_bytes']:,} bytes")

if __name__=='__main__': main_guard(main)
