"""Database/data-file reference reader for the API owner; no web server or UI."""
from functools import lru_cache
import numpy as np
import pyarrow.parquet as pq
from _media_common import *

LABEL='指示线按样本序号估算位置；深度与结果来自所选样本。'

@lru_cache(maxsize=32)
def parquet_group(path, checksum, size, mtime, group):
    require(sha(Path(path))==checksum,'Canonical asset content changed')
    with pq.ParquetFile(path) as pf:return pf.read_row_group(group)

@lru_cache(maxsize=16)
def spectral_bytes(path, size, mtime, offset, length, checksum):
    with Path(path).open('rb') as f:f.seek(offset);data=f.read(length)
    require(len(data)==length and hashlib.sha256(data).hexdigest()==checksum,'Spectral block content changed')
    return data

def local_asset(conn, aid):
    a=conn.execute('''SELECT a.*,l.root_key,l.object_key,l.access_status FROM core.asset a
      JOIN core.asset_location l ON l.asset_id=a.id AND l.verified_sha256=a.sha256
      WHERE a.id=%s AND l.backend='local' AND l.access_status='verified_local'
      ORDER BY l.root_key,l.object_key''',(aid,)).fetchall()
    for row in a:
        if row['root_key']!='etl4':continue
        p=inside(ETL,ETL/row['object_key'])
        if p.is_file() and p.stat().st_size==row['byte_size']:return row,p
    return None,None

def read_result(conn,log,axis,sample_no):
    base={k:log[k] for k in ('id','source_log_id','source_log_name','log_kind','unit','interpretation_set_id',
        'variant_code','algorithm_family','output_region','component_rank','metric_key')}
    base['log_id']=base.pop('id')
    if log['availability_status']!='payload_present':return {**base,'status':log['availability_status'],'value':None}
    if log['binding_status']!='verified' or str(log['axis_id'])!=str(axis):return {**base,'status':'axis_binding_unavailable','value':None}
    if log['log_kind']=='spectral':
        candidates=conn.execute('''SELECT a.*,m.source_row_from,m.sample_no_from,m.sample_no_to
          FROM core.spectral_array a JOIN core.spectral_sample_map m ON m.spectral_array_id=a.id
          WHERE a.log_id=%s AND m.axis_id=%s AND %s BETWEEN m.sample_no_from AND m.sample_no_to
          AND m.binding_status='verified' AND a.layout_status='verified_sample_major' ''',(log['id'],axis,sample_no)).fetchall()
        if len(candidates)!=1:return {**base,'status':'spectral_mapping_unavailable','value':None}
        a=candidates[0];source_row=a['source_row_from']+sample_no-a['sample_no_from']
        blocks=conn.execute('''SELECT * FROM core.spectral_block WHERE spectral_array_id=%s AND
          source_row_from<=%s AND source_row_to_exclusive>%s''',(a['id'],source_row,source_row)).fetchall()
        require(len(blocks)==1,'Ambiguous spectral byte block');b=blocks[0]
        asset,path=local_asset(conn,a['asset_id'])
        if path is None:return {**base,'status':'asset_unavailable','value':None}
        stat=path.stat();data=spectral_bytes(str(path),stat.st_size,stat.st_mtime_ns,b['byte_offset'],b['byte_length'],b['sha256'])
        offset=(source_row-b['source_row_from'])*a['sample_stride_bytes']
        v=np.frombuffer(data,dtype=a['dtype_code'],count=a['channel_count'],offset=offset)
        st=conn.execute('SELECT * FROM core.spectral_stream WHERE id=%s',(a['spectral_stream_id'],)).fetchone()
        return {**base,'status':'available','source_row':source_row,'region_code':st['region_code'],
            'wavelength':st['wavelengths'],'wavelength_unit':st['wavelength_unit'],
            'spectra':[float(x) if np.isfinite(x) else None for x in v],
            'nonfinite_channels':[i for i,x in enumerate(v) if not np.isfinite(x)],'value_unit':a['value_unit'],
            'scaling_applied':False,'array_id':a['id']}
    chunks=conn.execute('''SELECT c.*,a.sha256 FROM core.data_chunk c JOIN core.asset a ON a.id=c.asset_id
       WHERE c.log_id=%s AND c.axis_id=%s AND c.source_row_from<=%s AND c.source_row_to_exclusive>%s''',
        (log['id'],axis,sample_no,sample_no)).fetchall()
    if len(chunks)!=1:return {**base,'status':'sample_result_unavailable','value':None}
    ch=chunks[0];asset,path=local_asset(conn,ch['asset_id'])
    if path is None:return {**base,'status':'asset_unavailable','value':None}
    stat=path.stat();table=parquet_group(str(path),ch['sha256'],stat.st_size,stat.st_mtime_ns,ch['row_group'])
    row=table.slice(sample_no-ch['source_row_from'],1).to_pylist()[0]
    require(row['sample_no']==sample_no,'Canonical row has a different sample identity')
    if log['log_kind']=='scalar':
        status='source_null' if row['value_numeric'] is None and row['value_text'] is None else 'available'
    else:status='available_uncalibrated_profile'
    return {**base,'status':status,'value':row}

