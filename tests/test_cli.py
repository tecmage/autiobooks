import pytest

from autiobooks.cli import _parse_chapter_selection


class TestParseChapterSelection:
    """Tests for cli._parse_chapter_selection(spec, total)."""

    def test_single_number(self):
        assert _parse_chapter_selection("1", 10) == [0]

    def test_comma_list(self):
        assert _parse_chapter_selection("1,3", 10) == [0, 2]

    def test_range(self):
        assert _parse_chapter_selection("1-3", 10) == [0, 1, 2]

    def test_mixed(self):
        assert _parse_chapter_selection("1,3-5,8", 10) == [0, 2, 3, 4, 7]

    def test_whitespace_tolerated(self):
        assert _parse_chapter_selection(" 1 , 3 - 5 , 8 ", 10) == [0, 2, 3, 4, 7]

    def test_reverse_range_yields_nothing(self):
        # "5-3" produces range(5, 4) which is empty.
        assert _parse_chapter_selection("5-3", 10) == []

    def test_zero_is_skipped(self):
        # Chapter numbers are 1-based; 0 is out of bounds.
        assert _parse_chapter_selection("0-2", 10) == [0, 1]

    def test_upper_bound_clamped(self):
        # Values past `total` are silently dropped.
        assert _parse_chapter_selection("1-999", 5) == [0, 1, 2, 3, 4]

    def test_past_upper_bound_is_empty(self):
        assert _parse_chapter_selection("6,7,8", 5) == []

    def test_duplicates_deduplicated_and_sorted(self):
        # "3,1,3-4" picks up 1, 3, 4 — sorted and unique.
        assert _parse_chapter_selection("3,1,3-4", 10) == [0, 2, 3]

    def test_non_numeric_raises(self):
        with pytest.raises(ValueError):
            _parse_chapter_selection("abc", 10)

    def test_empty_string_raises(self):
        with pytest.raises(ValueError):
            _parse_chapter_selection("", 10)

    def test_range_with_non_numeric_raises(self):
        with pytest.raises(ValueError):
            _parse_chapter_selection("1-abc", 10)
