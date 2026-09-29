"""Index immutable raw spectra into contiguous blocks without duplicating arrays."""
from _media_common import *

def main():
    arrays=[]
    for a in stage(21)['spectra']:
        if not a['file']: continue
        require(a['per_sample_publication_allowed'],'Unverified spectra cannot be served')
        path=source_path(a,a['file']);n,c=a['shape'];stride=c*4;blocks=[]
        with path.open('rb') as f:
            for start in range(0,n,1024):
                count=min(1024,n-start);data=f.read(count*stride)
                require(len(data)==count*stride,'Short spectral block')
                blocks.append({'block_no':len(blocks),'source_row_from':start,'source_row_to_exclusive':start+count,
                    'byte_offset':start*stride,'byte_length':len(data),'sha256':hashlib.sha256(data).hexdigest()})
            require(f.read(1)==b'','Trailing spectral bytes')
        arrays.append({**a,'blocks':blocks,'data_offset_bytes':0,'sample_stride_bytes':stride,
            'channel_stride_bytes':4,'access_method':'original_f32_byte_range','scaling_applied':False,
            'sample_map':{'kind':'identity','source_row_from':0,'source_row_to':n-1,'sample_no_from':0,'sample_no_to':n-1}})
    save_stage(22,{'status':'passed','arrays':arrays,'array_bytes_duplicated':0})

if __name__=='__main__': main_guard(main)
