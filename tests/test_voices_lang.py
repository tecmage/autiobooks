import pytest

torch = pytest.importorskip('torch')

from autiobooks import voices_lang


@pytest.fixture
def voices_dir(tmp_path, monkeypatch):
    monkeypatch.setattr(voices_lang, '_voices_dir_override', None)
    voices_lang.set_voices_dir(tmp_path)
    yield tmp_path
    voices_lang.set_voices_dir(None)


def _write_voice(path, shape=(511, 1, 256)):
    tensor = torch.zeros(shape, dtype=torch.float32)
    torch.save(tensor, path)


class TestDiscovery:
    def test_empty_dir_returns_empty_list(self, voices_dir):
        assert voices_lang.discover_custom_voices() == []

    def test_missing_dir_returns_empty_list(self, tmp_path, monkeypatch):
        monkeypatch.setattr(voices_lang, '_voices_dir_override', None)
        voices_lang.set_voices_dir(tmp_path / 'does_not_exist')
        try:
            assert voices_lang.discover_custom_voices() == []
        finally:
            voices_lang.set_voices_dir(None)

    def test_lists_pt_files_sorted(self, voices_dir):
        _write_voice(voices_dir / 'bm_steve.pt')
        _write_voice(voices_dir / 'af_maya.pt')
        assert voices_lang.discover_custom_voices() == ['af_maya', 'bm_steve']

    def test_ignores_non_pt_files(self, voices_dir):
        _write_voice(voices_dir / 'bm_steve.pt')
        (voices_dir / 'notes.txt').write_text('not a voice')
        assert voices_lang.discover_custom_voices() == ['bm_steve']


class TestEmojify:
    def test_builtin_voice_no_sparkle(self):
        # af_heart is built-in
        assert voices_lang.emojify_voice('af_heart') == '🇺🇸 af_heart'

    def test_custom_voice_gets_sparkle(self, voices_dir):
        # bm_steve is not in voices_internal — treated as custom. Sparkle is
        # keyed off actual discovery in the voices dir, so write the file.
        _write_voice(voices_dir / 'bm_steve.pt')
        assert voices_lang.emojify_voice('bm_steve') == '✨ 🇬🇧 bm_steve'

    def test_deemojify_strips_sparkle(self):
        assert voices_lang.deemojify_voice('✨ 🇬🇧 bm_steve') == 'bm_steve'

    def test_deemojify_builtin_unchanged(self):
        assert voices_lang.deemojify_voice('🇺🇸 af_heart') == 'af_heart'