def read_sample(conn,release_id,dataset_id,axis_id,sample_no,log_ids=None,image_log_id=None):
    require(isinstance(sample_no,int) and not isinstance(sample_no,bool) and sample_no>=0,'Invalid sample number')
    context=conn.execute('''SELECT d.dataset_revision_id,a.sample_count FROM core.release_dataset d
      JOIN core.sample_axis a ON a.dataset_revision_id=d.dataset_revision_id
      WHERE d.release_id=%s AND d.dataset_id=%s AND a.id=%s''',(release_id,dataset_id,axis_id)).fetchone()
    require(context,'Release, dataset and axis do not belong to the same selection')
    sample=conn.execute('SELECT * FROM core.scan_sample WHERE axis_id=%s AND sample_no=%s',(axis_id,sample_no)).fetchone()
    require(sample,'Selected source sample does not exist')
    logs=conn.execute('''SELECT l.*,b.status AS binding_status,b.axis_id,
      i.variant_code,i.algorithm_family,i.output_region
      FROM core.scan_log l LEFT JOIN core.log_axis_binding b ON b.log_id=l.id
      LEFT JOIN core.interpretation_set i ON i.id=l.interpretation_set_id WHERE l.dataset_revision_id=%s
      AND l.log_kind IN ('scalar','spectral','profile') ORDER BY l.log_kind,l.source_log_name,l.id''',(context['dataset_revision_id'],)).fetchall()
    if log_ids is not None:
        wanted={str(x) for x in log_ids};require(wanted<={str(l['id']) for l in logs},'Result log is outside selected dataset revision')
        logs=[l for l in logs if str(l['id']) in wanted]
    images=conn.execute('''SELECT m.*,r.region_status,f.log_id,f.asset_id AS source_asset_id,
      ra.asset_id,ra.width_px,ra.height_px,ra.transform
      FROM core.image_sample_mapping m JOIN core.image_region r ON r.id=m.image_region_id
      JOIN core.image_frame f ON f.id=r.image_frame_id
      LEFT JOIN core.image_region_asset ra ON ra.region_id=r.id AND ra.representation_kind='native_lossless_png'
      WHERE m.axis_id=%s AND m.section_interval_id=%s AND %s BETWEEN m.sample_no_from AND m.sample_no_to
      ORDER BY f.log_id,r.region_ordinal,ra.asset_id''',(axis_id,sample['section_interval_id'],sample_no)).fetchall()
    if image_log_id is not None:
        require(conn.execute('SELECT 1 FROM core.scan_log WHERE id=%s AND dataset_revision_id=%s AND log_kind=%s',
            (image_log_id,context['dataset_revision_id'],'image')).fetchone(),'Image log is outside selected dataset revision')
        images=[i for i in images if str(i['log_id'])==str(image_log_id)]
    candidates=[]
    for i in images:
        asset,path=local_asset(conn,i['asset_id']) if i['asset_id'] else (None,None)
        available=path is not None and i['region_status']=='reviewed' and i['mapping_status']=='metadata_associated'
        indicator=None
        if available and i['indicator_method']=='sample_index_linear' and i['indicator_version']==1 and i['indicator_status']=='approximate':
            require(i['indicator_parameters']==INDICATOR,'Unknown index mapping contract')
            indicator=marker(sample_no,i['sample_no_from'],i['sample_no_to'],i['width_px'])
        candidates.append({'image_region_id':i['image_region_id'],'image_asset_id':i['asset_id'],'image_log_id':i['log_id'],
            'source_image_asset_id':i['source_asset_id'],'status':'available' if available else 'image_unavailable',
            'object_key':asset['object_key'] if available else None,'width_px':i['width_px'],'height_px':i['height_px'],
            'sample_no_from':i['sample_no_from'],'sample_no_to':i['sample_no_to'],'indicator':indicator,
            'mapping_level':i['mapping_level'],'mapping_status':i['mapping_status'],
            'source_depth_direction':i['source_depth_direction'],'error_px':i['error_px'],'error_depth_m':i['error_depth_m']})
    # Multiple images remain explicit candidates. The caller must choose a log;
    # never silently select the first same-depth or same-numbered tray.
    return {'release_id':str(release_id),'dataset_id':str(dataset_id),'dataset_revision_id':str(context['dataset_revision_id']),
        'axis_id':str(axis_id),'sample_no':sample_no,'md_m':sample['md_m'],'sample_count':context['sample_count'],
        'sample':sample,'results':[read_result(conn,l,axis_id,sample_no) for l in logs],
        'image_status':'image_unavailable' if not candidates else 'selection_required' if len(candidates)>1 else candidates[0]['status'],
        'images':candidates,'indicator_label':LABEL}
