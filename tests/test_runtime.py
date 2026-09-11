"""Regression tests for AUDIT_2026-07-16 findings §5.5, §5.6, and §5.9.

§5.5: ensure_ffmpeg() must not exit mute when a broken ffmpeg is found on
PATH — every other failure exit from this function shows a dialog, but the
two bare `return False`s here used to show nothing (the tkinter import ran
strictly after both returns, making the muteness structural).

§5.6: ffprobe is called just as often as ffmpeg (engine.py's duration/
chapter probing) but was never validated — a host with a working ffmpeg but
no/broken ffprobe passed ensure_ffmpeg's gate and failed invisibly, deep
inside a conversion, hours later. The post-extract flatten loop (moving
files out of the zip's nested bin/ directory) also lacked the .tmp +
os.replace atomicity every other write in the extraction pipeline has.

§5.9: the ffmpeg zip download had no truncation check, no .part staging,
and no retry — CPython's HTTPResponse.read() returns b'' (not an
exception) on a dropped non-chunked connection, so a truncated download
used to look like a clean one and only failed later, confusingly, at
zipfile.ZipFile() with a message that says nothing about the network.

All tests run against fakes (monkeypatched shutil.which/subprocess.run/
urlopen) and a tmp_path standing in for BIN_DIR — no real network access
and no real ffmpeg/ffprobe binary required.
"""

import io
import os
import zipfile
from types import SimpleNamespace

import pytest

runtime = pytest.importorskip('autiobooks.runtime')
tk = pytest.importorskip('tkinter')
from tkinter import messagebox  # noqa: E402 — submodule, needs its own import


def _make_fake_ffmpeg_zip_bytes(exe_names=('ffmpeg.exe', 'ffprobe.exe')):
    """A minimal zip shaped like the real BtbN release: one root dir, a
    nested bin/ subdirectory — this is exactly the layout that forces the
    post-extract flatten loop to run."""
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, 'w') as zf:
        for name in exe_names:
            zf.writestr(f'ffmpeg-master-latest-win64-gpl/bin/{name}',
                       b'fake binary content')
    return buf.getvalue()


class _FakeResponse:
    """Stands in for urlopen()'s return value. Optionally serves fewer
    bytes than Content-Length claims, to exercise the §5.9 truncation
    check (mirrors HTTPResponse's real behavior: read() returns b'' on a
    dropped connection instead of raising)."""

    def __init__(self, data, truncate_to=None):
        self._full_len = len(data)
        self._data = data if truncate_to is None else data[:truncate_to]
        self._pos = 0
        self.headers = {'Content-Length': str(self._full_len)}

    def read(self, n=-1):
        if n < 0:
            n = len(self._data) - self._pos
        chunk = self._data[self._pos:self._pos + n]
        self._pos += len(chunk)
        return chunk

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False


@pytest.fixture(autouse=True)
def _restore_path(monkeypatch):
    # ensure_ffmpeg calls ensure_bin_in_path(), which mutates os.environ
    # directly (not via monkeypatch) — register the current value so
    # monkeypatch's teardown restores it regardless of later mutation.
    monkeypatch.setenv('PATH', os.environ.get('PATH', ''))


class TestDownloadFileTruncationCheck:
    """§5.9's core new mechanism, isolated from the GUI/threading layer."""

    def test_raises_ioerror_when_short_of_content_length(self, tmp_path, monkeypatch):
        data = b'x' * 1000

        def fake_urlopen(req, timeout=None):
            return _FakeResponse(data, truncate_to=400)

        monkeypatch.setattr(runtime, 'urlopen', fake_urlopen)
        dest = tmp_path / 'out.bin'
        with pytest.raises(IOError, match='truncated'):
            runtime._download_file('http://example.invalid/f', dest)

    def test_succeeds_when_full_content_delivered(self, tmp_path, monkeypatch):
        data = b'x' * 1000

        def fake_urlopen(req, timeout=None):
            return _FakeResponse(data)

        monkeypatch.setattr(runtime, 'urlopen', fake_urlopen)
        dest = tmp_path / 'out.bin'
        runtime._download_file('http://example.invalid/f', dest)
        assert dest.read_bytes() == data


