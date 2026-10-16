"""Check independent/composed adapters and finite float32 comparison guards."""

import argparse
import ast
from pathlib import Path
import sys
import tempfile

import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from prepare import CASES, build_copy


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--work-dir", type=Path, required=True)
    args = parser.parse_args()
    args.work_dir.mkdir(parents=True, exist_ok=False)
    with tempfile.TemporaryDirectory(dir=args.work_dir) as directory:
        root = Path(directory)
        for name, folder in CASES.items():
            build_copy(root/name, [folder], ["fixture-dataset"])
            print(f"{name}: compiled", flush=True)
        build_copy(root/"combined", list(CASES.values()), ["fixture-dataset"])
        source = (root/"combined/processing/sample_alignment.py").read_text(encoding="utf-8")
        expression = next(node.value for node in ast.walk(ast.parse(source))
                          if isinstance(node, ast.Assign) and any(isinstance(t, ast.Name) and t.id == "same_float32" for t in node.targets))
        compiled = compile(ast.Expression(expression), "image_float32_guard", "eval")
        examples = [(297.9254, 297.92542, True), (297.9254, 298.0, False), (1e40, 2e40, False)]
        for left, right, expected in examples:
            variables = {"np": np, "a": left, "b": left,
                         "tray": {"observed_depth_min_m": right, "observed_depth_max_m": right}}
            with np.errstate(over="ignore"):
                result = eval(compiled, variables)
            if result != expected:
                raise AssertionError((left, right, result))
        print("Combined adapters and 3 float32 boundary checks passed", flush=True)


if __name__ == "__main__":
    main()
