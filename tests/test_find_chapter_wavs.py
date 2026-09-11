"""Stem-wide WAV sweep tests: engine.find_chapter_wavs and the CLI
delete paths built on it.

find_chapter_wavs is the ONLY stem-wide sweep — every other consumer
builds exact paths via chapter_wav_name(). It must treat the stem as a
literal string (book filenames legally contain glob metacharacters, so
a glob either matches nothing or ANOTHER book's files), and its callers
may delete the result only on SUCCESS or an explicit user-initiated
clear: WAVs are kept for resume on cancel/failure.
"""

import sys
from pathlib import Path

import pytest

engine = pytest.importorskip('autiobooks.engine')

from autiobooks import cli  # noqa: E402


def _touch(wav_dir, name):
    p = Path(wav_dir) / name
    p.write_bytes(b'RIFF')
    return p


class TestFindChapterWavs:

    def test_bracketed_stem_matches_literally(self, tmp_path):
        # Old implementation globbed the raw stem: '[Illustrated]' was a
        # character class matching one char, so this returned [].
        stem = 'The Hobbit [Illustrated]'
        wav = _touch(tmp_path, f'{stem}_chapter_a1b2c3d4.wav')
        _touch(tmp_path, f'{stem}_chapter_1_enc.m4a')  # wrong suffix
        _touch(tmp_path, f'{stem}_notes.txt')          # wrong shape
        assert engine.find_chapter_wavs(stem, tmp_path) == [wav]

    @pytest.mark.skipif(sys.platform == 'win32',
                        reason='* and ? are illegal in Windows filenames')
    @pytest.mark.parametrize('stem', ['Book *', 'Book ?', 'What?!'])
    def test_star_and_question_stems_match_literally(self, tmp_path, stem):
        wav = _touch(tmp_path, f'{stem}_chapter_deadbeef.wav')
        assert engine.find_chapter_wavs(stem, tmp_path) == [wav]

    @pytest.mark.skipif(sys.platform == 'win32',
                        reason='* is illegal in Windows filenames')
    def test_star_stem_cannot_match_other_books(self, tmp_path):
        # Inverse hazard: a glob for stem 'Book *' would match EVERY book
        # starting with 'Book ', handing another book's cache to unlink.
        _touch(tmp_path, 'Book of Hours_chapter_deadbeef.wav')
        assert engine.find_chapter_wavs('Book *', tmp_path) == []

    def test_bracket_class_stem_cannot_match_other_book(self, tmp_path):
        # 'Book [a]' as a glob pattern MATCHES 'Book a_chapter_*.wav' —
        # Clear WAVs on one book deleted another book's cache. The literal
        # prefix must keep the two books fully isolated.
        theirs = _touch(tmp_path, 'Book a_chapter_11111111.wav')
        mine = _touch(tmp_path, 'Book [a]_chapter_22222222.wav')
        assert engine.find_chapter_wavs('Book [a]', tmp_path) == [mine]
        assert engine.find_chapter_wavs('Book a', tmp_path) == [theirs]

    def test_prefix_overlap_stems_are_isolated(self, tmp_path):
        # 'Book 2_chapter_x.wav'.startswith('Book_chapter_') is False:
        # the '_chapter_' tail of the prefix seals the stem boundary.
        b1 = _touch(tmp_path, 'Book_chapter_11111111.wav')
        b2 = _touch(tmp_path, 'Book 2_chapter_22222222.wav')
        bish = _touch(tmp_path, 'Bookish_chapter_33333333.wav')
        assert engine.find_chapter_wavs('Book', tmp_path) == [b1]
        assert engine.find_chapter_wavs('Book 2', tmp_path) == [b2]
        assert engine.find_chapter_wavs('Bookish', tmp_path) == [bish]

    def test_all_render_keys_swept(self, tmp_path):
        # The whole point: files hashed under OLD settings (names the
        # current render_key cannot reproduce) are still found.
        old = _touch(tmp_path, 'book_chapter_00000000.wav')
        new = _touch(tmp_path, 'book_chapter_ffffffff.wav')
        assert engine.find_chapter_wavs('book', tmp_path) == [old, new]

    def test_missing_dir_returns_empty(self, tmp_path):
        assert engine.find_chapter_wavs('book', tmp_path / 'nope') == []


class _Ch:
    def __init__(self, text):
        self.extracted_text = text


