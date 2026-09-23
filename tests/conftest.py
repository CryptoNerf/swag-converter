"""Shared fixtures.

The important one is not a fixture at all: it redirects the settings file
away from the real user's before anything imports the window.  Constructing a
Bridge writes preferences, and a test run had been quietly rewriting the
preferences of whoever ran it.
"""

from __future__ import annotations

import os
from pathlib import Path
import tempfile

from swag_converter.gui.store import HOME_VARIABLE

_SANDBOX = Path(tempfile.mkdtemp(prefix="swag-tests-home-"))
os.environ[HOME_VARIABLE] = str(_SANDBOX)


def pytest_report_header() -> str:
    return f"settings sandboxed to {_SANDBOX}"
