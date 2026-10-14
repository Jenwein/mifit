"""Make the ``src`` layout importable for tests without installing the package.

Packaging (pyproject.toml, console entry points) is added in a later task; until
then tests resolve ``mifit`` by putting ``src`` on ``sys.path``.
"""

import sys
from pathlib import Path

SRC = Path(__file__).resolve().parents[1] / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))
