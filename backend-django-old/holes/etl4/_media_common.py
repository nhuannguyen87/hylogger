"""Shared helpers for numbered media steps; never modify locked ETL outputs."""
from _db_common import *
from PIL import Image

WORK = ROOT / 'media_work'
EVIDENCE = ROOT / 'media_evidence'
DIRECTION = {'source_depth_direction':'left_to_right','direction_status':'confirmed',
             'direction_basis':'project_owner_manual_confirmation',
             'confirmed_on':'2026-09-08','scope':list(ALLOWED)}
INDICATOR = {'range_basis':'row_sample_interval','endpoint_rule':'first_last_pixel_center',
             'single_sample_rule':'center','coordinate_space':'row_crop_pixels'}

def require(condition, message):
    if not condition: raise ValueError(message)

def stage(number): return read(WORK/f'{number}.json')
def save_stage(number, value):
    write(WORK/f'{number}.json', value)
    print(f'Step {number}: {value.get("status", "prepared")}',flush=True)

def units(): return stage(20)['datasets']
def source_folder(u): return inside(PREPARED,PREPARED/u['prepared_directory'])
def source_path(u, rel): return inside(ETL/u['hole_id'],ETL/u['hole_id']/rel)

def marker(sample_no, a, b, width):
    require(all(isinstance(v,int) and not isinstance(v,bool) for v in (sample_no,a,b,width)), 'Integer sample identities and width required')
    require(0<=a<=sample_no<=b and width>=1,'Sample outside mapping interval or invalid image width')
    p=(sample_no-a)/(b-a) if b>a else .5
    return {'method':'sample_index_linear','version':1,'status':'approximate','p':p,'x_px':p*(width-1)}

def decode(path):
    with Image.open(path) as im:
        # Native encoded orientation is the declared coordinate system. Refuse
        # an unreviewed EXIF transform rather than silently moving crop boxes.
        require(im.getexif().get(274,1)==1,'EXIF orientation requires an explicit layout review')
        return im.convert('RGB')

def media_code_hash():
    files=sorted(p for p in ROOT.glob('*.py') if p.name.startswith('_media_') or
                 (p.name.split('_')[0].isdigit() and 20<=int(p.name.split('_')[0])<=29))
    return digest({p.name:sha(p) for p in files})
