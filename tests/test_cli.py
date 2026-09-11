import subprocess
import sys
import time

import pytest

from autiobooks.cli import _parse_chapter_selection


def _make_eta_test_epub(tmp_path):
    """2-chapter epub: chapter 1 carries a huge word count (stands in for
    the "resumed/duplicate" chapter in the §3.9 repro), chapter 2 is tiny
    (stands in for the first chapter that actually needs an ETA)."""
    ebooklib = pytest.importorskip('ebooklib')
    pytest.importorskip('bs4')
    from ebooklib import epub as ebooklib_epub

    book = ebooklib_epub.EpubBook()
    book.set_identifier('eta-test')
    book.set_title('ETA Test')
    book.add_author('Nobody')

    big_text = ' '.join(f'word{i}' for i in range(5000))
    c1 = ebooklib_epub.EpubHtml(title='One', file_name='ch1.xhtml')
    c1.content = f'<html><body><h1>One</h1><p>{big_text}</p></body></html>'
    c2 = ebooklib_epub.EpubHtml(title='Two', file_name='ch2.xhtml')
    c2.content = '<html><body><h1>Two</h1><p>two short words</p></body></html>'
    book.add_item(c1)
    book.add_item(c2)
    book.toc = (
        ebooklib_epub.Link('ch1.xhtml', 'One', 'one'),
        ebooklib_epub.Link('ch2.xhtml', 'Two', 'two'),
    )
    book.add_item(ebooklib_epub.EpubNcx())
    book.add_item(ebooklib_epub.EpubNav())
    book.spine = ['nav', c1, c2]

    path = tmp_path / 'book.epub'
    ebooklib_epub.write_epub(str(path), book)
    return str(path)


