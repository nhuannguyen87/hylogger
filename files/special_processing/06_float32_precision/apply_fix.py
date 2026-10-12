"""Apply this case only to a fresh processing copy."""

from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from _support import replace_once, case_main


def apply(code_dir):
    replace_once(code_dir, 'processing/sample_alignment.py',
        r'''def align_hole(hole, image_depth_policy="decimal_1e-5", context=None):''',
        r'''def align_hole(hole, image_depth_policy="decimal_1e-5_or_same_float32", context=None):''', count=1)
    replace_once(code_dir, 'processing/sample_alignment.py',
        r'''np.float32(x).tobytes() == np.float32(y).tobytes()''',
        r'''(np.isfinite(np.float32(x)) and np.isfinite(np.float32(y)) and np.float32(x).tobytes() == np.float32(y).tobytes())''', count=1)
    replace_once(code_dir, 'processing/sample_alignment.py',
        r'''            if not np.isfinite(differences).all() or not (differences <= 1e-4).all():''',
        r'''            if np.isfinite(differences).all() and not (differences <= 1e-4).all():
                left = values[np.isfinite(values)]
                right = positive_min[np.isfinite(values)]
                left32, right32 = left.astype(np.float32), right.astype(np.float32)
                if not (np.isfinite(left32).all() and np.isfinite(right32).all()
                        and np.array_equal(left32.view(np.uint32), right32.view(np.uint32))):
                    raise ValueError("Profile references differ at float32 precision")
                issues.append(issue("PROFILE_REFERENCE_TEXT_PRECISION", hole,
                    "All source values have identical finite float32 representations; original decimals retained.",
                    "info", {"scalar_log_id": entry["log_id"], "max_absolute_difference": float(differences.max())}))
                differences_for_check = np.zeros_like(differences)
            else:
                differences_for_check = differences
            if not np.isfinite(differences_for_check).all() or not (differences_for_check <= 1e-4).all():''', count=1)


if __name__ == "__main__":
    case_main('06_float32_precision')
