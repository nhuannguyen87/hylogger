"""Write Parquet files and read them back to compare values, nulls, and row counts.
生成 Parquet 文件，并回读比较数值、空值和行数。
"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from processing._etl_common import fail_main, run_holes
from processing.columnar_storage import build_hole


def main():
    run_holes("step 5 build columnar", build_hole)


if __name__ == "__main__":
    fail_main(main)
