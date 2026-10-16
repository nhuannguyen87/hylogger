"""Apply this case only to a fresh processing copy."""

from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from _support import replace_once, case_main


def apply(code_dir):
    replace_once(code_dir, 'processing/metadata_normalization.py',
        r'''                        and max(waves) == 14500
                        else None''',
        r'''                        and max(waves) == 14500
                        else "UNCLASSIFIED"''', count=1)
    replace_once(code_dir, 'processing/metadata_normalization.py',
        r'''"classification_basis": "observed source wavelength axis",''',
        r'''"classification_basis": "unclassified source wavelength axis" if region == "UNCLASSIFIED" else "observed source wavelength axis",''', count=1)


if __name__ == "__main__":
    case_main('09_unclassified_spectral_axis')
