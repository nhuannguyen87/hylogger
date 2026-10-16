"""Apply this case only to a fresh processing copy."""

from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from _support import replace_once, case_main


def apply(code_dir):
    replace_once(code_dir, '_settings.py',
        r'''    if any(not isinstance(h, str) or not re.fullmatch(r"[A-Za-z0-9_-]+", h) for h in holes):
        raise ValueError("Borehole IDs may contain only letters, digits, underscores, and hyphens")
''',
        r'''    # Accept spaces and percent-encoded names, while rejecting path separators.
    if any(not isinstance(h, str) or not re.fullmatch(r"(?:[A-Za-z0-9_ -]|%[0-9A-Fa-f]{2})+", h)
           or h != h.strip() or h.upper() in {"CON", "PRN", "AUX", "NUL", *(f"COM{i}" for i in range(1, 10)), *(f"LPT{i}" for i in range(1, 10))}
           for h in holes):
        raise ValueError("Borehole directory names must be safe single path components")
''', count=1)
    replace_once(code_dir, 'processing/0_check_raw.py',
        r'''import uuid
''',
        r'''import uuid
from urllib.parse import unquote
''', count=1)
    replace_once(code_dir, 'processing/0_check_raw.py',
        r'''data["borehole.json"]["nvcl_id"] != folder.name''',
        r'''str(data["borehole.json"]["nvcl_id"]).strip() != unquote(folder.name)''', count=1)
    replace_once(code_dir, 'processing/0_check_raw.py',
        r'''package.get("hole_id") != folder.name''',
        r'''package.get("hole_id") != unquote(folder.name)''', count=1)
    replace_once(code_dir, 'processing/0_check_raw.py',
        r'''    package = data.get("package_manifest.json", {})
''',
        r'''    package = data.get("package_manifest.json", {})
    identity_file = folder / "source_identity.json"
    if identity_file.exists():
        try:
            identity = json.loads(identity_file.read_text(encoding="utf-8-sig"))
            if (identity.get("local_directory") != folder.name
                    or identity.get("hole_id") != unquote(folder.name)
                    or identity.get("hole_id") != data.get("package_manifest.json", {}).get("hole_id")
                    or identity.get("provider") != data.get("package_manifest.json", {}).get("provider")):
                errors.append("Source identity disagrees with the package or directory")
        except (OSError, ValueError, AttributeError) as error:
            errors.append(f"Invalid source_identity.json: {error}")
    elif unquote(folder.name) != folder.name:
        errors.append("Encoded directory names require source_identity.json")
''', count=1)
    replace_once(code_dir, 'processing/1_inventory.py',
        r'''    references = {}
''',
        r'''    # Check directory mappings without rewriting source identifiers.
    from urllib.parse import unquote
    expected_hole = unquote(hole)
    if (str(metadata["borehole.json"]["nvcl_id"]).strip() != expected_hole
            or package.get("hole_id") != expected_hole):
        raise ValueError("Source hole identity disagrees with the local directory")
    identity_path = source_path(hole, "source_identity.json")
    if identity_path.exists():
        identity = source_json(hole, "source_identity.json")
        if (identity.get("local_directory") != hole
                or identity.get("hole_id") != expected_hole
                or identity.get("provider") != package.get("provider")):
            raise ValueError("Source identity disagrees with the package or local directory")
    elif expected_hole != hole:
        raise ValueError("Encoded directory names require source_identity.json")
    references = {}
''', count=1)
    replace_once(code_dir, 'processing/1_inventory.py',
        r'''(*METADATA, "download-report.txt")''',
        r'''(*METADATA, "download-report.txt", "source_identity.json")''', count=1)


if __name__ == "__main__":
    case_main('01_source_identity')
