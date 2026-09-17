"""Compare explicit official sampleNo JSON with local F32; cache evidence offline."""
import argparse
from concurrent.futures import ThreadPoolExecutor
import httpx
import numpy as np
from _media_common import *

URL='https://geossdi.dmp.wa.gov.au/NVCLDataServices/getspectraldata.html'
COMMIT='f67726970e492583febf4343b0e886f981bc0a27'

def verify_one(a, fetch):
    if not a['file']: return {**a,'verification_status':'metadata_only','per_sample_publication_allowed':False}
    u=a; n=a['sample_count']; c=a['band_count']
    sections=[s for s in read(source_folder(u)/'4_core_intervals.json') if s['interval_kind']=='section']
    edge=sections[0]['sample_no_to']
    starts=sorted({0,edge,max(0,n//2-1),max(0,n-2),min(4095,n-2)})
    proofs=[]
    matrix=np.memmap(source_path(u,a['file']),dtype='<f4',mode='r',shape=(n,c))
    for start in starts:
        end=min(start+1,n-1); p=EVIDENCE/'spectra'/a['dataset_id']/a['log_id']/f'{start}_{end}.json'
        params={'speclogid':a['log_id'],'startsampleno':start,'endsampleno':end,'outputformat':'json'}
        if not p.exists():
            require(fetch,f'Missing reference {p.name}; run step 21 --fetch-reference once')
            with httpx.Client(timeout=25,follow_redirects=True) as client:
                response=client.get(URL,params=params);response.raise_for_status()
            value=response.json()
            require(isinstance(value,list),'Unexpected official spectral response')
            write(p,{'url':str(response.url),'retrieved_at':now(),'source_sha256':a['sha256'],
                     'parameters':params,'response':value})
        evidence=read(p)
        require(evidence['source_sha256']==a['sha256'] and evidence['parameters']==params,'Reference identity mismatch')
        rows=evidence['response']
        require([r['sampleNo'] for r in rows]==list(range(start,end+1)),'Official sample numbers differ')
        for row in rows:
            actual=np.asarray(row['floatspectraldata'],dtype='<f4')
            require(actual.shape==(c,),'Reference channels differ')
            # JSON decimals represent IEEE float32; compare reconstructed bits,
            # keeping all channels, including zeros and non-finite values.
            require(actual.tobytes()==matrix[row['sampleNo']].tobytes(),'Official sample differs from local spectrum')
        proofs.append({'path':p.relative_to(ROOT).as_posix(),'sha256':sha(p),'sample_no_from':start,'sample_no_to':end})
    del matrix
    require(n==a['sample_count'] and n==next(u['sample_count'] for u in units() if u['dataset_id']==a['dataset_id']), 'Axis length mismatch')
    return {**a,'layout_status':'verified_sample_major','binding_status':'verified',
        'verification_status':'passed','per_sample_publication_allowed':True,
        'binding_basis':'official ordered sample-number service contract + source complete-file manifest + explicit sampleNo reference comparisons; no depth interpolation',
        'matrix_order':'C','dtype_code':'<f4','shape':[n,c],'references':proofs,
        'contract_commit':COMMIT,'verification_scope':'All local bytes checked by source hash; selected first/boundary/middle/last spectra independently compared across all channels, not an exhaustive redownload'}

def main():
    p=argparse.ArgumentParser();p.add_argument('--fetch-reference',action='store_true');args=p.parse_args()
    with ThreadPoolExecutor(max_workers=4) as pool:
        verified=list(pool.map(lambda a:verify_one(a,args.fetch_reference),stage(20)['spectra']))
    official={p.name:sha(p) for p in (EVIDENCE/'official').glob('*') if p.is_file()}
    require({'NVCLDataSvcDao.java','SpectralDataVo.java','getspectraldatausage.html'}<=official.keys(),'Missing official contract evidence')
    save_stage(21,{'status':'passed','spectra':verified,'official_evidence_sha256':official,'official_commit':COMMIT})

if __name__=='__main__': main_guard(main)
