"""Resume-cache identity tests for engine.render_key / chapter_wav_name.

The WAV cache filename must change when ANY render-affecting setting
changes (voice, speed, chapter_gap, heteronyms, contractions,
auto_acronyms, substitutions, phoneme_overrides) — a bare content hash
let a cancelled run's WAVs be spliced into a re-run made with different
settings. Equally important is the inverse: the GUI, batch, and CLI paths
must build byte-identical keys for the same settings so their resume
caches interoperate (CLAUDE.md's chapter-1 hash parity invariant).
"""

import pytest

engine = pytest.importorskip('autiobooks.engine')
torch = pytest.importorskip('torch')

from autiobooks import voices_lang  # noqa: E402
from autiobooks.config import sanitize_dict_list  # noqa: E402
from autiobooks.voices_lang import deemojify_voice, emojify_voice  # noqa: E402


BASE = dict(voice='af_heart', speed=1.0, chapter_gap=2.0,
            heteronyms=True, contractions=False, auto_acronyms=False,
            substitutions=[], phoneme_overrides=[])


def _key(**overrides):
    return engine.render_key(**{**BASE, **overrides})


def _name(text='Chapter text.', key=None, stem='book', wav_dir='/tmp/x'):
    if key is None:
        key = _key()
    return engine.chapter_wav_name(stem, text, wav_dir, key)


class TestRenderKeyChangesName:
    """(a) Each render-affecting setting must change the WAV name."""

    @pytest.mark.parametrize('change', [
        dict(voice='bm_george'),
        dict(speed=1.3),
        dict(chapter_gap=0.0),
        dict(heteronyms=False),
        dict(contractions=True),
        dict(auto_acronyms=True),
        dict(substitutions=[{'find': 'lead', 'replace': 'led',
                             'case_sensitive': False, 'whole_word': True}]),
        dict(phoneme_overrides=[{'word': 'sean', 'ipa': 'ʃɔn',
                                 'enabled': True}]),
    ], ids=lambda c: next(iter(c)))
    def test_setting_change_changes_name(self, change):
        assert _name(key=_key()) != _name(key=_key(**change))

    def test_identical_settings_identical_name(self):
        # (b) Two independently built keys from equal settings must agree.
        assert _name(key=_key()) == _name(key=_key())

    def test_text_still_disambiguates(self):
        key = _key()
        assert _name('Chapter one.', key) != _name('Chapter two.', key)

    def test_text_and_key_do_not_smear(self):
        # The key/text join must be unambiguous — moving characters across
        # the boundary must not collide.
        a = engine.chapter_wav_name('b', 'xtext', '/tmp/x', 'key')
        b = engine.chapter_wav_name('b', 'text', '/tmp/x', 'keyx')
        assert a != b


class TestInterfaceParity:
    """(c) GUI-, batch-, and CLI-style key construction must agree."""

    def test_gui_and_cli_inputs_build_identical_keys(self):
        subs = [{'find': 'Dr.', 'replace': 'Doctor',
                 'case_sensitive': True, 'whole_word': False}]
        overrides = [{'word': 'niamh', 'ipa': 'niv', 'enabled': True}]
        # GUI: voice from the emojified dropdown, speed from a string
        # Entry, gap parsed via float(), snapshot copies of the lists.
        gui_key = engine.render_key(
            deemojify_voice(emojify_voice('af_heart')),
            '1.2', float('1.5'),
            True, False, False,
            [dict(s) for s in subs], [dict(o) for o in overrides])
        # CLI: plain voice name, argparse floats, sanitized config lists.
        cli_key = engine.render_key(
            'af_heart', 1.2, 1.5,
            True, False, False,
            sanitize_dict_list(subs), sanitize_dict_list(overrides))
        assert gui_key == cli_key
        assert (engine.chapter_wav_name('b', 't', '/tmp/x', gui_key)
                == engine.chapter_wav_name('b', 't', '/tmp/x', cli_key))

    def test_custom_voice_display_name_folds_to_plain_name(self):
        # A custom voice is keyed by its de-emojified NAME, never the
        # resolved tensor — '✨ 🇬🇧 bm_steve' and 'bm_steve' must agree.
        assert (_key(voice=deemojify_voice('✨ 🇬🇧 bm_steve'))
                == _key(voice='bm_steve'))

    def test_none_and_empty_lists_agree(self):
        # Batch passes None for an absent override list; the CLI passes [].
        assert (_key(substitutions=None, phoneme_overrides=None)
                == _key(substitutions=[], phoneme_overrides=[]))

    def test_dict_key_order_is_irrelevant(self):
        a = _key(substitutions=[{'find': 'a', 'replace': 'b'}])
        b = _key(substitutions=[{'replace': 'b', 'find': 'a'}])
        assert a == b

    def test_substitution_order_matters(self):
        # Substitutions apply in list order, so order is render-affecting.
        s1 = [{'find': 'a', 'replace': 'b'}, {'find': 'c', 'replace': 'd'}]
        assert _key(substitutions=s1) != _key(substitutions=s1[::-1])


