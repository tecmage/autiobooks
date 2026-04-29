import hashlib
import re
import subprocess
import sys
import threading
import time
import warnings
import json
from concurrent.futures import ThreadPoolExecutor
import numpy as np
import soundfile
import torch
import os
from pathlib import Path
from kokoro import KPipeline
from tempfile import NamedTemporaryFile, TemporaryDirectory
from .text_processing import normalize_text
from .voices_lang import get_language_from_voice


def _patch_misaki_preprocess():
    """Replace misaki.en.G2P.preprocess with a version that keeps whitespace
    runs as their own source tokens.

    Upstream misaki splits inter-markdown text with `str.split()`, which
    discards every whitespace run. spaCy's tokenizer keeps `\\n` (and other
    whitespace) as separate tokens, so the source-token list ends up shorter
    than the mutable-token list and `Alignment.from_strings` drifts further
    with every paragraph break. In a multi-paragraph chapter, by the time a
    `[word](/IPA/)` markdown wrapping appears, the feature gets attached to a
    punctuation or newline mutable_token instead of the actual word — so the
    rating-5 phoneme override is silently dropped and the word falls back to
    misaki's gold lookup. Symptom: contextual `bowed → bˈWd` worked on short
    isolated sentences but lost the override mid-chapter, which surfaced as
    "boud" on `bowed` and `bˈWd` audio bleeding onto neighbouring punctuation.

    Kokoro's KPipeline imports `misaki` at module-load time and never exposes
    a hook for a custom preprocess, so we patch the class method in place.
    Idempotent — re-imports won't double-patch.
    """
    from misaki import en  # system install, used by Kokoro
    if getattr(en.G2P.preprocess, '_autiobooks_ws_patch', False):
        return

    @staticmethod
    def preprocess(text):
        result = ''
        tokens = []
        features = {}
        last_end = 0
        text = text.lstrip()
        def _split_keep_ws(s):
            return [t for t in re.split(r'(\s+)', s) if t]
        for m in en.LINK_REGEX.finditer(text):
            result += text[last_end:m.start()]
            tokens.extend(_split_keep_ws(text[last_end:m.start()]))
            f = m.group(2)
            if en.is_digit(f[1 if f[:1] in ('-', '+') else 0:]):
                f = int(f)
            elif f in ('0.5', '+0.5'):
                f = 0.5
            elif f == '-0.5':
                f = -0.5
            elif len(f) > 1 and f[0] == '/' and f[-1] == '/':
                f = f[0] + f[1:].rstrip('/')
            elif len(f) > 1 and f[0] == '#' and f[-1] == '#':
                f = f[0] + f[1:].rstrip('#')
            else:
                f = None
            if f is not None:
                features[len(tokens)] = f
            result += m.group(1)
            tokens.append(m.group(1))
            last_end = m.end()
        if last_end < len(text):
            result += text[last_end:]
            tokens.extend(_split_keep_ws(text[last_end:]))
        return result, tokens, features

    preprocess.__func__._autiobooks_ws_patch = True
    en.G2P.preprocess = preprocess


_patch_misaki_preprocess()

# Suppress torch warnings from Kokoro's model internals
warnings.filterwarnings('ignore', message='.*dropout option adds dropout.*')
warnings.filterwarnings('ignore', category=FutureWarning, module='torch.nn.utils.weight_norm')


SAMPLE_RATE = 24000


# PyInstaller --windowed Windows builds flash a black console window on every
# subprocess spawn unless CREATE_NO_WINDOW is set. Spread this dict into every
# subprocess.run/Popen call in this module and in runtime.py.
if sys.platform == 'win32':
    _SUBPROCESS_FLAGS = {'creationflags': subprocess.CREATE_NO_WINDOW}
else:
    _SUBPROCESS_FLAGS = {}


def chapter_wav_name(stem, text, wav_dir):
    """Return the canonical resume-safe WAV path for a chapter's text.

    The filename embeds an 8-char MD5 prefix of the chapter text so that
    reshuffling or shrinking the selected chapter set between runs can't
    cause a sequential-position resume to feed one chapter's audio into
    another chapter's slot. Two chapters with identical text deliberately
    share a wav path (the audio is identical, so re-using it is correct).
    """
    h = hashlib.md5(text.encode('utf-8', errors='replace')).hexdigest()[:8]
    return str(Path(wav_dir) / f'{stem}_chapter_{h}.wav')


