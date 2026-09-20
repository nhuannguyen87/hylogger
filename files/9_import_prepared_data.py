"""Step 9: transactionally import verified metadata, positions and asset indexes."""
import argparse
from _db_common import *
from _ingest import run_import

def main():
    p=argparse.ArgumentParser(); p.add_argument('--holes',nargs='+',choices=ALLOWED,default=list(ALLOWED)); args=p.parse_args()
    result=run_import(list(dict.fromkeys(args.holes)))
    report('9_import_'+('_'.join(args.holes))+'.json',result)
    print('Staged import complete; publication requires step 10 verification',flush=True)

if __name__=='__main__': main_guard(main)
