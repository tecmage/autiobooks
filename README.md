# Autiobooks: Automatically convert epubs to audiobooks
[![CI](https://github.com/tecmage/autiobooks/actions/workflows/ci.yml/badge.svg)](https://github.com/tecmage/autiobooks/actions/workflows/ci.yml)
![PyPI - Python Version](https://img.shields.io/pypi/pyversions/autiobooks)
![PyPI - Version](https://img.shields.io/pypi/v/autiobooks)

Autiobooks generates `.m4b` audiobooks from regular `.epub` e-books, using Kokoro's high-quality speech synthesis.

![Demo of Autiobooks in action](rec.gif)

[Kokoro](https://huggingface.co/hexgrad/Kokoro-82M) is an open-weight text-to-speech model with 82 million parameters. It yields natural sounding output while being able to run on consumer hardware.

Kokoro supports multiple languages, but Autiobooks currently exposes only American and British English [voices](https://huggingface.co/hexgrad/Kokoro-82M/blob/main/VOICES.md). Additional languages may be enabled in future releases.

PRs are welcome!

## Features

- **High-quality TTS** — powered by [Kokoro](https://huggingface.co/hexgrad/Kokoro-82M), an 82M parameter open-weight model
- **Multiple voices** — choose from a range of American and British English [voices](https://huggingface.co/hexgrad/Kokoro-82M/blob/main/VOICES.md) with different accents and prosody
- **Chapter selection** — select which chapters to convert, with word counts and text previews
- **Drag and drop** — drag an epub file directly onto the window to open it (install with `pip install "autiobooks[dnd]"`)
- **Chapter title detection** — automatically extracts chapter titles from the epub's table of contents or headings (can be toggled off)
- **Voice preview** — listen to a sample of any chapter before converting the full book
- **Resume support** — if a conversion is cancelled or fails, previously completed chapters are kept so you can resume without re-converting them
- **GPU acceleration** — CUDA support for significantly faster conversion on NVIDIA GPUs
- **Adjustable settings** — reading speed, chapter gap duration, bitrate (64/128/192k), VBR mode, and starting chapter number
- **Editable metadata** — correct the title and author before converting
- **Settings persistence** — voice, speed, gap, bitrate, and other preferences are saved between sessions
- **Cover art** — embeds the epub's cover image into the output `.m4b` file
- **Append M4B** — concatenate two `.m4b` files with merged chapter markers via the Tools menu
- **Docker support** — run in a container with X11 forwarding

## Requirements

- **Python** 3.10–3.12 (3.13 is not supported due to dependency constraints)
- **ffmpeg** — required for audio encoding and m4b creation
- **tkinter** — required for the GUI (included with most Python installations)
- **espeak-ng** (optional) — improves pronunciation of uncommon words. Without it, Kokoro handles most text well, but espeak-ng provides a fallback for words the model hasn't seen
- **NVIDIA GPU** (optional) — enables CUDA acceleration for faster conversion. Works with any CUDA-capable GPU. Without a GPU, conversion runs on CPU

## Changelog

#### 1.6.0

**Windows Builds:**
- Two standalone Windows executables via PyInstaller:
  - **CPU build** (`dist/autiobooks/autiobooks.exe`): CPU-only torch, GPU checkbox disabled (grayed out)
  - **CUDA build** (`dist-cuda/autiobooks-cuda/autiobooks-cuda.exe`): Full GPU acceleration, checkbox enabled by default
- Bundled ffmpeg and espeak-ng (downloaded at build time)
- Bundled spacy + en_core_web_sm for proper NLP tokenization (both builds)
- GPU checkbox shows but is disabled on CPU build with tooltip explaining CUDA build is needed
- "Don't ask again" preference for CUDA prompt (saved to config)
- Tools > Download CUDA Support... for manual download (bypasses "Don't ask again")

#### 1.5.0

**New features:**
- **Batch queue system** — "Add to Batch" button captures the current epub with all its settings (selected chapters, voice, speed, gap, detect titles, starting chapter) into a queue
- **Batch Queue window** (Tools > Batch Queue...) — view, reorder, remove queued jobs, select output directory, and start batch conversion
- Sequential batch conversion with per-job progress tracking and ETA
- Per-file error handling — failures don't stop the batch, summary shown on completion

#### 1.4.0

**New features:**
- **Configurable bitrate** — choose 64k, 128k, or 192k AAC output (default 64k); setting is saved between sessions
- **VBR mode** — new VBR checkbox uses AAC variable bitrate (`-q:a 2`, ~96–128 kbps) for better quality-to-size ratio; disables the bitrate dropdown when active
- **Editable metadata** — a dialog before conversion lets you correct the title and author extracted from the epub
- **Clear WAVs button** — new button in the chapter list toolbar deletes leftover `_chapter_*.wav` files for the current book without navigating to the filesystem
- **Chapter numbers** — chapter list now shows a sequence number (1, 2, 3…) before each title, counting only non-empty chapters

**GUI improvements:**
- Chapter list footer shows total selected chapters, word count, and estimated listening duration (updates live as checkboxes or speed change)
- Save As dialog remembers the last-used output directory separately from the epub input directory
- Append M4B dialog shows chapter count and duration for each selected file after browsing
- Append M4B dialog validates that input files exist and are `.m4b` before starting

**Bug fixes:**
- Cancelling a conversion now also cancels any queued background AAC encoding jobs, not just the TTS loop

#### 1.3.0

**New features:**
- **Append M4B** — new Tools menu with "Append M4B files..." dialog to concatenate two m4b files; chapter markers from both files are merged with correct timestamps, cover art and metadata are taken from the base file

**GUI improvements:**
- Starting Chapter # field moved next to the Detect chapter titles checkbox
- Starting Chapter # field is disabled while Detect chapter titles is checked (the two are mutually exclusive)

**Bug fixes:**
- Fixed chapter markers being silently truncated at 255 in the output m4b file (caused by the Nero `chpl` atom's 8-bit chapter count limit; now suppressed in favour of the standard MP4 chapter track)

#### 1.2.3

**Performance:**
- Each chapter is now encoded to AAC in a background thread immediately after TTS completes, overlapping encoding with TTS generation for subsequent chapters
- The final m4b assembly step is now a fast stream copy (remux only) instead of a full re-encode, making the "Creating m4b file" step near-instant

**GUI improvements:**
- Version number shown in the title bar

#### 1.2.2

**Performance:**
- m4b creation no longer runs ffprobe for freshly converted chapters — duration is captured directly from the TTS output, which is exact and avoids the subprocess overhead entirely
- Remaining ffprobe calls (resumed chapters) now run in parallel instead of sequentially

**GUI improvements:**
- Progress percentage shown during m4b encoding (Creating m4b file... 42%)

**Bug fixes:**
- Temp wav cleanup on success now tracks all chapter files, including any that were created on disk but not used (e.g. a chapter that produced no audio) — previously those could be left behind
- Added a short delay and retry loop before deleting temp wav files to handle cases where the OS still has a file handle open

#### 1.2.1

**Bug fixes:**
- All GUI progress/status updates now routed through the main thread (fixes rare Tkinter crashes during conversion)
- FFmpeg stderr no longer decoded with text=True — prevents UnicodeDecodeError from leaving WAV temp files behind after a successful conversion
- FFmpeg concat file now correctly escapes single quotes in file paths (fixes conversions failing for epubs with apostrophes in their filename)
- Preview playback polling loop now exits cleanly when the user manually stops playback (previously leaked a polling loop per stopped preview)

#### 1.2.0

**Refactoring:**
- Split `engine.py` into `epub_parser.py`, `text_processing.py`, `config.py`, and a slimmed `engine.py`

**Epub parsing:**
- Expanded HTML tag handling from 7 to 30+ block-level tags
- No duplication from nested blocks
- Handles `<br>`, `<img>` alt text, `<hr>`, footnote removal, script/style/nav stripping

**Text normalization:**
- Unicode cleanup (smart quotes, em-dash, en-dash, ligatures)
- Abbreviation expansion (30+ common book abbreviations)
- Context-aware Roman numeral conversion
- Special character/symbol replacement, URL/email removal
- Scene break marker removal (`***`, `---`, etc.)

**GUI improvements:**
- Bottom controls in fixed frame (never cut off)
- Compact two-row settings layout
- Chapter titles from epub TOC instead of filenames (with toggle to disable)
- Mouse wheel scrolling on chapter list
- Resizable progress bar with per-chapter progress and ETA
- Threaded preview (no GUI freeze)
- Cancel button for conversions
- Error dialogs instead of terminal-only errors
- Select all / clear all buttons for chapter selection

**Performance:**
- TTS pipeline cached and reused across chapters (model loads once)
- `torch.inference_mode()` for faster TTS inference
- Chapter durations calculated from sample count instead of spawning ffprobe per chapter

**Docker:**
- Added Dockerfile, docker-compose.yml, and .dockerignore
- X11 forwarding for GUI display
- NVIDIA GPU support
- Volume mounts for books and persistent settings
- Updated devcontainer to match

**Bug fixes & polish:**
- Save As dialog for output location
- Speed validation blocks conversion
- ffmpeg `-y` flag prevents interactive prompts
- Temp file cleanup (wav, chapters.txt, preview audio)
- Cover image temp file leak fixed
- M4b overwrite handling
- ffmpeg error capture with clear error messages
- Input validation for chapter number and gap fields
- Defensive metadata extraction for malformed epubs
- Warning suppression (ebooklib, torch, Kokoro)
- Replaced `exit(1)` with proper exceptions
- Added `lxml` as explicit dependency

#### 1.1.0

- Fix race condition - @Thabian

#### 1.0.9

- Fix issue with output file containing multiple audio stream [10](https://github.com/plusuncold/autiobooks/issues/10) - @tomhense
- Add an entrypoint for pipx - @tomhense

#### 1.0.7

- Uptick kokoro package

#### 1.0.6

- Fix chapter index - @tomhense

#### 1.0.5
- Fix pip installs

#### 1.0.3
- Fix bug causing errors on some linux installs
- Read epub files with chapters not marked as ITEM_DOCUMENT
- Select all chapters if none are selected

#### 1.0.2
- Window can be resized

#### 1.0.1
- Initial release


## How to install and run

Requires Python 3.10–3.12 (3.13 is not supported).

### 1. Install system dependencies

**Linux:**
```bash
sudo apt install ffmpeg python3-tk espeak-ng
```

**macOS:**
```bash
brew install ffmpeg python-tk espeak-ng
```

**Windows:**
- Install [ffmpeg](https://ffmpeg.org/download.html) and add it to your PATH
- tkinter is included with the standard Python installer
- [espeak-ng](https://github.com/espeak-ng/espeak-ng/releases) is optional but recommended

### 2. Clone and install

```bash
git clone https://github.com/plusuncold/autiobooks.git
cd autiobooks
pip install .
```

To also enable drag-and-drop support:
```bash
pip install ".[dnd]"
```

### 3. Run

```bash
python -m autiobooks
```

The program creates `.wav` files for each chapter, then combines them into a `.m4b` file for playing using an audiobook player.

### GPU Acceleration

If you have an NVIDIA GPU with CUDA support, check the "Enable GPU acceleration" option in the app to significantly speed up conversion. No additional setup is needed beyond having CUDA-compatible drivers installed.

## Docker

You can run Autiobooks in a Docker container. Since it's a GUI application, you'll need X11 forwarding for display.

### Build and run with Docker Compose

```bash
docker compose up --build
```

Place your `.epub` files in the `./books/` directory — this is mounted as the working directory inside the container.

### X11 Display Setup

**Linux / WSL2:**
```bash
xhost +local:docker
docker compose up --build
```

**Windows (with [VcXsrv](https://sourceforge.net/projects/vcxsrv/) or similar X server):**
```bash
# Start VcXsrv with "Disable access control" checked
DISPLAY=host.docker.internal:0 docker compose up --build
```

**macOS (with [XQuartz](https://www.xquartz.org/)):**
```bash
xhost +localhost
DISPLAY=host.docker.internal:0 docker compose up --build
```

### GPU Acceleration

If you have an NVIDIA GPU and [nvidia-container-toolkit](https://docs.nvidia.com/datacenter/cloud-native/container-toolkit/latest/install-guide.html) installed, the `deploy` section in `docker-compose.yml` enables CUDA acceleration. If you don't have a GPU, comment out or remove the `deploy` section to avoid errors.

### Volumes

| Volume | Purpose |
|--------|---------|
| `./books` | Epub input and audiobook output |
| `autiobooks-config` | Persisted settings between runs |

## Windows Builds

Pre-built Windows executables are available for download from the releases page. Two variants are provided:

| Build | File | Description |
|-------|------|-------------|
| CPU | `autiobooks.exe` | CPU-only, smaller (~1.5GB total), GPU checkbox disabled |
| CUDA | `autiobooks-cuda.exe` | Full GPU acceleration (~5.5GB total), requires NVIDIA GPU |

Both builds include bundled ffmpeg and espeak-ng, so no additional installation is required.

### Building from Source

If you need to rebuild the Windows executables, you'll need:

- **Python 3.12** (64-bit)
- **Windows 10/11**
- **Git** for cloning the repository

**Build tools** (installed automatically by the scripts):
- [scoop](https://scoop.sh/) or [chocolatey](https://chocolatey.org/) for espeak-ng
- ~2-6GB free disk space depending on build type

**CPU Build:**
```cmd
cd windows
build.bat
```
Output: `windows/dist/autiobooks/autiobooks.exe`

**CUDA Build:**
```cmd
cd windows
build-cuda.bat
```
Output: `windows/dist-cuda/autiobooks-cuda/autiobooks-cuda.exe`

Both scripts will:
1. Create a Python virtual environment
2. Install all dependencies
3. Download ffmpeg and espeak-ng
4. Run PyInstaller
5. Copy executables and DLLs to the output folder

**Using the builds:**
- Run the appropriate exe for your hardware
- On the CPU build, the GPU checkbox is disabled with a tooltip explaining a CUDA build is needed
- On first run with a CUDA build, you'll be prompted to download CUDA runtime (~2.5GB) if not already present
- Use **Tools > Download CUDA Support** to manually download CUDA (bypasses the "Don't ask again" preference)

## Author
by David Nesbitt, distributed under MIT license.
