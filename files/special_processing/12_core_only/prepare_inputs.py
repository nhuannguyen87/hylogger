"""Exclude cuttings datasets and prepare independent core-only inputs."""

from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from dataset_inputs import main


if __name__ == "__main__":
    main("12")

