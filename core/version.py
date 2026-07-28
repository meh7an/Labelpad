"""
core/version.py
Single source of truth for the application version.

The release ritual depends on this file: CI refuses to build any tag that does
not equal "v" + __version__ (see .github/workflows/build.yml), so the shipped
artifacts can never disagree with what the app believes it is running.
"""

__version__ = "1.4.1"
