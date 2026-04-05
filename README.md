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
- **Chapter title detection** — automatically extracts chapter titles from the epub's table of contents or headings (can be toggled off)
- **Voice preview** — listen to a sample of any chapter before converting the full book
- **Resume support** — if a conversion is cancelled or fails, previously completed chapters are kept so you can resume without re-converting them
- **GPU acceleration** — CUDA support for significantly faster conversion on NVIDIA GPUs
- **Adjustable settings** — reading speed, chapter gap duration, and starting chapter number
- **Settings persistence** — voice, speed, gap, and other preferences are saved between sessions
- **Cover art** — embeds the epub's cover image into the output `.m4b` file
- **Docker support** — run in a container with X11 forwarding

## Requirements

- **Python** 3.10–3.12 (3.13 is not supported due to dependency constraints)
- **ffmpeg** — required for audio encoding and m4b creation
- **tkinter** — required for the GUI (included with most Python installations)
- **espeak-ng** (optional) — improves pronunciation of uncommon words. Without it, Kokoro handles most text well, but espeak-ng provides a fallback for words the model hasn't seen
- **NVIDIA GPU** (optional) — enables CUDA acceleration for faster conversion. Works with any CUDA-capable GPU. Without a GPU, conversion runs on CPU

## Changelog

#### 1.5.0

**New features:**
- **Batch queue system** — "Add to Batch" button captures the current epub with all its settings (selected chapters, voice, speed, gap, detect titles, starting chapter) into a queue
- **Batch Queue window** (Tools > Batch Queue...) — view, reorder, remove queued jobs, select output directory, and start batch conversion
- Sequential batch conversion with per-job progress tracking and ETA
- Per-file error handling — failures don't stop the batch, summary shown on completion

#### 1.4.0

**GUI improvements:**
- Select all / clear all buttons for chapter selection
- Starting chapter number field with validation and tooltip (useful when splitting a book across multiple files)

#### 1.3.0

**New features:**
- Drag-and-drop epub file support (optional `tkinterdnd2` dependency)
- Tools menu added with "Append M4B files..." for merging two audiobooks into one
- Tooltip system for UI hints

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

## Author
by David Nesbitt, distributed under MIT license.