class TestResolveVoice:
    def test_builtin_returns_string(self):
        assert voices_lang.resolve_voice('af_heart') == 'af_heart'

    def test_custom_returns_tensor(self, voices_dir):
        _write_voice(voices_dir / 'bm_steve.pt')
        result = voices_lang.resolve_voice('bm_steve')
        assert isinstance(result, torch.Tensor)
        assert result.shape == (511, 1, 256)
        assert result.dtype == torch.float32

    def test_custom_tensor_is_cached(self, voices_dir):
        _write_voice(voices_dir / 'bm_steve.pt')
        a = voices_lang.resolve_voice('bm_steve')
        b = voices_lang.resolve_voice('bm_steve')
        assert a is b  # second call hits cache

    def test_missing_file_raises(self, voices_dir):
        with pytest.raises(FileNotFoundError):
            voices_lang.resolve_voice('nonexistent_voice')

    def test_stale_tensor_not_returned_after_in_place_overwrite(
            self, voices_dir):
        # §2.4 repro, reproduced independently by two verifiers in the
        # audit: overwrite the .pt in place (same size, same mtime-second
        # resolution risk) and confirm resolve_voice picks up the NEW
        # content instead of returning the discarded tensor forever
        # (`t2 is t1`). cache_key must include mtime/size, not just path.
        path = voices_dir / 'bm_steve.pt'
        torch.save(torch.zeros(511, 1, 256), path)
        first = voices_lang.resolve_voice('bm_steve')
        assert torch.equal(first, torch.zeros(511, 1, 256))

        import time
        time.sleep(0.01)  # ensure a distinguishable mtime_ns
        torch.save(torch.ones(511, 1, 256), path)
        second = voices_lang.resolve_voice('bm_steve')
        assert torch.equal(second, torch.ones(511, 1, 256))
        assert not torch.equal(first, second)

    def test_deleted_file_raises_not_cached_tensor(self, voices_dir):
        # §2.4: the is_file() guard used to run AFTER the cache read, so it
        # was dead code for any name already cached — a deleted file kept
        # returning the last-loaded tensor instead of raising.
        path = voices_dir / 'bm_steve.pt'
        _write_voice(path)
        voices_lang.resolve_voice('bm_steve')  # populate the cache
        path.unlink()
        with pytest.raises(FileNotFoundError):
            voices_lang.resolve_voice('bm_steve')

    def test_back_to_back_calls_same_object_when_unchanged(self, voices_dir):
        # Re-affirms the pre-existing `a is b` cache-hit contract still
        # holds once the cache key includes mtime/size — an unchanged file
        # must keep hitting the cache, not reload every call.
        _write_voice(voices_dir / 'bm_steve.pt')
        a = voices_lang.resolve_voice('bm_steve')
        b = voices_lang.resolve_voice('bm_steve')
        assert a is b

    def test_wrong_shape_rejected(self, voices_dir):
        _write_voice(voices_dir / 'bm_bad.pt', shape=(10, 10))
        with pytest.raises(ValueError, match='unexpected shape'):
            voices_lang.resolve_voice('bm_bad')

    def test_wrong_middle_dim_rejected(self, voices_dir):
        # (N, 5, 256) passes an ndim/last-dim check but is not a valid
        # Kokoro voice pack — must fail with the named-file error, not
        # crash later inside synthesis.
        _write_voice(voices_dir / 'bm_bad5.pt', shape=(10, 5, 256))
        with pytest.raises(ValueError, match='unexpected shape'):
            voices_lang.resolve_voice('bm_bad5')

    def test_non_tensor_rejected(self, voices_dir):
        torch.save({'not': 'a tensor'}, voices_dir / 'bm_bad.pt')
        with pytest.raises((ValueError, RuntimeError)):
            voices_lang.resolve_voice('bm_bad')


class TestCustomWinsCollision:
    """A custom .pt named after a built-in voice must win the collision."""

    def test_resolve_returns_tensor_not_builtin_string(self, voices_dir):
        # bm_daniel is a stock built-in name.
        _write_voice(voices_dir / 'bm_daniel.pt')
        result = voices_lang.resolve_voice('bm_daniel')
        assert isinstance(result, torch.Tensor)

    def test_merged_list_has_single_entry(self, voices_dir):
        _write_voice(voices_dir / 'bm_daniel.pt')
        voices_lang.refresh_voices()
        assert voices_lang.voices.count('bm_daniel') == 1

    def test_emojified_has_sparkle(self, voices_dir):
        _write_voice(voices_dir / 'bm_daniel.pt')
        voices_lang.refresh_voices()
        assert '✨ 🇬🇧 bm_daniel' in voices_lang.voices_emojified
        assert '🇬🇧 bm_daniel' not in voices_lang.voices_emojified

    def test_emojify_voice_direct_call_has_sparkle(self, voices_dir):
        _write_voice(voices_dir / 'bm_daniel.pt')
        assert voices_lang.emojify_voice('bm_daniel') == '✨ 🇬🇧 bm_daniel'