class TestConvertEtaExcludesResumedWords:
    """§3.9: a resumed/duplicate-reuse chapter's word count must not seed
    the words/sec ETA rate — those chapters cost ~0 wall clock (no
    synthesis), so crediting their words the instant on_chapter_done
    fires produces a wildly inflated rate and an absurdly small ETA for
    the real remaining work.

    Drives cli.cmd_convert() in-process with engine.convert_chapters_to_wav
    and engine.assemble_output stubbed out — no real TTS/ffmpeg is
    exercised, only the CLI's own ETA bookkeeping around the callbacks.
    """

    def test_resumed_chapter_does_not_seed_eta(self, tmp_path, monkeypatch,
                                               capsys):
        epub_path = _make_eta_test_epub(tmp_path)
        out_path = tmp_path / 'out.m4b'

        from autiobooks import cli, engine

        def fake_convert_chapters_to_wav(
                chapter_texts, voice, speed, wav_dir, stem, executor, *,
                on_chapter_start=None, on_chapter_done=None,
                on_chapter_error=None, on_segment=None, **kwargs):
            total = len(chapter_texts)
            # Chapter 1: resumed/duplicate-reuse — near-instant, per
            # engine.py's documented contract (on_chapter_start(..., True)
            # then on_chapter_done(i, None) with no synthesis). A tiny
            # real sleep makes elapsed time measurable (Windows'
            # time.time() resolution is coarse enough that a same-tick
            # sequence of calls can read back an elapsed of exactly 0.0,
            # which would mask the bug via the `elapsed > 0` guard rather
            # than the fix under test).
            on_chapter_start(1, total, chapter_texts[0], True)
            time.sleep(0.05)
            on_chapter_done(1, None)
            # Chapter 2: real synthesis.
            on_chapter_start(2, total, chapter_texts[1], False)
            on_chapter_done(2, 0.1)
            return {'wav_files': ['a.wav', 'b.wav'],
                    'converted_indices': [0, 1],
                    'encode_futures': {}, 'cancelled': False,
                    'render_key': 'key'}

        monkeypatch.setattr(
            engine, 'convert_chapters_to_wav', fake_convert_chapters_to_wav)
        monkeypatch.setattr(
            engine, 'assemble_output', lambda *a, **k: None)
        monkeypatch.setattr(
            engine, 'set_gpu_acceleration', lambda *a, **k: None)
        monkeypatch.setattr(
            engine, 'get_gpu_acceleration_available', lambda: False)

        # No --chapters: auto_select_indices() drops the leading empty
        # nav-item "chapter" _load_book's list carries ahead of the two
        # real ones, robustly landing on exactly [c1, c2] regardless of
        # how many synthetic entries ebooklib's spine parsing produces.
        args = cli.build_parser().parse_args([
            'convert', epub_path, '-o', str(out_path),
            '--no-titles', '--no-gpu', '--no-resume', '-v',
        ])
        cli.cmd_convert(args)

        err_lines = capsys.readouterr().err.splitlines()
        ch2_line = next(
            line for line in err_lines
            if line.strip().startswith('Chapter 2/2'))
        # Chapter 1 (5000 words) was resumed — free. Only chapter 2's own
        # 3 words have "really" been done by the time its status line
        # prints, and words_done is still 0 at that point (chapter 2
        # itself hasn't finished yet) — so no ETA should be printed at
        # all. Before the fix, chapter 1's word count landed in
        # words_done via the unconditional on_chapter_done credit,
        # producing a bogus "remaining" estimate here.
        assert 'remaining' not in ch2_line


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

    def test_out_of_range_part_warns(self, capsys):
        # "1,99" on a 20-chapter book should still select chapter 1, but
        # must warn that 99 was dropped rather than failing silently.
        assert _parse_chapter_selection("1,99", 20) == [0]
        err = capsys.readouterr().err
        assert "out of range" in err
        assert "99" in err

    def test_reversed_range_warns(self, capsys):
        assert _parse_chapter_selection("10-5", 20) == []
        err = capsys.readouterr().err
        assert "invalid ranges" in err
        assert "10-5" in err

    def test_valid_selection_has_no_warning(self, capsys):
        assert _parse_chapter_selection("1,3-5,8", 10) == [0, 2, 3, 4, 7]
        err = capsys.readouterr().err
        assert err == ""

    def test_partial_range_clamped_without_warning(self, capsys):
        # A range that's only partially out of range is silently clamped —
        # this is documented, expected behavior, not the bug being fixed.
        assert _parse_chapter_selection("1-999", 5) == [0, 1, 2, 3, 4]
        err = capsys.readouterr().err
        assert err == ""

    def test_wholly_out_of_range_range_warns(self, capsys):
        # §3.10: "50-99" on a 20-chapter book must clamp to an empty
        # selection and warn, same as a single out-of-range number.
        assert _parse_chapter_selection("50-99", 20) == []
        err = capsys.readouterr().err
        assert "out of range" in err
        assert "50-99" in err

    def test_zero_zero_range_warns(self, capsys):
        assert _parse_chapter_selection("0-0", 20) == []
        err = capsys.readouterr().err
        assert "out of range" in err

    def test_huge_upper_bound_is_fast(self):
        # §3.10: the loop bounds must be clamped BEFORE iterating, not
        # filtered per-iteration — the old code walked the full literal
        # range (~42M iter/s measured), so "1-999999999999" projected to
        # tens of minutes of pegged CPU with zero output. Clamping first
        # makes this O(matched) instead of O(spec); this must return
        # near-instantly.
        start = time.time()
        result = _parse_chapter_selection("1-999999999999", 5)
        elapsed = time.time() - start
        assert result == [0, 1, 2, 3, 4]
        assert elapsed < 2.0


class TestQuietFatalErrors:
    """`-q` must suppress progress output, not fatal pre-flight errors.

    Runs the CLI in a real subprocess (using the current interpreter —
    venv312/Scripts/python.exe when invoked via that venv's pytest) so this
    exercises entry.py's argv dispatch and cli.py's module-level
    `_verbosity` global exactly as a real invocation would, rather than
    calling cmd_convert() in-process against shared module state.
    """

    def test_missing_input_still_reports_under_quiet(self):
        result = subprocess.run(
            [sys.executable, '-m', 'autiobooks', 'convert', '-q',
             '/nonexistent/missing.epub'],
            capture_output=True, text=True, timeout=60)
        assert result.returncode == 1
        assert result.stderr.strip() != ''
        assert 'not found' in result.stderr.lower()
