import sys
import os
from pathlib import Path
from PyInstaller.utils.hooks import collect_all, collect_submodules

block_cipher = None

project_root = Path(SPECPATH).parent

hidden_imports = [
    'autiobooks',
    'autiobooks.autiobooks',
    'autiobooks.config',
    'autiobooks.engine',
    'autiobooks.epub_parser',
    'autiobooks.pdf_parser',
    'autiobooks.runtime',
    'autiobooks.text_processing',
    'autiobooks.voices_lang',
    'pypdf',
    'kokoro',
    'kokoro.pipeline',
    'kokoro.models',
    'transformers',
    'soundfile',
    'pygame',
    'pygame.mixer',
    'PIL',
    'PIL.Image',
    'lxml',
    'ebooklib',
    'ebooklib.epub',
    'bs4',
    'bs4.builder',
    'phonemizer',
    'numpy',
    'addict',
    'regex',
    'torch.cuda',
    'spacy',
    'spacy.util',
    'spacy.lang.en',
    'importlib.metadata',
    'pkg_resources',
]

hooks_dir = project_root / 'autiobooks' / 'hooks'
hookspath = [str(hooks_dir)] if hooks_dir.exists() else []

datas = []

excludes = [
    'torch.distributed',
    'torch.testing',
]

torch_datas, torch_binaries, torch_hiddenimports = collect_all('torch')
phonemizer_datas, phonemizer_binaries, phonemizer_hiddenimports = collect_all('phonemizer')
kokoro_datas, kokoro_binaries, kokoro_hiddenimports = collect_all('kokoro')
language_tags_datas, language_tags_binaries, language_tags_hiddenimports = collect_all('language_tags')
csvw_datas, csvw_binaries, csvw_hiddenimports = collect_all('csvw')
segments_datas, segments_binaries, segments_hiddenimports = collect_all('segments')
espeakng_loader_datas, espeakng_loader_binaries, espeakng_loader_hiddenimports = collect_all('espeakng_loader')
spacy_datas, spacy_binaries, spacy_hiddenimports = collect_all('spacy')
spacy_model_datas, spacy_model_binaries, spacy_model_hiddenimports = collect_all('en_core_web_sm')
# misaki must be bundled because kokoro.pipeline does `from misaki import en, espeak`
# at import time. build-cuda.bat overwrites _internal/misaki/en.py with our patched
# copy (HAS_SPACY patch + silenced TODO:NUM debug) after PyInstaller runs.
misaki_datas, misaki_binaries, misaki_hiddenimports = collect_all('misaki')

vc_redist = [
    (r'C:\Windows\System32\msvcp140.dll', '.'),
    (r'C:\Windows\System32\vcruntime140.dll', '.'),
    (r'C:\Windows\System32\vcruntime140_1.dll', '.'),
]

a = Analysis(
    [str(project_root / 'autiobooks' / '__main__.py')],
    pathex=[str(project_root), str(Path(sys.prefix) / 'Lib' / 'site-packages' / 'torch' / 'lib')],
    binaries=torch_binaries + vc_redist + language_tags_binaries + csvw_binaries + segments_binaries + kokoro_binaries + phonemizer_binaries + espeakng_loader_binaries + spacy_binaries + spacy_model_binaries + misaki_binaries,
    datas=datas + torch_datas + kokoro_datas + language_tags_datas + csvw_datas + segments_datas + phonemizer_datas + espeakng_loader_datas + spacy_datas + spacy_model_datas + misaki_datas,
    hiddenimports=hidden_imports + torch_hiddenimports + kokoro_hiddenimports + language_tags_hiddenimports + csvw_hiddenimports + segments_hiddenimports + phonemizer_hiddenimports + espeakng_loader_hiddenimports + spacy_hiddenimports + spacy_model_hiddenimports + misaki_hiddenimports,
    hookspath=hookspath,
    hooksconfig={},
    runtime_hooks=[str(project_root / 'autiobooks' / 'hooks' / 'pyi_rth_torch.py')],
    excludes=excludes,
    win_no_prefer_redirects=False,
    win_private_assemblies=False,
    cipher=block_cipher,
    noarchive=False,
)

pyz = PYZ(a.pure, a.zipped_data, cipher=block_cipher)

exe = EXE(
    pyz,
    a.scripts,
    [],
    exclude_binaries=True,
    name='autiobooks-cuda',
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=False,
    console=True,
    disable_windowed_traceback=False,
    argv_emulation=False,
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,
)

coll = COLLECT(
    exe,
    a.binaries,
    a.zipfiles,
    a.datas,
    strip=False,
    upx=False,
    upx_exclude=[],
    name='autiobooks-cuda',
)