def safe_stem(stem, wav_dir):
    """Return a stem short enough that `{wav_dir}/{stem}_chapter_999_enc.m4a`
    fits within Windows MAX_PATH (260). On non-Windows, returns stem unchanged.

    Long book filenames + deep user home dirs can push generated chapter paths
    past 260 chars, and Windows CreateFile rejects anything longer. Truncating
    deterministically (same stem → same truncation) keeps resume working.
    """
    if sys.platform != 'win32':
        return stem
    reserved = len(str(wav_dir)) + len('\\_chapter_999_enc.m4a') + 1
    max_stem = 240 - reserved
    if max_stem < 16:
        max_stem = 16
    if len(stem) <= max_stem:
        return stem
    short_hash = hashlib.md5(stem.encode('utf-8')).hexdigest()[:8]
    return stem[:max_stem - 9] + '_' + short_hash


def _drain_stderr(proc, stderr_buf):
    """Read proc.stderr to completion into stderr_buf.

    Returned by threading.Thread's target; exceptions are caught and
    recorded so FFmpeg error reporting never goes silent if the drain
    itself fails.
    """
    try:
        stderr_buf.append(proc.stderr.read())
    except Exception as e:
        stderr_buf.append(f'<stderr drain failed: {e}>')


def _escape_ffmeta(value):
    """Escape a value for the FFMETADATA1 file format.

    Per ffmpeg docs, values must backslash-escape \\, =, ;, #, and newline.
    Without this, a title containing any of these corrupts the metadata
    stream and ffmpeg either parses the wrong key or silently drops fields.
    """
    if value is None:
        return ''
    s = str(value)
    s = s.replace('\\', '\\\\')
    s = s.replace('\n', '\\\n')
    s = s.replace('=', '\\=')
    s = s.replace(';', '\\;')
    s = s.replace('#', '\\#')
    return s


def _safe_probe_duration(file_name):
    """Probe a media file's duration, returning 0.0 on any failure.

    Used by create_m4b where a missing/corrupt chapter shouldn't abort the
    whole batch — the caller can still assemble the other chapters and the
    metadata-stream chapter offsets just compress around the failed one.
    """
    try:
        return probe_duration(file_name)
    except (subprocess.CalledProcessError, subprocess.TimeoutExpired,
            ValueError, FileNotFoundError) as e:
        print(f'probe_duration failed for {file_name}: {e}',
              file=sys.stderr)
        return 0.0


_pipeline_cache = {}
_pipeline_lock = threading.Lock()
_current_device = 'cpu'


def set_gpu_acceleration(enabled):
    global _current_device
    new_device = 'cpu'
    if enabled:
        if torch.cuda.is_available():
            print('CUDA GPU available', file=sys.stderr)
            new_device = 'cuda'
        elif hasattr(torch.backends, 'mps') and torch.backends.mps.is_available():
            new_device = 'mps'
            print('MPS GPU available', file=sys.stderr)
        else:
            print('GPU not available. Defaulting to CPU', file=sys.stderr)
    with _pipeline_lock:
        torch.set_default_device(new_device)
        if new_device != _current_device:
            _pipeline_cache.clear()
        _current_device = new_device


def get_gpu_acceleration_available():
    if torch.cuda.is_available():
        return True
    if hasattr(torch.backends, 'mps') and torch.backends.mps.is_available():
        return True
    from .runtime import check_nvidia_gpu, _cuda_installed
    if check_nvidia_gpu() and _cuda_installed():
        return True
    return False


def create_pipeline(lang_code):
    """Create a KPipeline instance, forcing UTF-8 for its internal file reads.

    KPipeline opens internal config files without specifying encoding, which
    breaks on Windows where the preferred encoding is cp1252. We briefly
    monkey-patch builtins.open to inject encoding='utf-8' when not otherwise
    specified. This patch is serialized via _pipeline_lock and only held
    during the KPipeline() constructor, so the only threads that can observe
    it are ones doing file I/O during that brief window — and the wrapper
    only *adds* utf-8 when encoding is unspecified, which is a safer default
    anyway. On systems where utf-8 is already the preferred encoding we skip
    the patch entirely so there is no global side effect at all.
    """
    import locale
    if locale.getpreferredencoding(False).lower().replace('-', '') == 'utf8':
        return KPipeline(lang_code=lang_code, device=_current_device)

    import builtins
    original_open = builtins.open

    def utf8_open(file, mode='r', *args, **kwargs):
        if 'b' not in mode and 'encoding' not in kwargs:
            kwargs['encoding'] = 'utf-8'
        return original_open(file, mode, *args, **kwargs)

    try:
        builtins.open = utf8_open
        return KPipeline(lang_code=lang_code, device=_current_device)
    finally:
        builtins.open = original_open


