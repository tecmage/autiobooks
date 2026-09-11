import os
import warnings

import pytest

from autiobooks import config


@pytest.fixture
def tmp_config(tmp_path, monkeypatch):
    cfg_file = tmp_path / 'config.json'
    monkeypatch.setattr(config, 'CONFIG_DIR', tmp_path)
    monkeypatch.setattr(config, 'CONFIG_FILE', cfg_file)
    return cfg_file


class TestLoadConfig:
    def test_missing_file_returns_empty(self, tmp_config):
        assert config.load_config() == {}

    def test_valid_roundtrip(self, tmp_config):
        config.save_config({'voice': 'af_heart', 'speed': '1.2'})
        assert config.load_config() == {'voice': 'af_heart', 'speed': '1.2'}

    def test_corrupt_json_returns_empty(self, tmp_config):
        tmp_config.write_text('{not json', encoding='utf-8')
        assert config.load_config() == {}

    def test_invalid_utf8_returns_empty(self, tmp_config):
        # UnicodeDecodeError is a ValueError, not a JSONDecodeError — a
        # binary-corrupted config must degrade to defaults, not crash the GUI.
        tmp_config.write_bytes(b'\xff\xfe\x00garbage\x9c')
        assert config.load_config() == {}

    def test_non_dict_json_returns_empty(self, tmp_config):
        tmp_config.write_text('[1, 2, 3]', encoding='utf-8')
        assert config.load_config() == {}


class TestSaveConfig:
    def test_no_tmp_file_left_behind(self, tmp_config):
        config.save_config({'a': 1})
        assert list(tmp_config.parent.glob('*.tmp')) == []

    def test_overwrites_previous(self, tmp_config):
        config.save_config({'a': 1})
        config.save_config({'b': 2})
        assert config.load_config() == {'b': 2}

    def test_unicode_content_roundtrip(self, tmp_config):
        cfg = {'phoneme_overrides': [{'word': 'Sean', 'ipa': 'ʃˈɔn'}]}
        config.save_config(cfg)
        assert config.load_config() == cfg

    def test_temp_file_name_is_unique_per_call(self, tmp_config, monkeypatch):
        # §3.7: a fixed temp filename lets two concurrent instances
        # interleave writes into the same file. Spy on tempfile.mkstemp to
        # confirm each save mints a distinct name (not a constant
        # 'config.json.tmp').
        seen = []
        real_mkstemp = config.tempfile.mkstemp

        def spy_mkstemp(*args, **kwargs):
            fd, name = real_mkstemp(*args, **kwargs)
            seen.append(name)
            return fd, name

        monkeypatch.setattr(config.tempfile, 'mkstemp', spy_mkstemp)
        config.save_config({'a': 1})
        config.save_config({'b': 2})
        assert len(seen) == 2
        assert seen[0] != seen[1]

    def test_tmp_file_removed_on_replace_failure(self, tmp_config, monkeypatch):
        def fail_replace(*args, **kwargs):
            raise OSError("simulated concurrent-instance failure")

        monkeypatch.setattr(config.os, 'replace', fail_replace)
        config.save_config({'a': 1})
        assert list(tmp_config.parent.glob('*.tmp')) == []

    def test_non_serializable_value_leaves_no_tmp_file(self, tmp_config):
        # F1: json.dumps must run BEFORE mkstemp creates anything, so a
        # TypeError from a non-serializable value can't reach the
        # file-creation step and orphan a uniquely-named temp file.
        config.save_config({'c': object()})
        assert list(tmp_config.parent.glob('*.tmp')) == []
        assert not tmp_config.exists()

    def test_non_oserror_after_file_creation_still_cleans_up(
            self, tmp_config, monkeypatch):
        # F1: cleanup must not be OSError-only — a non-OSError raised
        # after mkstemp already created the temp file (e.g. during the
        # write) must still unlink it rather than leaking it.
        def fail_fdopen(fd, *args, **kwargs):
            # Close the raw fd first (as fdopen normally would take
            # ownership of it) so the temp file isn't left open-handled
            # on Windows when the cleanup path tries to unlink it.
            os.close(fd)
            raise ValueError("simulated non-OSError failure during write")

        monkeypatch.setattr(config.os, 'fdopen', fail_fdopen)
        config.save_config({'a': 1})
        assert list(tmp_config.parent.glob('*.tmp')) == []


class TestSanitizeDictList:
    def test_non_list_yields_empty(self):
        assert config.sanitize_dict_list('nope') == []
        assert config.sanitize_dict_list(None) == []

    def test_non_dict_entries_dropped(self):
        value = ['not a dict', 123, {'find': 'a', 'replace': 'b'}]
        assert config.sanitize_dict_list(value) == [
            {'find': 'a', 'replace': 'b'}]

    def test_default_str_keys_does_not_check_field_types(self):
        # Default str_keys=() preserves the old shape-only behavior so
        # existing callers (e.g. render_key parity tests) are unaffected.
        value = [{'find': 123, 'replace': 'x'}]
        assert config.sanitize_dict_list(value) == value

    def test_non_str_field_dropped_with_str_keys(self):
        value = [{'find': 123, 'replace': 'one two three'}]
        assert config.sanitize_dict_list(
            value, str_keys=('find', 'replace')) == []

    def test_valid_rows_survive_with_str_keys(self):
        value = [{'find': 'lead', 'replace': 'led'}]
        assert config.sanitize_dict_list(
            value, str_keys=('find', 'replace')) == value

    def test_word_substitutions_mixed_valid_and_invalid(self):
        value = [
            {'find': 'lead', 'replace': 'led'},
            {'find': 123, 'replace': 'one two three'},
        ]
        result = config.sanitize_dict_list(
            value, str_keys=('find', 'replace'))
        assert result == [{'find': 'lead', 'replace': 'led'}]

    def test_phoneme_overrides_non_str_word_dropped(self):
        value = [
            {'word': 'five', 'ipa': 'faɪv'},
            {'word': 5, 'ipa': 'faɪv'},
        ]
        result = config.sanitize_dict_list(value, str_keys=('word', 'ipa'))
        assert result == [{'word': 'five', 'ipa': 'faɪv'}]

    def test_missing_key_defaults_to_empty_string_and_survives(self):
        # entry.get(k, '') means a missing key is treated as '' (a str),
        # not dropped outright — matches the existing "get with default"
        # idiom used by the consumers themselves.
        value = [{'find': 'lead'}]
        assert config.sanitize_dict_list(
            value, str_keys=('find', 'replace')) == value

    def test_warns_when_row_dropped_with_str_keys(self):
        value = [
            {'find': 'lead', 'replace': 'led'},
            {'find': 123, 'replace': 'one two three'},
        ]
        with pytest.warns(RuntimeWarning):
            config.sanitize_dict_list(value, str_keys=('find', 'replace'))

    def test_no_warning_for_all_valid_rows(self):
        value = [{'find': 'lead', 'replace': 'led'}]
        with warnings.catch_warnings():
            warnings.simplefilter('error')
            assert config.sanitize_dict_list(
                value, str_keys=('find', 'replace')) == value

    def test_no_warning_for_default_str_keys(self):
        # Default str_keys=() preserves the old shape-only behavior and
        # must stay warning-free even when non-dict rows are dropped.
        value = ['not a dict', {'find': 123, 'replace': 'x'}]
        with warnings.catch_warnings():
            warnings.simplefilter('error')
            assert config.sanitize_dict_list(value) == [
                {'find': 123, 'replace': 'x'}]
