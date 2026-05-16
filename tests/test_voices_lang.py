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
        # bm_steve is not in voices_internal — treated as custom
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

    def test_wrong_shape_rejected(self, voices_dir):
        _write_voice(voices_dir / 'bm_bad.pt', shape=(10, 10))
        with pytest.raises(ValueError, match='unexpected shape'):
            voices_lang.resolve_voice('bm_bad')

    def test_non_tensor_rejected(self, voices_dir):
        torch.save({'not': 'a tensor'}, voices_dir / 'bm_bad.pt')
        with pytest.raises((ValueError, RuntimeError)):
            voices_lang.resolve_voice('bm_bad')


class TestRefreshVoices:
    def test_refresh_picks_up_new_files(self, voices_dir):
        voices_lang.refresh_voices()
        builtin_count = len(voices_lang.voices_internal)
        assert len(voices_lang.voices) == builtin_count
        _write_voice(voices_dir / 'bm_steve.pt')
        voices_lang.refresh_voices()
        assert 'bm_steve' in voices_lang.voices
        assert '✨ 🇬🇧 bm_steve' in voices_lang.voices_emojified