class TestEnsureFfmpegBrokenNotMute:
    """§5.5: a broken-but-present ffmpeg must show a dialog, not exit
    silently. §5.6: ffmpeg-only validation (ffprobe missing) must also not
    silently pass."""

    def test_foreign_broken_ffmpeg_reports_instead_of_exiting_mute(
            self, tmp_path, monkeypatch, tk_root):
        fake_ffmpeg = tmp_path / 'foreign' / 'ffmpeg'
        fake_ffmpeg.parent.mkdir(parents=True)
        fake_ffmpeg.write_text('not a real binary')

        monkeypatch.setattr(
            runtime.shutil, 'which',
            lambda name: str(fake_ffmpeg) if name == 'ffmpeg' else None)
        monkeypatch.setattr(
            runtime.subprocess, 'run',
            lambda cmd, **kw: SimpleNamespace(returncode=1))

        calls = []
        monkeypatch.setattr(messagebox, 'showerror',
                            lambda title, msg: calls.append((title, msg)))

        result = runtime.ensure_ffmpeg(root=tk_root)

        assert result is False
        assert len(calls) == 1
        assert calls[0][0] == 'FFmpeg Broken'

    def test_unremovable_managed_copy_reports_instead_of_exiting_mute(
            self, tmp_path, monkeypatch, tk_root):
        bin_dir = tmp_path / 'bin'
        bin_dir.mkdir(parents=True)
        monkeypatch.setattr(runtime, 'BIN_DIR', bin_dir)

        target = bin_dir / 'ffmpeg'
        target.write_text('broken')
        monkeypatch.setattr(
            runtime.shutil, 'which',
            lambda name: str(target) if name == 'ffmpeg' else None)
        monkeypatch.setattr(
            runtime.subprocess, 'run',
            lambda cmd, **kw: SimpleNamespace(returncode=1))

        from pathlib import Path as RealPath
        orig_unlink = RealPath.unlink

        def flaky_unlink(self, *a, **kw):
            if str(self) == str(target):
                raise PermissionError('locked by another process')
            return orig_unlink(self, *a, **kw)

        monkeypatch.setattr(RealPath, 'unlink', flaky_unlink)

        calls = []
        monkeypatch.setattr(messagebox, 'showerror',
                            lambda title, msg: calls.append((title, msg)))

        result = runtime.ensure_ffmpeg(root=tk_root)

        assert result is False
        assert len(calls) == 1
        assert calls[0][0] == 'FFmpeg Broken'

    def test_working_ffmpeg_without_ffprobe_does_not_pass_silently(
            self, tmp_path, monkeypatch, tk_root):
        fake_ffmpeg = tmp_path / 'ffmpeg'
        fake_ffmpeg.write_text('x')

        monkeypatch.setattr(
            runtime.shutil, 'which',
            lambda name: str(fake_ffmpeg) if name == 'ffmpeg' else None)
        monkeypatch.setattr(
            runtime.subprocess, 'run',
            lambda cmd, **kw: SimpleNamespace(returncode=0))

        calls = []
        monkeypatch.setattr(messagebox, 'showerror',
                            lambda title, msg: calls.append((title, msg)))

        result = runtime.ensure_ffmpeg(root=tk_root)

        # Must not silently return True on ffmpeg alone (§5.6), and must
        # not exit mute either (§5.5) — both regressions share one gate.
        assert result is False
        assert len(calls) == 1

    def test_working_ffmpeg_and_ffprobe_returns_true(self, tmp_path, monkeypatch, tk_root):
        fake_ffmpeg = tmp_path / 'ffmpeg'
        fake_ffprobe = tmp_path / 'ffprobe'
        fake_ffmpeg.write_text('x')
        fake_ffprobe.write_text('x')

        monkeypatch.setattr(
            runtime.shutil, 'which',
            lambda name: str(fake_ffmpeg) if name == 'ffmpeg'
            else (str(fake_ffprobe) if name == 'ffprobe' else None))
        monkeypatch.setattr(
            runtime.subprocess, 'run',
            lambda cmd, **kw: SimpleNamespace(returncode=0))

        calls = []
        monkeypatch.setattr(messagebox, 'showerror',
                            lambda title, msg: calls.append((title, msg)))

        assert runtime.ensure_ffmpeg(root=tk_root) is True
        assert calls == []