def get_pipeline(lang_code):
    """Get or create a cached KPipeline for the given language code."""
    with _pipeline_lock:
        if lang_code not in _pipeline_cache:
            _pipeline_cache[lang_code] = create_pipeline(lang_code)
        return _pipeline_cache[lang_code]


def gen_audio_segments(text, voice, speed, split_pattern=r'\n+',
                       on_segment=None):
    # a for american or b for british etc.
    pipeline = get_pipeline(voice[0])
    audio_segments = []
    speed = float(speed)
    with torch.inference_mode():
        for gs, ps, audio in pipeline(text, voice=voice, speed=speed,
                                      split_pattern=split_pattern):
            audio_segments.append(audio)
            if on_segment:
                on_segment(len(audio_segments))
    return audio_segments


def create_m4b(chapter_files, output_path, cover_image, title, creator,
               chapter_num, chapter_titles=None, progress_callback=None,
               known_durations=None, preencoded=False, bitrate='64k', vbr=False):
    with TemporaryDirectory() as tempdir:
        # Create concat file listing chapter files
        concat_file = os.path.join(tempdir, 'concat.txt')
        with open(concat_file, 'w', encoding='utf-8') as f:
            for chapter_file in chapter_files:
                safe_path = chapter_file.replace("'", "'\\''")
                f.write(f"file '{safe_path}'\n")

        # Resolve chapter durations for timestamp metadata.
        # When pre-encoded M4A files are provided the WAV-based durations are
        # not usable — each M4A has an AAC encoder delay prepended that would
        # accumulate across chapters. Always probe the M4A files in that case.
        # For raw WAV files, use any caller-supplied durations and only probe
        # the remainder (e.g. chapters recovered via resume).
        if preencoded:
            with ThreadPoolExecutor(max_workers=min(len(chapter_files), 8)) as pool:
                durations = list(pool.map(_safe_probe_duration, chapter_files))
        else:
            known = known_durations or {}
            files_to_probe = [f for f in chapter_files if f not in known]
            if files_to_probe:
                with ThreadPoolExecutor(
                        max_workers=min(len(files_to_probe), 8)) as pool:
                    probed = dict(zip(files_to_probe,
                                      pool.map(_safe_probe_duration,
                                               files_to_probe)))
            else:
                probed = {}
            durations = [
                known[f] if f in known else probed[f]
                for f in chapter_files
            ]

        chapters_file = create_index_file(
            title, creator, durations, chapter_num, chapter_titles,
            output_dir=tempdir)

        # FFmpeg arguments for cover image if present
        cover_image_args = []
        cover_image_path = None
        try:
            if cover_image:
                cover_image_file = NamedTemporaryFile("wb", delete=False)
                # Record the path before writing so cleanup runs even if the
                # write or close raises — NamedTemporaryFile has already
                # created the file on disk at this point.
                cover_image_path = cover_image_file.name
                try:
                    cover_image_file.write(cover_image)
                finally:
                    cover_image_file.close()
                cover_image_args = [
                    "-i", cover_image_path,
                    '-disposition:v', 'attached_pic'
                ]

            if preencoded:
                audio_codec_args = ['-c:a', 'copy']
            elif vbr:
                audio_codec_args = ['-c:a', 'aac', '-q:a', '2']
            else:
                audio_codec_args = ['-c:a', 'aac', '-b:a', bitrate]
            total_duration_us = sum(durations) * 1_000_000
            proc = subprocess.Popen([
                'ffmpeg', '-y',
                '-safe', '0',
                '-f', 'concat',
                '-i', concat_file,
                '-i', chapters_file,
                *cover_image_args,
                *audio_codec_args,
                '-c:v', 'copy',
                '-map_metadata', '1',
                '-movflags', '+disable_chpl',
                '-progress', 'pipe:1',
                '-nostats',
                output_path
            ], stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True,
               **_SUBPROCESS_FLAGS)

            stderr_buf = []
            stderr_thread = threading.Thread(
                target=_drain_stderr, args=(proc, stderr_buf))
            stderr_thread.start()

            for line in proc.stdout:
                if progress_callback and line.startswith('out_time_ms='):
                    try:
                        us = int(line.split('=', 1)[1])
                        if total_duration_us > 0:
                            pct = min(100, int(us / total_duration_us * 100))
                            progress_callback(pct)
                    except ValueError:
                        pass

            proc.wait()
            stderr_thread.join()
            if proc.returncode != 0:
                stderr_text = (stderr_buf[0] if stderr_buf else '')[-2000:]
                raise RuntimeError(f"FFmpeg failed:\n{stderr_text}")
        finally:
            if cover_image_path and os.path.exists(cover_image_path):
                for attempt in range(3):
                    try:
                        os.unlink(cover_image_path)
                        break
                    except OSError:
                        if attempt < 2:
                            time.sleep(0.5)