class TestCustomVoiceTensorIdentityInKey:
    """render_key must fold a custom voice's tensor identity (mtime_ns,
    size) into the key so a regenerated `.pt` (e.g. a kvoicewalk re-run
    between a cancel and a resume) misses the resume cache instead of
    splicing the DISCARDED voice's audio into the new run — the on-disk
    twin of the §2.4 in-process tensor cache bug. render_key must derive
    this itself from the voice NAME via the custom voices dir, never take
    a tensor/stat from its caller (CLAUDE.md: GUI/batch/CLI all build the
    key from the de-emojified name for cross-interface resume-cache
    parity), and a built-in voice's key must stay byte-identical to
    before this was added so existing resume caches for built-in voices
    are not invalidated.
    """

    @pytest.fixture
    def voices_dir(self, tmp_path, monkeypatch):
        monkeypatch.setattr(voices_lang, '_voices_dir_override', None)
        voices_lang.set_voices_dir(tmp_path)
        yield tmp_path
        voices_lang.set_voices_dir(None)

    @staticmethod
    def _write(path, fill=0.0):
        torch.save(torch.full((511, 1, 256), fill, dtype=torch.float32), path)

    def test_builtin_voice_key_unchanged_by_this_feature(self, voices_dir):
        # No file named 'af_heart.pt' exists in the (empty) custom voices
        # dir, so get_custom_voice_stat returns None and render_key must
        # emit the exact same 8-element list as before this fix — proven
        # against a hand-reconstructed pre-fix render_key below.
        import json

        def pre_fix_render_key(voice, speed, chapter_gap, heteronyms,
                               contractions, auto_acronyms, substitutions,
                               phoneme_overrides):
            def _enabled(entries):
                return [e for e in (entries or []) if e.get('enabled', True)]
            return json.dumps(
                [str(voice), float(speed), float(chapter_gap),
                 bool(heteronyms), bool(contractions), bool(auto_acronyms),
                 _enabled(substitutions), _enabled(phoneme_overrides)],
                sort_keys=True, ensure_ascii=True, separators=(',', ':'))

        args = ('af_heart', 1.2, 1.5, True, False, False,
                [{'find': 'a', 'replace': 'b'}], [{'word': 'x', 'ipa': 'y'}])
        assert engine.render_key(*args) == pre_fix_render_key(*args)

    def test_custom_voice_key_differs_from_name_only_key(self, voices_dir):
        self._write(voices_dir / 'bm_steve.pt')
        key = engine.render_key('bm_steve', 1.0, 2.0, True, False, False,
                                [], [])
        # What the key would be WITHOUT the tensor-identity component
        # (i.e. what a built-in voice with the same name would produce).
        name_only_key = '["bm_steve",1.0,2.0,true,false,false,[],[]]'
        assert key != name_only_key
        assert key.startswith(name_only_key[:-1])  # same prefix, extra elem

    def test_regenerated_tensor_changes_key(self, voices_dir):
        path = voices_dir / 'bm_steve.pt'
        self._write(path, fill=0.0)
        key1 = engine.render_key('bm_steve', 1.0, 2.0, True, False, False,
                                 [], [])
        import time
        time.sleep(0.01)
        self._write(path, fill=1.0)  # kvoicewalk-style in-place overwrite
        key2 = engine.render_key('bm_steve', 1.0, 2.0, True, False, False,
                                 [], [])
        assert key1 != key2

    def test_unchanged_tensor_keeps_same_key(self, voices_dir):
        path = voices_dir / 'bm_steve.pt'
        self._write(path)
        key1 = engine.render_key('bm_steve', 1.0, 2.0, True, False, False,
                                 [], [])
        key2 = engine.render_key('bm_steve', 1.0, 2.0, True, False, False,
                                 [], [])
        assert key1 == key2

    def test_missing_custom_voice_degrades_gracefully(self, voices_dir):
        # No file at all for this name — must not raise; falls back to a
        # name-only key exactly like a built-in voice would.
        key = engine.render_key('bm_ghost', 1.0, 2.0, True, False, False,
                                [], [])
        assert key == '["bm_ghost",1.0,2.0,true,false,false,[],[]]'


