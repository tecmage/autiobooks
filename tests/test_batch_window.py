"""Tests for the Batch Queue window's singleton + close semantics (§2.3).

These drive a real Toplevel against the session-scoped `tk_root` (see
conftest): the bug being guarded is entirely about Tk window identity and
the module-level `_window`/`_current_running_idx` state, which a stub can't
model. Nothing here starts a conversion — `show_batch_window` only builds
widgets; the worker is spawned by the Start Batch button.
"""

import pytest

pytest.importorskip('tkinter')
batch_window = pytest.importorskip('autiobooks.batch_window')
autiobooks_gui = pytest.importorskip('autiobooks.autiobooks')


def _job(path='book.epub'):
    return autiobooks_gui.BatchJob(
        file_path=path, book=object(), chapters=[],
        selected_chapter_indices=[0], voice='af_heart', speed='1.0',
        chapter_gap='2.0', gpu_acceleration=False, detect_titles=True,
        starting_chapter=1,
    )


def _open(tk_root, queue, tmp_path):
    import tkinter as tk
    return batch_window.show_batch_window(
        tk_root, queue, str(tmp_path),
        prevent_sleep=lambda: __import__('contextlib').nullcontext(),
        prefs={'heteronyms': tk.BooleanVar(master=tk_root, value=False),
               'contractions': tk.BooleanVar(master=tk_root, value=False)},
        get_substitutions=lambda: [],
        get_phoneme_overrides=lambda: [],
        get_auto_acronyms=lambda: False,
    )


@pytest.fixture(autouse=True)
def _reset_module_state():
    """The window ref / run index are module-level so a reopened window can
    adopt a live run — reset around each test so ordering can't leak state."""
    batch_window._window = None
    batch_window._current_running_idx[0] = -1
    batch_window._batch_cancel.clear()
    yield
    if batch_window._window is not None:
        try:
            batch_window._window.destroy()
        except Exception:
            pass
    batch_window._window = None
    batch_window._current_running_idx[0] = -1
    batch_window._batch_cancel.clear()


class TestBatchWindowSingleton:
    def test_second_call_does_not_open_a_second_window(self, tk_root, tmp_path):
        # A second Toplevel used to replace the module-level `_ui` entries
        # wholesale, so window A's progress/status/completion updates went
        # nowhere while A's widgets were still alive and visibly frozen.
        queue = [_job()]
        _open(tk_root, queue, tmp_path)
        first = batch_window._window
        assert first is not None

        _open(tk_root, queue, tmp_path)
        assert batch_window._window is first, 'a second window was created'

    def test_reopen_after_close_creates_a_fresh_window(self, tk_root, tmp_path):
        # The singleton must not wedge the window shut — close-then-reopen
        # is the documented adoption path for a run still draining.
        queue = [_job()]
        _open(tk_root, queue, tmp_path)
        first = batch_window._window
        first.destroy()
        batch_window._window = None

        _open(tk_root, queue, tmp_path)
        assert batch_window._window is not None
        assert batch_window._window is not first

    def test_empty_queue_opens_nothing(self, tk_root, tmp_path, monkeypatch):
        monkeypatch.setattr(batch_window.messagebox, 'showinfo',
                            lambda *a, **k: None)
        _open(tk_root, [], tmp_path)
        assert batch_window._window is None


class TestOnCloseCancelGating:
    """Closing a window called batch_cancel.set() unconditionally, so closing
    a duplicate aborted the run the FIRST window had started — jobs silently
    cancelled without anyone touching Cancel.
    """

    def _close(self, window):
        # Invoke exactly what the window manager's X button invokes.
        window.tk.call(*window.protocol('WM_DELETE_WINDOW').split())

    def test_close_with_no_live_run_does_not_cancel(self, tk_root, tmp_path):
        _open(tk_root, [_job()], tmp_path)
        window = batch_window._window
        batch_window._current_running_idx[0] = -1  # nothing in flight

        self._close(window)
        assert not batch_window._batch_cancel.is_set()
        assert batch_window._window is None

    def test_close_with_live_run_does_cancel(self, tk_root, tmp_path):
        # The gate must not break the real Cancel-on-close behaviour.
        _open(tk_root, [_job()], tmp_path)
        window = batch_window._window
        batch_window._current_running_idx[0] = 0  # job 0 running

        self._close(window)
        assert batch_window._batch_cancel.is_set()