_INTERMEDIATE_EXTS = {
    'm4b': '.m4a',  # intermediate for final mux
    'mp3': '.mp3',
    'flac': '.flac',
    'opus': '.opus',
    'wav': '.wav',
}


def convert_chapters_to_wav(chapter_texts, voice, speed, wav_dir, stem,
                            encode_executor, *,
                            out_format='m4b', bitrate='64k', vbr=False,
                            chapter_gap=0.0, substitutions=None,
                            heteronyms=True, contractions=True,
                            phoneme_overrides=None, auto_acronyms=False,
                            resume=True, cancel_check=None,
                            on_chapter_start=None, on_segment=None,
                            on_chapter_done=None, on_chapter_error=None):
    """Run TTS for each chapter text and queue background encoding.

    Shared by the CLI and GUI conversion paths. Generates
    `{stem}_chapter_{hash8}.wav` in `wav_dir` (content-hashed filename so
    resume stays correct across selection changes) and submits each to
    `encode_executor` to be encoded to `{stem}_chapter_{i}_enc{ext}` in the
    target format.

    Callbacks let the caller drive progress reporting without the helper
    needing to know anything about Tkinter or stderr:
      on_chapter_start(idx, total, text, is_resume)
      on_segment(idx, seg_count, est_segs)
      on_chapter_done(idx, duration_or_none)
      on_chapter_error(idx, exception)

    `cancel_check` is polled before each chapter; if it returns truthy the
    loop stops, already-submitted futures are cancelled, and
    `cancelled` is True in the returned dict.

    The caller owns `encode_executor` and must shut it down. Chapter files
    that resumed-from-disk are submitted to the executor immediately so the
    returned dict always maps every wav to a future.

    Returns a dict with:
      wav_files      — list[str] in generation order (resumed + newly done)
      encode_futures — dict[wav_path] -> (Future, encoded_path)
      cancelled      — bool
    """
    wav_dir = Path(wav_dir)
    total = len(chapter_texts)
    enc_ext = _INTERMEDIATE_EXTS.get(out_format, '.m4a')
    wav_files = []
    encode_futures = {}
    cancelled = False

    def _cancel_pending():
        for fut, _ in encode_futures.values():
            fut.cancel()

    for i, text in enumerate(chapter_texts, start=1):
        if cancel_check is not None and cancel_check():
            cancelled = True
            _cancel_pending()
            return {'wav_files': wav_files,
                    'encode_futures': encode_futures,
                    'cancelled': True}

        wav_filename = chapter_wav_name(stem, text, wav_dir)
        enc_filename = str(wav_dir / f'{stem}_chapter_{i}_enc{enc_ext}')

        if resume and Path(wav_filename).exists():
            if on_chapter_start is not None:
                on_chapter_start(i, total, text, True)
            wav_files.append(wav_filename)
            encode_futures[wav_filename] = (
                encode_executor.submit(
                    encode_chapter, wav_filename, enc_filename,
                    out_format, bitrate, vbr),
                enc_filename)
            if on_chapter_done is not None:
                on_chapter_done(i, None)
            continue

        if on_chapter_start is not None:
            on_chapter_start(i, total, text, False)

        est_segs = max(len(text.split('\n\n\n')), 1)

        def _seg_cb(seg_count, _idx=i, _est=est_segs):
            if on_segment is not None:
                on_segment(_idx, seg_count, _est)

        try:
            duration = convert_text_to_wav_file(
                text, voice, speed, wav_filename,
                on_segment=_seg_cb,
                trailing_silence=chapter_gap,
                substitutions=substitutions,
                heteronyms=heteronyms,
                contractions=contractions,
                phoneme_overrides=phoneme_overrides,
                auto_acronyms=auto_acronyms)
        except Exception as e:
            if on_chapter_error is not None:
                on_chapter_error(i, e)
            else:
                print(f"Chapter {i} failed: {e}", file=sys.stderr)
            continue

        if duration is not None:
            wav_files.append(wav_filename)
            encode_futures[wav_filename] = (
                encode_executor.submit(
                    encode_chapter, wav_filename, enc_filename,
                    out_format, bitrate, vbr),
                enc_filename)

        if on_chapter_done is not None:
            on_chapter_done(i, duration)

    if cancel_check is not None and cancel_check():
        cancelled = True
        _cancel_pending()

    return {'wav_files': wav_files,
            'encode_futures': encode_futures,
            'cancelled': cancelled}