class TestDisabledEntriesFilteredFromKey:
    """render_key drops disabled substitution/override entries before
    serializing: apply_substitutions and apply_phoneme_overrides both skip
    them, so keying on them would miss the cache over an edit that cannot
    change a single audio sample."""

    def test_adding_disabled_substitution_does_not_change_key(self):
        disabled = [{'find': 'lead', 'replace': 'led', 'enabled': False}]
        assert _key(substitutions=[]) == _key(substitutions=disabled)

    def test_adding_disabled_override_does_not_change_key(self):
        disabled = [{'word': 'sean', 'ipa': 'ʃɔn', 'enabled': False}]
        assert (_key(phoneme_overrides=[])
                == _key(phoneme_overrides=disabled))

    def test_editing_disabled_substitution_does_not_change_key(self):
        a = [{'find': 'lead', 'replace': 'led', 'enabled': False}]
        b = [{'find': 'wind', 'replace': 'wynd', 'enabled': False}]
        assert _key(substitutions=a) == _key(substitutions=b)

    def test_editing_disabled_override_does_not_change_key(self):
        a = [{'word': 'sean', 'ipa': 'ʃɔn', 'enabled': False}]
        b = [{'word': 'niamh', 'ipa': 'niv', 'enabled': False}]
        assert _key(phoneme_overrides=a) == _key(phoneme_overrides=b)

    def test_enabled_substitution_does_change_key(self):
        enabled = [{'find': 'lead', 'replace': 'led', 'enabled': True}]
        assert _key(substitutions=[]) != _key(substitutions=enabled)

    def test_enabled_override_does_change_key(self):
        enabled = [{'word': 'sean', 'ipa': 'ʃɔn', 'enabled': True}]
        assert (_key(phoneme_overrides=[])
                != _key(phoneme_overrides=enabled))

    def test_missing_enabled_field_defaults_to_enabled(self):
        # Config entries written before the 'enabled' flag existed omit
        # it entirely; apply_substitutions/apply_phoneme_overrides treat
        # a missing flag as enabled via .get('enabled', True), so the key
        # must too — a no-flag entry must not be filtered out like a
        # disabled one.
        no_flag = [{'find': 'lead', 'replace': 'led'}]
        assert _key(substitutions=no_flag) != _key(substitutions=[])


class _StubFuture:
    def result(self):
        return None

    def cancel(self):
        pass


class _StubExecutor:
    def submit(self, fn, *args, **kwargs):
        return _StubFuture()


class TestConvertLoopUsesSameKey:
    """The engine loop must hit a cache prebuilt with render_key(), and
    duplicate-text chapters must still share one WAV and one encode."""

    def test_resume_hits_external_key_and_duplicates_share(
            self, tmp_path, monkeypatch):
        def _no_tts(*args, **kwargs):
            raise AssertionError(
                'cache miss: engine built a different render key than '
                'the caller')
        monkeypatch.setattr(engine, 'convert_text_to_wav_file', _no_tts)

        texts = ['hello there.', 'other chapter.', 'hello there.']
        key = engine.render_key(**BASE)
        for t in set(texts):
            wav = engine.chapter_wav_name('book', t, tmp_path, key)
            open(wav, 'wb').close()

        result = engine.convert_chapters_to_wav(
            texts, BASE['voice'], BASE['speed'], tmp_path, 'book',
            _StubExecutor(),
            chapter_gap=BASE['chapter_gap'],
            substitutions=BASE['substitutions'],
            heteronyms=BASE['heteronyms'],
            contractions=BASE['contractions'],
            phoneme_overrides=BASE['phoneme_overrides'],
            auto_acronyms=BASE['auto_acronyms'],
            resume=True)

        assert result['render_key'] == key
        assert not result['cancelled']
        # (d) duplicate text under identical settings shares one name and
        # one scheduled encode.
        assert len(result['wav_files']) == 3
        assert result['wav_files'][0] == result['wav_files'][2]
        assert result['wav_files'][0] != result['wav_files'][1]
        assert len(result['encode_futures']) == 2

    def test_resume_misses_under_different_settings(
            self, tmp_path, monkeypatch):
        calls = []

        def _fake_tts(text, voice, speed, filename, **kwargs):
            calls.append(filename)
            open(filename, 'wb').close()
            return 1.0
        monkeypatch.setattr(engine, 'convert_text_to_wav_file', _fake_tts)

        text = 'hello there.'
        stale = engine.chapter_wav_name(
            'book', text, tmp_path, engine.render_key(**BASE))
        open(stale, 'wb').close()

        result = engine.convert_chapters_to_wav(
            [text], 'bm_george', BASE['speed'], tmp_path, 'book',
            _StubExecutor(),
            chapter_gap=BASE['chapter_gap'],
            substitutions=BASE['substitutions'],
            heteronyms=BASE['heteronyms'],
            contractions=BASE['contractions'],
            phoneme_overrides=BASE['phoneme_overrides'],
            auto_acronyms=BASE['auto_acronyms'],
            resume=True)

        assert calls, 'stale WAV from a different voice was reused'
        assert result['wav_files'] == calls
        assert result['wav_files'][0] != stale


