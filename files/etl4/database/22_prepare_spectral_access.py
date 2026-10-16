"""Index byte blocks in the original spectra without copying entire arrays.
为原始光谱建立字节块索引，不复制完整数组。
"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import hashlib
from _run_reports import main_guard
from _settings import settings, configure
from _media_common import require, save_stage, source_path, stage


def main(work=None):
    work = settings.media_work_dir if work is None else work
    arrays = []
    for a in stage(21, work)["spectra"]:
        if not a["file"]:
            continue
        require(a["per_sample_publication_allowed"], "Unverified spectra cannot be served")
        path = source_path(a, a["file"])
        n, c = a["shape"]
        stride = c * 4
        blocks = []
        with path.open("rb") as f:
            for start in range(0, n, 1024):
                count = min(1024, n - start)
                data = f.read(count * stride)
                require(len(data) == count * stride, "Short spectral block")
                blocks.append(
                    {
                        "block_no": len(blocks),
                        "source_row_from": start,
                        "source_row_to_exclusive": start + count,
                        "byte_offset": start * stride,
                        "byte_length": len(data),
                        "sha256": hashlib.sha256(data).hexdigest(),
                    }
                )
            require(f.read(1) == b"", "Trailing spectral bytes")
        arrays.append(
            {
                **a,
                "blocks": blocks,
                "data_offset_bytes": 0,
                "sample_stride_bytes": stride,
                "channel_stride_bytes": 4,
                "access_method": "original_f32_byte_range",
                "scaling_applied": False,
                "sample_map": {
                    "kind": "identity",
                    "source_row_from": 0,
                    "source_row_to": n - 1,
                    "sample_no_from": 0,
                    "sample_no_to": n - 1,
                },
            }
        )
    save_stage(22, {"status": "passed", "arrays": arrays, "array_bytes_duplicated": 0}, work=work)


if __name__ == "__main__":
    from _settings import StepParser

    args = StepParser().parse_args()
    configure(args)
    main_guard(main)
