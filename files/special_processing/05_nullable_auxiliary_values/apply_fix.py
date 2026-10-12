"""Apply this case only to a fresh processing copy."""

from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from _support import replace_once, case_main


def apply(code_dir):
    path = code_dir / "processing/sample_alignment.py"
    content = path.read_text(encoding="utf-8")
    if '                # SPECIAL_VIRTUAL_FALLBACK: Entry for explicit special handling.\n' in content:
        replace_once(code_dir, "processing/sample_alignment.py", '                # SPECIAL_VIRTUAL_FALLBACK: Entry for explicit special handling.\n', r'''                # SPECIAL_VIRTUAL_FALLBACK: Entry for explicit special handling.
                present = np.isfinite(actual_virtual)
                if (~present).any():
                    if not present.any() or not np.isnan(actual_virtual[~present]).all() or not np.array_equal(actual_virtual[present], expected[present]):
                        raise ValueError("Non-null virtual_section values disagree with declared intervals")
                    issues.append(issue("VIRTUAL_SECTION_PARTIALLY_NULL", hole,
                        "Source nulls retained; non-null labels, complete intervals and all position logs verified.",
                        "info", {"source_log_id": entry["log_id"], "null_count": int((~present).sum())}))
                    continue
''')
    else:
        replace_once(code_dir, "processing/sample_alignment.py", r'''            if not np.array_equal(numeric_values(hole, entry), expected):
                raise ValueError("virtual_section differs from declared Section intervals")''', r'''            actual_virtual = numeric_values(hole, entry)
            if not np.array_equal(actual_virtual, expected):
                present = np.isfinite(actual_virtual)
                if (~present).any():
                    if not present.any() or not np.isnan(actual_virtual[~present]).all() or not np.array_equal(actual_virtual[present], expected[present]):
                        raise ValueError("Non-null virtual_section values disagree with declared intervals")
                    issues.append(issue("VIRTUAL_SECTION_PARTIALLY_NULL", hole,
                        "Source nulls retained; non-null labels, complete intervals and all position logs verified.",
                        "info", {"source_log_id": entry["log_id"], "null_count": int((~present).sum())}))
                    continue
                raise ValueError("virtual_section differs from declared Section intervals")''')
    replace_once(code_dir, 'processing/sample_alignment.py',
        r'''            differences = np.abs(values - positive_min)
            if not np.isfinite(differences).all() or not (differences <= 1e-4).all():''',
        r'''            # Nulls provide no comparison evidence; infinity is not null.
            comparable = np.isfinite(values)
            if not comparable.any() or not np.isnan(values[~comparable]).all():
                raise ValueError("Invalid or entirely absent profile reference values")
            differences = np.abs(values[comparable] - positive_min[comparable])
            if not np.isfinite(differences).all() or not (differences <= 1e-4).all():''', count=1)
    replace_once(code_dir, 'processing/sample_alignment.py',
        r'''                    "tolerance": 1e-4,''',
        r'''                    "compared_sample_count": int(comparable.sum()),
                    "source_null_reference_count": int((~comparable).sum()),
                    "tolerance": 1e-4,''', count=1)


if __name__ == "__main__":
    case_main('05_nullable_auxiliary_values')
