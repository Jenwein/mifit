"""Filesystem locations used by the SDK.

Per-user state lives in a ``.mifit`` directory under the user's home directory,
outside any repository checkout. The repo's ``.gitignore`` also ignores
``.mifit/`` so that a stray copy of this directory inside a working tree can
never be committed.
"""

from __future__ import annotations

from pathlib import Path

APP_DIR_NAME = ".mifit"
SESSION_FILE_NAME = "session.json"


def app_data_dir() -> Path:
    """Return the per-user application data directory (not created here)."""
    return Path.home() / APP_DIR_NAME


def default_session_path() -> Path:
    """Return the default location of the persisted login session."""
    return app_data_dir() / SESSION_FILE_NAME
