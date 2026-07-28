"""
tests/test_version.py
Unit tests for core/version.py  (M0.1)
Run with:  python -m pytest tests/ -v
"""

import re

from core.version import __version__


def test_version_is_nonempty_string():
    assert isinstance(__version__, str)
    assert __version__


def test_version_format():
    assert re.fullmatch(r"\d+\.\d+\.\d+(b\d+)?", __version__)
