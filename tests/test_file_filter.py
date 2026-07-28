"""
tests/test_file_filter.py
Unit tests for the file panel's filter matching
Run with:  python -m pytest tests/ -v
"""

from ui.file_panel_widget import stem_matches_filter


class TestStemMatchesFilter:
    def test_empty_query_matches_everything(self):
        assert stem_matches_filter("mri_001", "")

    def test_whitespace_query_matches_everything(self):
        assert stem_matches_filter("mri_001", "   ")

    def test_case_insensitive_substring(self):
        assert stem_matches_filter("MRI_Head_001", "mri")
        assert stem_matches_filter("mri_head_001", "HEAD")

    def test_substring_anywhere(self):
        assert stem_matches_filter("scan_2026_chest", "chest")

    def test_no_match(self):
        assert not stem_matches_filter("mri_head_001", "ct")

    def test_multi_token_requires_all(self):
        assert stem_matches_filter("mri_head_001", "mri 001")
        assert not stem_matches_filter("mri_head_001", "mri 002")

    def test_token_order_irrelevant(self):
        assert stem_matches_filter("mri_head_001", "001 head mri")
