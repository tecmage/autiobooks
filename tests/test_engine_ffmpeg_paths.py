"""Regression tests for AUDIT_2026-07-16 findings §2.7 and §2.8.

§2.7: per-chapter encode paths (and, as defense-in-depth, WAV paths) must
be absolutized — a relative dash-leading name (CLI `convert ./-draft.epub`
in a CWD-relative wav_dir) breaks ffmpeg's output positional (ffmpeg has no
'--' end-of-options marker), so the WAV pass completes but every encode
future then raises 'Chapter encoding failed', surfacing only after the
entire TTS run at assemble_output's future.result().

§2.8: _probe_chapters/_probe_format_tags decode ffprobe's stdout with
text=True, encoding='utf-8' but (before this fix) no errors=, unlike every
other text-mode subprocess call in the module. A strict decode failure
raises inside subprocess.run itself, so _check_probe never runs and the
intended RuntimeError (carrying ffprobe's stderr tail) is never built.
"""

from types import SimpleNamespace

import pytest

engine = pytest.importorskip('autiobooks.engine')


class _FakeFuture:
    def cancel(self):
        pass


class _FakeExecutor:
    """Stand-in for the real ThreadPoolExecutor — records submit() calls
    without touching ffmpeg, so the resume path can be exercised without a
    real encoder."""

    def __init__(self):
        self.calls = []

    def submit(self, fn, *args):
        self.calls.append(args)
        return _FakeFuture()


class TestChapterWavNameAbsolutized:
    def test_relative_wav_dir_yields_absolute_path(self, tmp_path, monkeypatch):
        monkeypatch.chdir(tmp_path)
        name = engine.chapter_wav_name('-draft', 'some text', '.', 'key')
        assert __import__('os').path.isabs(name)

    def test_already_absolute_wav_dir_is_unchanged_in_meaning(self, tmp_path):
        name = engine.chapter_wav_name('book', 'some text', str(tmp_path), 'key')
        assert __import__('os').path.isabs(name)
        assert name.startswith(str(tmp_path))


class TestEncodeFilenameAbsolutized:
    """Reproduces the §2.7 repro scenario: wav_dir='.' (CLI's Path('.') for
    a book in the CWD) + a dash-leading stem (CLI `convert ./-draft.epub`).
    Uses the resume branch so no real TTS/ffmpeg runs — only the path
    construction and the encode_executor.submit() call are exercised."""

    def test_resume_branch_submits_absolute_enc_path(self, tmp_path, monkeypatch):
        monkeypatch.chdir(tmp_path)
        text = 'Some chapter text.\n'
        rkey = engine.render_key('af_heart', 1.0, 0.0, True, True, False, None, None)
        wav_filename = engine.chapter_wav_name('-draft', text, '.', rkey)
        # Pre-create the WAV so convert_chapters_to_wav takes the resume
        # branch (no real synthesis needed) — the enc_filename hazard lives
        # in the loop body reached by every branch, resume included.
        import pathlib
        pathlib.Path(wav_filename).parent.mkdir(parents=True, exist_ok=True)
        pathlib.Path(wav_filename).write_bytes(b'')

        fake_exec = _FakeExecutor()
        result = engine.convert_chapters_to_wav(
            [text], 'af_heart', 1.0, '.', '-draft', fake_exec,
            resume=True)

        assert not result['cancelled']
        assert len(fake_exec.calls) == 1
        _wav_path, enc_path, _out_format, _bitrate, _vbr = fake_exec.calls[0]
        assert __import__('os').path.isabs(enc_path), (
            f'enc_filename must be absolutized, got {enc_path!r}')


class TestProbeErrorsReplace:
    """Both probe helpers must pass errors='replace' like every other
    text-mode subprocess call in the module (probe_duration already had
    it; these two didn't)."""

    @staticmethod
    def _make_fake_run(require_errors_replace, stdout):
        def fake_run(cmd, **kwargs):
            if kwargs.get('errors') != 'replace':
                # Mirrors the real failure mode: a strict UTF-8 decode
                # inside subprocess.run raises before _check_probe ever
                # gets to run, so the friendly RuntimeError is never built.
                raise UnicodeDecodeError(
                    'utf-8', b'\xff', 0, 1, 'invalid start byte')
            return SimpleNamespace(returncode=0, stdout=stdout, stderr='')
        return fake_run

    def test_probe_chapters_survives_non_utf8_bytes(self, monkeypatch):
        fake_run = self._make_fake_run(True, '{"chapters": []}')
        monkeypatch.setattr(engine.subprocess, 'run', fake_run)
        assert engine._probe_chapters('some/file.m4b') == []

    def test_probe_format_tags_survives_non_utf8_bytes(self, monkeypatch):
        fake_run = self._make_fake_run(True, '{"format": {"tags": {}}}')
        monkeypatch.setattr(engine.subprocess, 'run', fake_run)
        assert engine._probe_format_tags('some/file.m4b') == {}

    def test_probe_chapters_passes_errors_replace_kwarg(self, monkeypatch):
        captured = {}

        def fake_run(cmd, **kwargs):
            captured.update(kwargs)
            return SimpleNamespace(returncode=0, stdout='{"chapters": []}',
                                   stderr='')
        monkeypatch.setattr(engine.subprocess, 'run', fake_run)
        engine._probe_chapters('some/file.m4b')
        assert captured.get('errors') == 'replace'

    def test_probe_format_tags_passes_errors_replace_kwarg(self, monkeypatch):
        captured = {}

        def fake_run(cmd, **kwargs):
            captured.update(kwargs)
            return SimpleNamespace(returncode=0,
                                   stdout='{"format": {"tags": {}}}',
                                   stderr='')
        monkeypatch.setattr(engine.subprocess, 'run', fake_run)
        engine._probe_format_tags('some/file.m4b')
        assert captured.get('errors') == 'replace'
