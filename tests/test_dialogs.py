import json

import pytest

tk = pytest.importorskip('tkinter')

from autiobooks import dialogs  # noqa: E402
from autiobooks.dialogs import _coerce_str  # noqa: E402


def _find_widget(root, text):
    """Depth-first search for a descendant widget whose 'text' option
    matches (buttons/labels). Dialog widgets aren't returned by the show_*
    functions, so tests locate them by their visible label instead."""
    for child in root.winfo_children():
        try:
            if child.cget('text') == text:
                return child
        except tk.TclError:
            pass
        found = _find_widget(child, text)
        if found is not None:
            return found
    return None


class TestCoerceStr:
    """§3.8: `dict.get(key, default)` returns a stored `None`, not the
    default, when the key is present with a null value — a hand-edited
    config's `{"word": null, ...}` used to reach
    show_phoneme_overrides_dialog's `local` list as None, render as the
    literal text "None" in the tree, and crash Import JSON's dedup
    comprehension (`None.lower()`) with no try/except around it.
    `_coerce_str` is the fix: normalize word/ipa to str when `local` is
    built, so refresh/dedup/export all see well-typed rows.
    """

    def test_str_passes_through_unchanged(self):
        assert _coerce_str('taste') == 'taste'

    def test_none_becomes_empty_string(self):
        # Not str(None) == "None" — that's the exact cosmetic bug (a tree
        # row rendering the literal text "None") this must avoid.
        assert _coerce_str(None) == ''

    def test_empty_string_stays_empty(self):
        assert _coerce_str('') == ''

    def test_int_is_stringified(self):
        assert _coerce_str(5) == '5'

    def test_float_is_stringified(self):
        assert _coerce_str(3.5) == '3.5'


class TestImportJsonUsesCoerceStr:
    """F2: import_json (inside show_phoneme_overrides_dialog) coerced with
    raw `str()`, not `_coerce_str` — `str(None) == 'None'` is truthy, so a
    JSON file with `{"word": null, "ipa": "x"}` slipped past the `if not w
    or not i: continue` guard and imported a bogus override for the
    literal word "None". Drives the real dialog end-to-end (Import JSON
    button -> Save button) rather than testing the closure directly, since
    import_json isn't exposed outside show_phoneme_overrides_dialog."""

    def test_null_word_and_null_ipa_are_dropped_on_import(
            self, tk_root, tmp_path, monkeypatch):
        data = [
            {'word': None, 'ipa': 'x'},          # null word -> dropped
            {'word': 'foo', 'ipa': None},        # null ipa -> dropped
            {'word': 'bar', 'ipa': 'bˈɑɹ'},       # valid -> kept
        ]
        json_path = tmp_path / 'overrides.json'
        json_path.write_text(json.dumps(data), encoding='utf-8')

        monkeypatch.setattr(
            dialogs.filedialog, 'askopenfilename', lambda **k: str(json_path))
        monkeypatch.setattr(dialogs.messagebox, 'showinfo', lambda *a, **k: None)
        monkeypatch.setattr(dialogs.messagebox, 'showerror', lambda *a, **k: None)

        saved = {}
        before = set(tk_root.winfo_children())
        dialogs.show_phoneme_overrides_dialog(
            tk_root, [], lambda new: saved.setdefault('result', new))
        dlg = (set(tk_root.winfo_children()) - before).pop()
        try:
            _find_widget(dlg, 'Import JSON…').invoke()
            _find_widget(dlg, 'Save').invoke()
        finally:
            if dlg.winfo_exists():
                dlg.destroy()

        words = [o['word'] for o in saved['result']]
        assert words == ['bar']
        assert 'None' not in words

    def test_show_substitutions_dialog_has_no_json_import_path(self):
        # Companion check the audit asked for: does show_substitutions_dialog
        # have the same raw-str() bug in an import path? It has no JSON
        # import at all (no 'Import' button, no json.load call), so there
        # is nothing for the F2 bug class to apply to.
        import inspect
        src = inspect.getsource(dialogs.show_substitutions_dialog)
        assert 'import_json' not in src
        assert 'json.load' not in src
