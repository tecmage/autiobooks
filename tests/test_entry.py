import importlib.metadata

from autiobooks import entry


class TestHelpText:
    """§5.8: `autiobooks --help` in a site-packages install (wheel, both
    PyInstaller builds, the Docker image) has no README.md sitting next to
    entry.py — the `Path(__file__).parent.parent / 'README.md'` probe lands
    in site-packages/the frozen bundle root, not the repo root, and silently
    falls through to a 3-line stub with no flag/option docs. METADATA (which
    pyproject's `readme = "README.md"` embeds at build time) covers those
    layouts.

    The probe runs FIRST, though: the two sources cover disjoint layouts, and
    only the probe is stale-free. In a source checkout METADATA is baked at
    `pip install -e .` time and drifts behind the tree, so a metadata-first
    order served help text from an older release.
    """

    def test_readme_on_disk_wins_over_stale_metadata(self, monkeypatch,
                                                     tmp_path):
        # A source checkout's editable-install METADATA is frozen at install
        # time (observed reading 2.4.0 against a 2.6.0 tree), so the live
        # README must win wherever both exist.
        class FakeMetadata:
            def get_payload(self):
                return 'STALE PAYLOAD FROM OLD INSTALL'

        monkeypatch.setattr(
            importlib.metadata, 'metadata', lambda name: FakeMetadata())
        pkg_dir = tmp_path / 'autiobooks'
        pkg_dir.mkdir()
        (tmp_path / 'README.md').write_text(
            'LIVE CHECKOUT README', encoding='utf-8')
        monkeypatch.setattr(entry, '__file__', str(pkg_dir / 'entry.py'))
        assert entry._help_text() == 'LIVE CHECKOUT README'

    def test_metadata_payload_wins_even_if_readme_path_missing(
            self, monkeypatch, tmp_path):
        # The metadata route must not depend on README.md existing next to
        # entry.py at all — that's precisely the case it's fixing.
        class FakeMetadata:
            def get_payload(self):
                return 'FAKE PAYLOAD'

        monkeypatch.setattr(
            importlib.metadata, 'metadata', lambda name: FakeMetadata())
        monkeypatch.setattr(
            entry, '__file__',
            str(tmp_path / 'site-packages' / 'autiobooks' / 'entry.py'))
        assert entry._help_text() == 'FAKE PAYLOAD'

    def test_falls_back_to_readme_path_when_package_not_found(
            self, monkeypatch, tmp_path):
        def fake_metadata(name):
            raise importlib.metadata.PackageNotFoundError(name)

        monkeypatch.setattr(importlib.metadata, 'metadata', fake_metadata)

        pkg_dir = tmp_path / 'autiobooks'
        pkg_dir.mkdir()
        (tmp_path / 'README.md').write_text(
            'SOURCE CHECKOUT README', encoding='utf-8')
        monkeypatch.setattr(entry, '__file__', str(pkg_dir / 'entry.py'))
        assert entry._help_text() == 'SOURCE CHECKOUT README'

    def test_returns_none_when_neither_source_available(
            self, monkeypatch, tmp_path):
        def fake_metadata(name):
            raise importlib.metadata.PackageNotFoundError(name)

        monkeypatch.setattr(importlib.metadata, 'metadata', fake_metadata)
        pkg_dir = tmp_path / 'autiobooks'
        pkg_dir.mkdir()
        monkeypatch.setattr(entry, '__file__', str(pkg_dir / 'entry.py'))
        assert entry._help_text() is None

    def test_empty_payload_falls_back_to_readme(self, monkeypatch, tmp_path):
        # A metadata() call that succeeds but returns an empty/whitespace
        # payload must not be treated as real help text.
        class FakeMetadata:
            def get_payload(self):
                return '   '

        monkeypatch.setattr(
            importlib.metadata, 'metadata', lambda name: FakeMetadata())
        pkg_dir = tmp_path / 'autiobooks'
        pkg_dir.mkdir()
        (tmp_path / 'README.md').write_text(
            'FALLBACK README', encoding='utf-8')
        monkeypatch.setattr(entry, '__file__', str(pkg_dir / 'entry.py'))
        assert entry._help_text() == 'FALLBACK README'