class TestCustomVoiceStemValidation:
    """§2.5: get_pipeline/get_language_from_voice both derive language from
    ONLY voice[0], so an unfiltered *.pt glob lets a bare stem like
    `steve.pt` (prefix 's', not a valid Kokoro lang_code) reach KPipeline
    and hard-crash, and worse, a bare stem like `zoe.pt`/`jim.pt` (prefix
    'z'/'j', which ARE valid Kokoro lang_codes) get silently read as
    Mandarin/Japanese. discover_custom_voices() must filter both classes
    out at the source so neither reaches the dropdown or CLI validation.
    """

    def test_unrecognized_prefix_excluded(self, voices_dir, capsys):
        # 's' is not a Kokoro lang_code at all — would hard-crash KPipeline.
        _write_voice(voices_dir / 'steve.pt')
        assert voices_lang.discover_custom_voices() == []
        assert 'steve' in capsys.readouterr().err

    def test_coincidentally_valid_prefix_excluded(self, voices_dir, capsys):
        # 'z' IS a valid Kokoro lang_code (Mandarin) — silently wrong
        # language with no crash and no warning was the dangerous case.
        _write_voice(voices_dir / 'zoe.pt')
        _write_voice(voices_dir / 'jim.pt')
        assert voices_lang.discover_custom_voices() == []
        err = capsys.readouterr().err
        assert 'zoe' in err
        assert 'jim' in err

    def test_full_convention_stem_accepted(self, voices_dir):
        _write_voice(voices_dir / 'bm_steve.pt')
        assert voices_lang.discover_custom_voices() == ['bm_steve']

    def test_lang_only_stem_accepted_no_regression(self, voices_dir):
        # A stem like `a_custom.pt` (lang code + underscore, no gender
        # letter) already resolves correctly today — only voice[0] is
        # ever consulted by either KPipeline or get_language_from_voice —
        # so requiring the audit's stricter `^[abefhijpz][fm]_` would
        # reject a stem that works and isn't ambiguous. Gender is optional.
        _write_voice(voices_dir / 'a_custom.pt')
        assert voices_lang.discover_custom_voices() == ['a_custom']
        assert voices_lang.get_language_from_voice('a_custom') == 'en-us'

    def test_valid_and_invalid_stems_mixed(self, voices_dir):
        _write_voice(voices_dir / 'bm_steve.pt')
        _write_voice(voices_dir / 'zoe.pt')
        assert voices_lang.discover_custom_voices() == ['bm_steve']

    def test_rejected_stem_not_in_emojified_list(self, voices_dir):
        _write_voice(voices_dir / 'zoe.pt')
        voices_lang.refresh_voices()
        assert 'zoe' not in voices_lang.voices
        assert not any('zoe' in v for v in voices_lang.voices_emojified)


class TestGetKokoroLangCode:
    def test_valid_prefix_returns_single_char(self):
        assert voices_lang.get_kokoro_lang_code('bm_daniel') == 'b'
        assert voices_lang.get_kokoro_lang_code('zf_xiaobei') == 'z'

    def test_unrecognized_prefix_raises_named_error(self):
        with pytest.raises(ValueError, match="steve"):
            voices_lang.get_kokoro_lang_code('steve')

    def test_agrees_with_get_language_from_voice(self):
        # The two must never disagree — get_language_from_voice is defined
        # in terms of get_kokoro_lang_code precisely so they can't.
        for voice in ('af_heart', 'bm_daniel', 'zf_xiaobei', 'jm_kumo'):
            code = voices_lang.get_kokoro_lang_code(voice)
            assert voices_lang._PREFIX_TO_LANGUAGE[code] == \
                voices_lang.get_language_from_voice(voice)


class TestRefreshVoices:
    def test_refresh_picks_up_new_files(self, voices_dir):
        voices_lang.refresh_voices()
        builtin_count = len(voices_lang.voices_internal)
        assert len(voices_lang.voices) == builtin_count
        _write_voice(voices_dir / 'bm_steve.pt')
        voices_lang.refresh_voices()
        assert 'bm_steve' in voices_lang.voices
        assert '✨ 🇬🇧 bm_steve' in voices_lang.voices_emojified