# Map the bitrate spinbox values to libmp3lame VBR quality levels when
# MP3 + VBR is active. Lower -q:a is better quality. These roughly track
# the CBR labels as average output bitrate so "64k" stays small and "192k"
# stays large in both modes.
_MP3_VBR_QUALITY = {
    '64k': '7',
    '128k': '4',
    '192k': '2',
}


def encode_chapter(wav_path, output_path, output_format='m4b',
                    bitrate='64k', vbr=False):
    """Encode a single WAV chapter to the target format.

    For m4b, produces an M4A intermediate. For other formats, encodes directly.
    Returns output_path.
    """
    if output_format == 'm4b':
        return encode_chapter_to_m4a(wav_path, output_path, bitrate, vbr)

    if output_format == 'mp3' and vbr:
        quality = _MP3_VBR_QUALITY.get(bitrate, '4')
        codec_args = ['-c:a', 'libmp3lame', '-q:a', quality]
    else:
        format_args = {
            'mp3': ['-c:a', 'libmp3lame', '-b:a', bitrate],
            'flac': ['-c:a', 'flac'],
            'opus': ['-c:a', 'libopus', '-b:a', bitrate],
            'wav': ['-c:a', 'pcm_s16le'],
        }
        codec_args = format_args.get(output_format, ['-c:a', 'copy'])
    result = subprocess.run([
        'ffmpeg', '-y',
        '-i', wav_path,
        *codec_args,
        output_path
    ], capture_output=True, **_SUBPROCESS_FLAGS)
    if result.returncode != 0:
        stderr_text = result.stderr.decode('utf-8', errors='replace')[-2000:]
        raise RuntimeError(f"Chapter encoding failed:\n{stderr_text}")
    return output_path


def concat_audio_files(chapter_files, output_path, cover_image=None,
                       title='', creator='', chapter_num=1,
                       chapter_titles=None, progress_callback=None):
    """Concatenate encoded chapter files into a single output file (non-m4b)."""
    with TemporaryDirectory() as tempdir:
        concat_file = os.path.join(tempdir, 'concat.txt')
        with open(concat_file, 'w', encoding='utf-8') as f:
            for chapter_file in chapter_files:
                safe_path = chapter_file.replace("'", "'\\''")
                f.write(f"file '{safe_path}'\n")

        total_duration_us = 0
        for cf in chapter_files:
            try:
                total_duration_us += int(probe_duration(cf) * 1_000_000)
            except Exception:
                pass

        proc = subprocess.Popen([
            'ffmpeg', '-y',
            '-safe', '0',
            '-f', 'concat',
            '-i', concat_file,
            '-c', 'copy',
            '-progress', 'pipe:1',
            '-nostats',
            output_path
        ], stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True,
           **_SUBPROCESS_FLAGS)

        stderr_buf = []
        stderr_thread = threading.Thread(
            target=_drain_stderr, args=(proc, stderr_buf))
        stderr_thread.start()

        for line in proc.stdout:
            if progress_callback and line.startswith('out_time_ms='):
                try:
                    us = int(line.split('=', 1)[1])
                    if total_duration_us > 0:
                        pct = min(100, int(us / total_duration_us * 100))
                        progress_callback(pct)
                except ValueError:
                    pass

        proc.wait()
        stderr_thread.join()
        if proc.returncode != 0:
            stderr_text = (stderr_buf[0] if stderr_buf else '')[-2000:]
            raise RuntimeError(f"FFmpeg concat failed:\n{stderr_text}")