class _CliHarness:
    """Drive cli.cmd_convert with the engine's TTS/mux stubbed out, real
    find_chapter_wavs/safe_stem/unlink_with_retry underneath."""

    def __init__(self, tmp_path, monkeypatch, tts_raises=None):
        from autiobooks import config as config_mod
        from autiobooks import runtime as runtime_mod

        self.book = tmp_path / 'My Book [Special].epub'
        self.book.write_bytes(b'')
        self.wav_dir = tmp_path
        self.stem = engine.safe_stem(self.book.stem, self.wav_dir)
        self.out = tmp_path / 'out.m4b'
        chapters = [_Ch('Chapter one text.'), _Ch('Chapter two text.')]

        monkeypatch.setattr(cli, '_load_book',
                            lambda p: (object(), chapters, None, False))
        monkeypatch.setattr(cli.shutil, 'which', lambda name: 'ffmpeg')
        monkeypatch.setattr(runtime_mod, 'ensure_bin_in_path', lambda: None)
        monkeypatch.setattr(config_mod, 'load_config', lambda: {})
        monkeypatch.setattr(engine, 'set_gpu_acceleration', lambda on: None)
        monkeypatch.setattr(engine, 'assemble_output',
                            lambda *a, **kw: None)

        harness = self

        def fake_convert(chapter_texts, voice, speed, wav_dir, stem,
                         executor, **kw):
            rkey = engine.render_key(
                voice, speed, kw['chapter_gap'], kw['heteronyms'],
                kw['contractions'], kw['auto_acronyms'],
                kw['substitutions'], kw['phoneme_overrides'])
            wav_files = []
            for t in chapter_texts:
                name = engine.chapter_wav_name(stem, t, wav_dir, rkey)
                Path(name).write_bytes(b'RIFF')
                wav_files.append(name)
                harness.written = list(wav_files)
                if tts_raises is not None:
                    raise tts_raises
            return {'wav_files': wav_files, 'encode_futures': {},
                    'cancelled': False, 'render_key': rkey}

        monkeypatch.setattr(engine, 'convert_chapters_to_wav', fake_convert)

        # An orphan from a hypothetical earlier run under other settings
        # (hash no current render_key reproduces) plus a neighbouring
        # book's cache that no sweep may ever touch.
        self.orphan = _touch(
            self.wav_dir, f'{self.stem}_chapter_00000000.wav')
        self.other_book = _touch(
            self.wav_dir, 'Other Book_chapter_ffffffff.wav')

    def run(self, *extra):
        cli.main(['convert', str(self.book), '-o', str(self.out),
                  '--voice', 'af_heart', '--no-gpu', '--no-titles',
                  '--title', 'T', '--author', 'A',
                  '--no-read-title-author', '-q', *extra])


class TestCliSweepPolicy:

    def test_success_sweeps_orphans_but_not_other_books(
            self, tmp_path, monkeypatch):
        h = _CliHarness(tmp_path, monkeypatch)
        h.run()
        assert not h.orphan.exists()
        assert not any(Path(w).exists() for w in h.written)
        assert h.other_book.exists()

    @pytest.mark.parametrize('exc,code', [
        (KeyboardInterrupt(), 130),
        (RuntimeError('tts blew up'), 1),
    ], ids=['cancel', 'failure'])
    def test_cancel_and_failure_keep_wavs(
            self, tmp_path, monkeypatch, exc, code):
        # WAVs are kept for resume on cancel/failure — the sweep must be
        # unreachable from these paths.
        h = _CliHarness(tmp_path, monkeypatch, tts_raises=exc)
        with pytest.raises(SystemExit) as si:
            h.run()
        assert si.value.code == code
        assert h.orphan.exists()
        assert all(Path(w).exists() for w in h.written)
        assert h.other_book.exists()

    def test_no_resume_predeletes_all_keys(self, tmp_path, monkeypatch):
        # --no-resume clears the book's cache stem-wide up front; the
        # deliberately failing run afterwards proves the deletion happened
        # in the pre-delete, not the success sweep.
        h = _CliHarness(tmp_path, monkeypatch,
                        tts_raises=RuntimeError('tts blew up'))
        with pytest.raises(SystemExit):
            h.run('--no-resume')
        assert not h.orphan.exists()
        assert all(Path(w).exists() for w in h.written)
        assert h.other_book.exists()