class TestAssembleOutputIndexAlignment:
    """§2.2 regression: assemble_output must align chapter titles/ordinals
    with `result['converted_indices']` (an ORIGINAL-index scan), not with a
    `wav_name in wav_files` membership test — membership can't tell two
    same-text chapters apart, since chapter_wav_name hashes text only.

    Confirmed failure scenario: texts [X, Y, X] / titles
    [Alpha, Beta, Gamma], chapter 0 (the first X) fails TRANSIENTLY (not a
    deterministic text-driven failure, which would kill both X occurrences
    and leave alignment correct by luck). A membership scan resurrects
    "Alpha" for chapter 2's ("Gamma") audio and mislabels every later
    marker; index alignment must keep Beta/Gamma in the right order and
    drop Alpha entirely.
    """

    def test_duplicate_earlier_occurrence_fails_titles_stay_aligned(
            self, tmp_path, monkeypatch):
        texts = ['X text.', 'Y text.', 'X text.']
        titles = ['Alpha', 'Beta', 'Gamma']

        state = {'first_x_done': False}

        def _fake_tts(text, voice, speed, filename, **kwargs):
            if text == 'X text.' and not state['first_x_done']:
                state['first_x_done'] = True
                raise RuntimeError('transient failure (e.g. CUDA OOM)')
            open(filename, 'wb').close()
            return 1.0
        monkeypatch.setattr(engine, 'convert_text_to_wav_file', _fake_tts)

        errors = []
        result = engine.convert_chapters_to_wav(
            texts, BASE['voice'], BASE['speed'], tmp_path, 'book',
            _StubExecutor(),
            chapter_gap=BASE['chapter_gap'],
            substitutions=BASE['substitutions'],
            heteronyms=BASE['heteronyms'],
            contractions=BASE['contractions'],
            phoneme_overrides=BASE['phoneme_overrides'],
            auto_acronyms=BASE['auto_acronyms'],
            resume=True,
            on_chapter_error=lambda i, e: errors.append(i))

        # Chapter 1 (1-based) — the first "X text." — is the one that
        # failed; the later duplicate at chapter 3 must NOT short-circuit
        # off a never-registered encode_futures entry and must re-synthesize.
        assert errors == [1]
        assert result['wav_files'] == [
            engine.chapter_wav_name('book', 'Y text.', tmp_path,
                                    result['render_key']),
            engine.chapter_wav_name('book', 'X text.', tmp_path,
                                    result['render_key']),
        ]
        # 0-based original indices of the two SURVIVING chapters (Y at 1,
        # the second X at 2) — chapter 0 dropped out entirely.
        assert result['converted_indices'] == [1, 2]

        captured = {}

        def _fake_create_m4b(chapter_files, output_path, cover_image, title,
                             creator, chapter_num, chapter_titles=None,
                             **kwargs):
            captured['titles'] = chapter_titles
            captured['chapter_numbers'] = kwargs.get('chapter_numbers')
        monkeypatch.setattr(engine, 'create_m4b', _fake_create_m4b)

        engine.assemble_output(
            result, texts, titles, 'book', tmp_path, 'm4b',
            str(tmp_path / 'out.m4b'), None, 'Book Title', 'Author',
            starting_chapter=1)

        # "Alpha" (chapter 0's title) must NOT be resurrected; Beta/Gamma
        # must stay attached to their own audio, in order.
        assert captured['titles'] == ['Beta', 'Gamma']
        # §2.6: markers keep their TRUE original ordinal (2, 3) rather than
        # renumbering contiguously from starting_chapter (which would give
        # 1, 2 and mislabel chapter 3's audio as "Chapter 2").
        assert captured['chapter_numbers'] == [2, 3]