def encode_chapter_to_m4a(wav_path, m4a_path, bitrate='64k', vbr=False):
    """Encode a single WAV chapter to AAC/M4A.

    Intended to run in a background thread during TTS generation so that the
    final assembly step can do a fast stream-copy instead of re-encoding.
    """
    quality_args = ['-q:a', '2'] if vbr else ['-b:a', bitrate]
    result = subprocess.run([
        'ffmpeg', '-y',
        '-i', wav_path,
        '-c:a', 'aac',
        *quality_args,
        m4a_path
    ], capture_output=True, **_SUBPROCESS_FLAGS)
    if result.returncode != 0:
        stderr_text = result.stderr.decode('utf-8', errors='replace')[-2000:]
        raise RuntimeError(f"Chapter encoding failed:\n{stderr_text}")
    return m4a_path


def _probe_chapters(file_path):
    """Return list of chapter dicts from an m4b file via ffprobe."""
    result = subprocess.run([
        'ffprobe', '-v', 'quiet',
        '-print_format', 'json',
        '-show_chapters',
        file_path
    ], capture_output=True, text=True, check=True, timeout=30,
       **_SUBPROCESS_FLAGS)
    return json.loads(result.stdout).get('chapters', [])


def _probe_format_tags(file_path):
    """Return the format-level metadata tags from an m4b file."""
    result = subprocess.run([
        'ffprobe', '-v', 'quiet',
        '-print_format', 'json',
        '-show_format',
        file_path
    ], capture_output=True, text=True, check=True, timeout=30,
       **_SUBPROCESS_FLAGS)
    return json.loads(result.stdout).get('format', {}).get('tags', {})


def append_m4b(base_path, append_path, output_path, progress_callback=None):
    """Append append_path onto base_path, writing the result to output_path.

    Cover art and global metadata are taken from base_path. Chapter markers
    from both files are merged with timestamps adjusted accordingly.
    """
    with TemporaryDirectory() as tempdir:
        # Concat list
        concat_file = os.path.join(tempdir, 'concat.txt')
        with open(concat_file, 'w', encoding='utf-8') as f:
            for p in [base_path, append_path]:
                safe = p.replace("'", "'\\''")
                f.write(f"file '{safe}'\n")

        # Durations and chapters
        base_duration = probe_duration(base_path)
        base_duration_ms = int(base_duration * 1000)
        append_duration = probe_duration(append_path)
        base_chapters = _probe_chapters(base_path)
        append_chapters = _probe_chapters(append_path)

        # Global metadata from base file
        tags = _probe_format_tags(base_path)
        title = tags.get('title', '')
        artist = tags.get('artist', tags.get('album_artist', ''))
        album = tags.get('album', title)

        # Build merged FFMETADATA1
        chapters_file = os.path.join(tempdir, 'chapters.txt')
        with open(chapters_file, 'w', encoding='utf-8') as f:
            f.write(f";FFMETADATA1\ntitle={_escape_ffmeta(title)}"
                    f"\nartist={_escape_ffmeta(artist)}"
                    f"\nalbum={_escape_ffmeta(album)}\n\n")

            def write_chapters(chapters, offset_ms=0):
                for ch in chapters:
                    tb_num, tb_den = map(int, ch['time_base'].split('/'))
                    start_ms = int(ch['start'] * tb_num * 1000 / tb_den) + offset_ms
                    end_ms = int(ch['end'] * tb_num * 1000 / tb_den) + offset_ms
                    ch_title = ch.get('tags', {}).get('title', '')
                    f.write(f"[CHAPTER]\nTIMEBASE=1/1000\nSTART={start_ms}"
                            f"\nEND={end_ms}\ntitle={_escape_ffmeta(ch_title)}\n\n")

            write_chapters(base_chapters)
            write_chapters(append_chapters, offset_ms=base_duration_ms)

        total_duration_us = (base_duration + append_duration) * 1_000_000
        proc = subprocess.Popen([
            'ffmpeg', '-y',
            '-safe', '0',
            '-f', 'concat',
            '-i', concat_file,        # input 0: concatenated audio
            '-i', chapters_file,      # input 1: merged metadata + chapters
            '-i', base_path,          # input 2: cover art source
            '-map', '0:a',
            '-map', '2:v?',
            '-c', 'copy',
            '-disposition:v', 'attached_pic',
            '-map_metadata', '1',
            '-movflags', '+disable_chpl',
            '-progress', 'pipe:1',
            '-nostats',
            output_path
        ], stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True,
           **_SUBPROCESS_FLAGS)

        stderr_buf = []
        stderr_thread = threading.Thread(
            target=_drain_stderr, args=(proc, stderr_buf))
        stderr_thread.start()

        for line in proc.stdout:
            if progress_callback and line.startswith('out_time_ms='):
                try:
                    us = int(line.split('=', 1)[1])
                    if total_duration_us > 0:
                        pct = min(100, int(us / total_duration_us * 100))
                        progress_callback(pct)
                except ValueError:
                    pass

        proc.wait()
        stderr_thread.join()
        if proc.returncode != 0:
            stderr_text = (stderr_buf[0] if stderr_buf else '')[-2000:]
            raise RuntimeError(f"FFmpeg append failed:\n{stderr_text}")