class TestEnsureFfmpegDownloadFlow:
    """End-to-end through the (mocked) download dialog: exercises the
    §5.9 .part staging + testzip() + retry, the §5.6 atomic flatten loop,
    and the §5.6 ffprobe post-extract validation together."""

    def _patch_common(self, monkeypatch, tmp_path, urlopen_fn):
        bin_dir = tmp_path / 'bin'
        bin_dir.mkdir(parents=True)
        monkeypatch.setattr(runtime, 'BIN_DIR', bin_dir)
        monkeypatch.setattr(runtime.shutil, 'which', lambda name: None)
        monkeypatch.setattr(runtime, 'urlopen', urlopen_fn)
        monkeypatch.setattr(
            runtime.subprocess, 'run',
            lambda cmd, **kw: SimpleNamespace(returncode=0))
        return bin_dir

    def test_clean_download_extracts_and_flattens(self, tmp_path, monkeypatch, tk_root):
        zip_bytes = _make_fake_ffmpeg_zip_bytes()

        def fake_urlopen(req, timeout=None):
            return _FakeResponse(zip_bytes)

        bin_dir = self._patch_common(monkeypatch, tmp_path, fake_urlopen)

        result = runtime.ensure_ffmpeg(root=tk_root)

        assert result is True
        # Flattened out of the nested bin/ dir directly into BIN_DIR...
        assert (bin_dir / 'ffmpeg.exe').exists()
        assert (bin_dir / 'ffprobe.exe').exists()
        # ...and the now-empty nested dir + intermediates are gone.
        assert not (bin_dir / 'bin').exists()
        assert not list(bin_dir.glob('*.tmp'))
        assert not list(bin_dir.glob('*.part'))

    def test_truncated_download_retries_once_and_succeeds(self, tmp_path, monkeypatch, tk_root):
        zip_bytes = _make_fake_ffmpeg_zip_bytes()
        attempts = {'n': 0}

        def fake_urlopen(req, timeout=None):
            attempts['n'] += 1
            # First attempt: truncated (simulates a dropped connection).
            # Second attempt: complete.
            truncate_to = len(zip_bytes) // 2 if attempts['n'] == 1 else None
            return _FakeResponse(zip_bytes, truncate_to=truncate_to)

        bin_dir = self._patch_common(monkeypatch, tmp_path, fake_urlopen)

        result = runtime.ensure_ffmpeg(root=tk_root)

        assert attempts['n'] == 2
        assert result is True
        assert (bin_dir / 'ffmpeg.exe').exists()

    def test_two_truncated_downloads_give_up_without_crashing(self, tmp_path, monkeypatch, tk_root):
        zip_bytes = _make_fake_ffmpeg_zip_bytes()
        attempts = {'n': 0}

        def fake_urlopen(req, timeout=None):
            attempts['n'] += 1
            return _FakeResponse(zip_bytes, truncate_to=len(zip_bytes) // 2)

        bin_dir = self._patch_common(monkeypatch, tmp_path, fake_urlopen)

        calls = []
        monkeypatch.setattr(messagebox, 'showerror',
                            lambda title, msg: calls.append((title, msg)))

        result = runtime.ensure_ffmpeg(root=tk_root)

        # One retry only — the third attempt never happens.
        assert attempts['n'] == 2
        assert result is False
        assert len(calls) == 1
        assert calls[0][0] == 'Download Error'
        assert 'truncated' in calls[0][1].lower()
        # Non-destructive: no leftover .part masquerading as a good zip.
        assert not (bin_dir / 'ffmpeg.exe').exists()
