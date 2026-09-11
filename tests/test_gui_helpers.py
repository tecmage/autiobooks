"""Tests for small pure helpers extracted from autiobooks.py.

Importing autiobooks.autiobooks pulls in tkinter/pygame/kokoro at module
scope (no Tk instance is created just by importing), so this module is
skipped in environments missing those optional GUI/audio dependencies.
"""

import pytest

autiobooks_gui = pytest.importorskip('autiobooks.autiobooks')
format_duration_estimate = autiobooks_gui.format_duration_estimate


class TestFormatDurationEstimate:
    def test_large_book_reports_hours_and_minutes(self):
        # 100,000 words / (150 wpm * 1.0) = 666.67 min = 11h 7m
        assert format_duration_estimate(100000, 1.0) == '~11h 7m'

    def test_medium_book_reports_minutes(self):
        # 5,000 words / 150 wpm = 33.3 min
        assert format_duration_estimate(5000, 1.0) == '~33 min'

    def test_tiny_word_count_reports_under_a_minute(self):
        assert format_duration_estimate(50, 1.0) == '~<1 min'
        assert format_duration_estimate(0, 1.0) == '~<1 min'

    def test_speed_scales_duration_inversely(self):
        # Doubling speed roughly halves the estimated minutes.
        assert format_duration_estimate(5000, 2.0) == '~17 min'

    def test_non_positive_speed_falls_back_to_1x(self):
        assert format_duration_estimate(5000, 0.0) == format_duration_estimate(5000, 1.0)
        assert format_duration_estimate(5000, -1.0) == format_duration_estimate(5000, 1.0)

    def test_exact_hour_boundary_has_no_leftover_minutes(self):
        # 150 wpm * 60 min = 9000 words -> exactly 60 minutes -> 1h 0m
        assert format_duration_estimate(9000, 1.0) == '~1h 0m'


class TestSetBoolVar:
    """§3.5 — BooleanVar.set() runs its argument through Tk's getboolean()
    internally, so a hand-edited config value reaches Tk raw. The seven
    config-restore calls used to pass it straight through, and start_gui()
    has no try/except around it: a bad value killed the process with a
    traceback BEFORE any window existed, and the only recovery was hand-
    editing the JSON back. These use a live Tk root because the coercion
    that has to be survived happens inside Tk itself — a stub would prove
    nothing about real getboolean() behaviour.
    """

    def _var(self, tk_root, default):
        import tkinter as tk
        return tk.BooleanVar(master=tk_root, value=default)

    @pytest.mark.parametrize('bad', ['enabled', 'maybe', 'dark'])
    def test_non_boolean_string_keeps_default(self, tk_root, bad):
        # TclError: expected boolean value but got "enabled"
        var = self._var(tk_root, True)
        autiobooks_gui._set_bool_var(var, bad)
        assert var.get() is True

    @pytest.mark.parametrize('bad', [None, [], {}, 2.5])
    def test_non_string_type_keeps_default(self, tk_root, bad):
        # TypeError: getboolean() argument must be str, not None
        var = self._var(tk_root, True)
        autiobooks_gui._set_bool_var(var, bad)
        assert var.get() is True

    def test_bad_value_does_not_flip_a_false_default(self, tk_root):
        var = self._var(tk_root, False)
        autiobooks_gui._set_bool_var(var, 'enabled')
        assert var.get() is False

    @pytest.mark.parametrize('good,expected', [
        (True, True), (False, False),
        ('true', True), ('yes', True), ('no', False),
        (1, True), (0, False),
    ])
    def test_valid_tcl_booleans_still_apply(self, tk_root, good, expected):
        # The guard must not swallow legitimate values — a saved config of
        # `false` has to keep turning the setting off.
        var = self._var(tk_root, not expected)
        autiobooks_gui._set_bool_var(var, good)
        assert var.get() is expected