def probe_duration(file_name):
    args = ['ffprobe', '-i', file_name, '-show_entries', 'format=duration',
            '-v', 'quiet', '-of', 'default=noprint_wrappers=1:nokey=1']
    proc = subprocess.run(args, capture_output=True, text=True, check=True,
                          timeout=30, **_SUBPROCESS_FLAGS)
    return float(proc.stdout.strip())


def create_index_file(title, creator, chapter_durations, chapter_num,
                      chapter_titles=None, output_dir=None):
    chapters_path = Path(output_dir or '.') / 'chapters.txt'
    esc_title = _escape_ffmeta(title)
    esc_creator = _escape_ffmeta(creator)
    with open(chapters_path, "w", encoding="utf-8") as f:
        f.write(f";FFMETADATA1\ntitle={esc_title}\nartist={esc_creator}"
                f"\nalbum={esc_title}\n\n")
        start = 0
        chapter_num = int(chapter_num)
        for idx, duration in enumerate(chapter_durations):
            end = start + int(duration * 1000)
            if chapter_titles and chapter_titles[idx]:
                ch_title = chapter_titles[idx]
            else:
                ch_title = f"Chapter {chapter_num + idx}"
            esc_ch_title = _escape_ffmeta(ch_title)
            f.write(f"[CHAPTER]\nTIMEBASE=1/1000\nSTART={start}\nEND={end}" +
                    f"\ntitle={esc_ch_title}\n\n")
            start = end
    return str(chapters_path)


def convert_text_to_wav_file(text, voice, speed, filename,
                             split_pattern=r'\n\n\n', on_segment=None,
                             trailing_silence=0, substitutions=None,
                             heteronyms=True, contractions=True,
                             phoneme_overrides=None, auto_acronyms=False):
    text = normalize_text(text, lang=get_language_from_voice(voice),
                          substitutions=substitutions,
                          heteronyms=heteronyms, contractions=contractions,
                          phoneme_overrides=phoneme_overrides,
                          auto_acronyms=auto_acronyms)
    audio = gen_audio_segments(text, voice, speed, split_pattern, on_segment)
    if audio:
        audio = np.concatenate(audio)
        if trailing_silence > 0:
            silence = np.zeros(int(SAMPLE_RATE * trailing_silence))
            audio = np.concatenate([audio, silence])
        # Write to a .part file and atomically replace the target so a
        # mid-write failure can never leave a truncated wav that resume
        # logic would mistake for a complete chapter.
        part_path = filename + '.part'
        try:
            soundfile.write(part_path, audio, SAMPLE_RATE, format='WAV')
            os.replace(part_path, filename)
        except Exception:
            try:
                Path(part_path).unlink(missing_ok=True)
            except OSError:
                pass
            raise
        return len(audio) / SAMPLE_RATE
    return None
