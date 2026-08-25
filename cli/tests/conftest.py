import sys
from pathlib import Path

# The package lives one level up; tests run without an editable install so that
# `uv run pytest` works straight from a clone.
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
