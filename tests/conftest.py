"""Shared pytest fixtures.

`tk_root` is the ONE Tk root for the whole test session, used by every
module that needs a live Tk interpreter (test_runtime.py, test_dialogs.py).
It is session-scoped and created lazily on first use, never per-module or
per-test: creating a second `tk.Tk()` interpreter later in the same process
— which is what happened when test_runtime.py and test_dialogs.py each had
their own module-scoped root — intermittently fails with
`_tkinter.TclError: Can't find a usable init.tcl in the following
directories: ...` on this Tcl build. That failure is order-dependent (it
only shows up when a second module's root is created after an earlier
module's root has already been created/destroyed in the same process) and
timing-sensitive (not 100% reproducible even with a fixed pytest-randomly
seed), which is exactly the "~1 run in 6" flake pytest-randomly's shuffling
exposed. A single root created once per process sidesteps the second-Tk()
codepath entirely.

No test may call `tk.Tk()` / `root.destroy()` itself. Widgets a test opens
(Toplevels, dialogs) must be cleaned up per-test in the test itself (as
test_dialogs.py already does in a try/finally) — that's independent of this
fixture and doesn't touch the shared root.
"""

import pytest


@pytest.fixture(scope='session')
def tk_root():
    """Session-scoped Tk root, shared by every test module. Skips cleanly
    (rather than erroring) when no display/Tk is available, so the suite
    stays runnable on a headless box. Never destroyed explicitly — living
    for the rest of the process is fine, and avoids re-triggering the
    flaky second-interpreter TclError this fixture exists to dodge."""
    tk = pytest.importorskip('tkinter')
    try:
        root = tk.Tk()
    except tk.TclError as exc:
        pytest.skip(f'Tk display unavailable: {exc}')
    root.withdraw()
    return root
