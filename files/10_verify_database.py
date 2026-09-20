"""Step 10: verify relational data and HTTP reads, then atomically publish locally."""
import argparse
from _db_common import *
from _verification import verify_and_publish

def main():
    p=argparse.ArgumentParser(); p.add_argument('--holes',nargs='+',choices=ALLOWED,default=list(ALLOWED)); p.add_argument('--no-publish',action='store_true'); a=p.parse_args()
    result=verify_and_publish(list(dict.fromkeys(a.holes)),publish=not a.no_publish)
    report('10_verified_'+('_'.join(a.holes))+'.json',result)
    if set(a.holes)==set(ALLOWED) and not a.no_publish: write(REPORTS/'latest_verified_database.json',result)
    print(f"Verified {len(a.holes)} holes; local release {result['release_id']}")

if __name__=='__main__': main_guard(main)
