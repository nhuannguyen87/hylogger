"""Lossless PNG crops; independently decode every output and compare pixels."""
from _media_common import *

def main():
    rows=[];total=0
    for r in stage(26)['rows']:
        original=source_path(r,r['source_path']);require(sha(original)==r['source_sha256'],'Changed source image')
        im=decode(original);x=r['x_px'];y=r['y_px'];w=r['width_px'];h=r['height_px']
        require(x>=0 and y>=0 and x+w<=im.width and y+h<=im.height,'Crop outside source')
        crop=im.crop((x,y,x+w,y+h))
        rel=f"crops/{r['dataset_id']}/{r['source_log_id']}/{r['row_key']}.png";p=inside(WORK,WORK/rel);p.parent.mkdir(parents=True,exist_ok=True)
        crop.save(p,format='PNG',optimize=False,compress_level=6)
        check=decode(p);require(check.size==crop.size and check.tobytes()==crop.tobytes(),'Crop pixels changed during encoding')
        size=p.stat().st_size;total+=size
        rows.append({**r,'crop_work_path':rel,'crop_sha256':sha(p),'crop_byte_size':size,
            'pixel_sha256':hashlib.sha256(crop.tobytes()).hexdigest(),
            'recipe':{'version':1,'format':'PNG','mode':'RGB','crop_box_half_open':[x,y,x+w,y+h],
                'source_sha256':r['source_sha256'],'exif_transform':'none','rotation_deg':0,'flip_x':False,
                'coordinate_space':'native_decoded_pixels','pixel_equality_verified':True}})
    save_stage(27,{'status':'passed','rows':rows,'row_asset_count':len(rows),'crop_bytes':total,'lossless_decoded_pixel_equality':True})

if __name__=='__main__': main_guard(main)
