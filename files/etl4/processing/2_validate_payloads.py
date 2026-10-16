"""Check file hashes and formats while recording explainable source issues.
逐文件检查哈希与数据格式，保留可解释的来源问题。
"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from processing._etl_common import fail_main, run_holes
from processing.payload_validation import validate_hole


def main():
    run_holes("step 2 validate payloads", validate_hole)


if __name__ == "__main__":
    fail_main(main)
