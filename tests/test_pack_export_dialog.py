"""
tests/test_pack_export_dialog.py
Unit tests for ui/pack_export_dialog.py

Only pure-Python helpers are tested here — no QApplication required.
Qt widget construction is covered in tests/test_pack_info_dialog.py.
Run with:  python -m pytest tests/ -v
"""

import pytest

from ui.pack_export_dialog import _parse_tags


# ---------------------------------------------------------------------------
# _parse_tags
# ---------------------------------------------------------------------------

class TestParseTags:
    """
    _parse_tags(raw) splits a comma-separated string into a clean list.
    Whitespace is stripped from each token; empty tokens are dropped.
    """

    def test_normal_comma_separated(self):
        assert _parse_tags("CT,head,cohort-A") == ["CT", "head", "cohort-A"]

    def test_strips_whitespace_around_tokens(self):
        assert _parse_tags("  CT  ,  head  ,  cohort-A  ") == ["CT", "head", "cohort-A"]

    def test_mixed_spacing(self):
        assert _parse_tags("CT, head,cohort-A") == ["CT", "head", "cohort-A"]

    def test_empty_string_returns_empty_list(self):
        assert _parse_tags("") == []

    def test_whitespace_only_returns_empty_list(self):
        assert _parse_tags("   ") == []

    def test_commas_only_returns_empty_list(self):
        assert _parse_tags(",,,") == []

    def test_commas_and_spaces_only_returns_empty_list(self):
        assert _parse_tags("  ,  ,  ") == []

    def test_single_tag_no_comma(self):
        assert _parse_tags("CT") == ["CT"]

    def test_single_tag_with_surrounding_spaces(self):
        assert _parse_tags("  CT  ") == ["CT"]

    def test_trailing_comma_dropped(self):
        assert _parse_tags("CT, head,") == ["CT", "head"]

    def test_leading_comma_dropped(self):
        assert _parse_tags(",CT, head") == ["CT", "head"]

    def test_consecutive_commas_produce_no_empty_tokens(self):
        assert _parse_tags("CT,,head") == ["CT", "head"]

    def test_internal_spaces_preserved_within_token(self):
        # "cohort A" is a single tag with an internal space — strip only
        # removes surrounding whitespace, not internal spaces.
        assert _parse_tags("CT, cohort A") == ["CT", "cohort A"]

    def test_returns_list_not_generator(self):
        result = _parse_tags("CT, head")
        assert isinstance(result, list)

    def test_returns_strings(self):
        result = _parse_tags("CT, head")
        assert all(isinstance(t, str) for t in result)

    def test_many_tags(self):
        raw    = ", ".join(str(i) for i in range(20))
        result = _parse_tags(raw)
        assert len(result) == 20
        assert result[0] == "0"
        assert result[-1] == "19"

    def test_unicode_tags(self):
        assert _parse_tags("CT, beyin, kafa") == ["CT", "beyin", "kafa"]