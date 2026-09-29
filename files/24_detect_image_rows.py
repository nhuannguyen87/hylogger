"""Propose native-pixel row regions and a representative review contact sheet."""
import math
from PIL import ImageDraw
from _media_common import *

def main():
    rows=[];templates={}
    for f in stage(23)['frames']:
        n=f['row_count']; pitch=f['height_px']/n; inset=max(1,round(.09*pitch))
        template=f"{f['hole_id']}_{f['width_px']}x{f['height_px']}_{n}rows"
        boxes=[]
        for i,s in enumerate(f['sections']):
            top=round(i*pitch)+inset;bottom=round((i+1)*pitch)-inset
            require(0<=top<bottom<=f['height_px'],'Invalid row bounds')
            row={k:v for k,v in f.items() if k!='sections'}
            row.update(source_frame_id=f['id'],source_section_id=s['id'],section=s,region_ordinal=i,
                x_px=0,y_px=top,width_px=f['width_px'],height_px=bottom-top,template_key=template,
                coordinate_basis='native_decoded_pixels_top_left_xy_half_open',region_status='candidate',
                detection_method='source_section_count_equal_pitch_inset_0.09_v1')
            row['row_key']=uid('row-candidate',f['dataset_id'],f['source_snapshot_id'],f['source_log_id'],f['id'],i)
            rows.append(row);boxes.append((0,top,f['width_px'],bottom))
        templates.setdefault(template,[]).append({'frame':f,'boxes':boxes})
    chosen=[]
    # First and last examples of every hole / dimensions / row-count class.
    for key,values in sorted(templates.items()):
        for j in sorted({0,len(values)-1}): chosen.append((key,values[j]))
    for page_start in range(0,len(chosen),12):
        subset=chosen[page_start:page_start+12]; sheet=Image.new('RGB',(1260,math.ceil(len(subset)/3)*205),'white');draw=ImageDraw.Draw(sheet)
        for j,(key,v) in enumerate(subset):
            f=v['frame'];im=decode(source_path(f,f['source_path']));pen=ImageDraw.Draw(im)
            for i,(x,y,r,b) in enumerate(v['boxes']): pen.rectangle((x,y,r-1,b-1),outline=(255,0,70),width=1)
            x=(j%3)*420+8;y=(j//3)*205
            draw.text((x,y+4),f"{key} tray {f['image_ordinal']+1}",fill='black');sheet.paste(im,(x,y+24))
        dest=WORK/f'24_review_{page_start//12+1}.png';sheet.save(dest)
    save_stage(24,{'status':'candidates_ready','rows':rows,'templates':{k:len(v) for k,v in templates.items()},
        'representatives':[{'template_key':k,'source_frame_id':v['frame']['id']} for k,v in chosen],
        'contact_sheets':[p.name for p in sorted(WORK.glob('24_review_*.png'))]})

if __name__=='__main__': main_guard(main)
