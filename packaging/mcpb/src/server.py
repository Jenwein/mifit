"""Start ``mifit mcp`` for Claude Desktop.

Claude Desktop runs this file with uv (``manifest.json``, server type ``uv``),
after installing the ``mifit`` that ``pyproject.toml`` pins. The extension's
"Allow GPS tracks" switch arrives as the one argument, ``"true"`` or ``"false"``.
"""

import sys

from mifit.cli import main


def mifit_args(argv: list[str]) -> list[str]:
    """The ``mifit`` arguments for this script's ``argv``: ``mcp``, plus
    ``--allow-gps`` only when the switch says exactly ``"true"``."""
    return ["mcp", "--allow-gps"] if argv == ["true"] else ["mcp"]


if __name__ == "__main__":
    sys.exit(main(mifit_args(sys.argv[1:])))
