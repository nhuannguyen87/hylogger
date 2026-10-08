"""Normalize metadata and source identifiers while preserving original documents.
整理元数据和来源标识，同时保存原始文档。
"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from processing._etl_common import fail_main, run_holes
from processing.metadata_normalization import normalize_hole


def main():
    run_holes("step 3 normalize metadata", normalize_hole)


if __name__ == "__main__":
    fail_main(main)
