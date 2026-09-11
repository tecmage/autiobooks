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
from .voices_lang import (get_language_from_voice, resolve_voice,
                          get_custom_voice_stat, get_kokoro_lang_code)


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


def render_key(voice, speed, chapter_gap, heteronyms, contractions,
               auto_acronyms, substitutions, phoneme_overrides):
    """Return a stable serialization of every setting that changes the
    rendered audio for a given chapter text.

    Folded into chapter_wav_name so a resume only reuses a WAV rendered
    with the SAME settings — a bare existence check spliced stale audio
    (previous voice/speed/gap/pronunciation config) into the output after
    a cancelled run. The GUI, batch, and CLI paths must all build the key
    through this one function, from the de-emojified voice NAME (never a
    resolved tensor), so their resume caches keep interoperating.

    Disabled substitution/override entries are dropped: apply_substitutions
    and apply_phoneme_overrides skip them, so keying on them would miss the
    whole cache over an edit that cannot change a single sample.

    A custom voice is identified by NAME here, same as always — but a
    kvoicewalk re-run can overwrite a custom `.pt` in place under the same
    name, and a name-only key can't tell the discarded tensor from the new
    one (the §2.4 stale-tensor-cache bug's twin, one layer up: this is the
    on-disk resume cache rather than the in-process tensor cache). So we
    look up the file's own (mtime_ns, size) via get_custom_voice_stat and
    fold it in — a regenerated tensor changes the key and misses the WAV
    cache instead of splicing stale audio. get_custom_voice_stat returns
    None for a built-in name (no matching file), and that component is
    then OMITTED from the list entirely rather than serialized as null —
    the key for a built-in voice must stay byte-identical to before this
    was added, so existing resume caches for built-in voices don't all go
    cold. A missing/unreadable custom `.pt` also resolves to None here;
    the real error still surfaces later from resolve_voice when the voice
    is actually loaded for synthesis.
    """
    def _enabled(entries):
        return [e for e in (entries or []) if e.get('enabled', True)]

    voice_str = str(voice)
    key_parts = [voice_str, float(speed), float(chapter_gap),
                 bool(heteronyms), bool(contractions), bool(auto_acronyms),
                 _enabled(substitutions), _enabled(phoneme_overrides)]
    custom_stat = get_custom_voice_stat(voice_str)
    if custom_stat is not None:
        key_parts.append(list(custom_stat))
    return json.dumps(key_parts, sort_keys=True, ensure_ascii=True,
                      separators=(',', ':'))


def chapter_wav_name(stem, text, wav_dir, render_key):
    """Return the canonical resume-safe WAV path for a chapter.

    The filename embeds an 8-char MD5 prefix over the render_key (see
    render_key()) plus the chapter text: reshuffling or shrinking the
    selected chapter set between runs can't feed one chapter's audio into
    another chapter's slot, and a run under different settings misses the
    cache instead of reusing audio rendered with the old ones. Two
    chapters with identical text and settings deliberately share a wav
    path (the audio is identical, so re-using it is correct).
    """
    digest = hashlib.md5(render_key.encode('utf-8', errors='replace'))
    digest.update(b'\x00')
    digest.update(text.encode('utf-8', errors='replace'))
    h = digest.hexdigest()[:8]
    # Absolutized as defense-in-depth alongside the enc_filename fix above —
    # the hash is over chapter text, not the path, so this doesn't disturb
    # resume caching.
    return str((Path(wav_dir) / f'{stem}_chapter_{h}.wav').absolute())


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


def find_chapter_wavs(stem, wav_dir):
    """Return every cached chapter WAV for a book stem, sorted, across
    ALL render_keys — including files left behind by runs under different
    settings, whose names no current-key list can reproduce.

    This is the ONLY stem-wide sweep in the codebase; every other consumer
    builds exact paths via chapter_wav_name(). It filters iterdir() on a
    literal name prefix rather than globbing, because the stem is
    user-derived and glob metacharacters in a book filename ('The Hobbit
    [Illustrated]') make a pattern that silently matches nothing — or,
    worse, matches ANOTHER book's files. The prefix ends at '_chapter_',
    so stem 'Book' can never match 'Book 2_chapter_*.wav'. `stem` must be
    the safe_stem() value the files were named with. Callers may only
    delete the result on SUCCESS or an explicit user-initiated clear —
    WAVs are kept for resume on cancel/failure. For 'wav' output format
    the '_enc.wav' intermediates also match; every caller already removes
    those unconditionally, so sweeping them is harmless. Returns [] when
    wav_dir is missing or unreadable.
    """
    prefix = f'{stem}_chapter_'
    try:
        return sorted(p for p in Path(wav_dir).iterdir()
                      if p.name.startswith(prefix) and p.suffix == '.wav')
    except OSError:
        return []


def unlink_with_retry(path):
    """Best-effort delete with exponential backoff.

    On Windows ffmpeg/pygame may still hold a handle for a fraction of a
    second after exit, so a single unlink can race that. Retries with
    0.1s → 1.6s backoff (≈3.1s total worst case) and only sleeps when a
    delete actually fails. Returns None on success or the final OSError.
    """
    delay = 0.1
    last_err = None
    for attempt in range(6):
        try:
            Path(path).unlink(missing_ok=True)
            return None
        except OSError as err:
            last_err = err
            if attempt < 5:
                time.sleep(delay)
                delay *= 2
    return last_err


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
    except (RuntimeError, subprocess.TimeoutExpired,
            ValueError, FileNotFoundError) as e:
        # probe_duration raises RuntimeError (with ffprobe's stderr) on a
        # non-zero exit or empty output.
        print(f'probe_duration failed for {file_name}: {e}',
              file=sys.stderr)
        return 0.0


_pipeline_cache = {}
_pipeline_lock = threading.Lock()
_current_device = 'cpu'

# Single global conversion slot. The GUI convert flow and the batch runner
# both flip process-global torch state (set_gpu_acceleration) and write the
# same per-book WAV/encode paths, so exactly one conversion may run at a
# time. Plain Lock (not RLock) so the slot claimed on the main thread can be
# released from the worker thread's finally.
_conversion_lock = threading.Lock()


def try_begin_conversion():
    """Atomically claim the global conversion slot. True when claimed.

    The caller that spawns the worker claims the slot; the worker MUST call
    end_conversion() in its finally, including on crash paths.
    """
    return _conversion_lock.acquire(blocking=False)


def end_conversion():
    """Release the conversion slot claimed by try_begin_conversion()."""
    try:
        _conversion_lock.release()
    except RuntimeError:
        pass


def is_conversion_active():
    return _conversion_lock.locked()


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
    # a for american or b for british etc. — routed through
    # get_kokoro_lang_code (the same _PREFIX_TO_LANGUAGE table
    # get_language_from_voice uses) instead of raw voice[0], so this and
    # normalize_text's language selection can't disagree about what
    # language a voice is, and an unrecognized prefix raises a named error
    # here instead of KPipeline's bare assertion tuple.
    pipeline = get_pipeline(get_kokoro_lang_code(voice))
    voice_arg = resolve_voice(voice)
    audio_segments = []
    speed = float(speed)
    with torch.inference_mode():
        for gs, ps, audio in pipeline(text, voice=voice_arg, speed=speed,
                                      split_pattern=split_pattern):
            audio_segments.append(audio)
            if on_segment:
                on_segment(len(audio_segments))
    return audio_segments


def _concat_entry(path):
    """One `file '...'` line for an ffmpeg concat list.

    Paths are absolutized — ffmpeg resolves relative entries against the
    concat file's own directory (a TemporaryDirectory here), not the CWD,
    so a relative input path (CLI `convert books/x.epub`) broke assembly.
    Quotes use the close-escape-reopen idiom; newlines are rejected up
    front because the concat format is line-based and a newline-bearing
    filename would inject a second directive.
    """
    p = os.path.abspath(path)
    if '\n' in p or '\r' in p:
        raise ValueError(f'newline in audio file path: {p!r}')
    safe = p.replace("'", "'\\''")
    return f"file '{safe}'\n"


def _part_path(output_path):
    """Temporary neighbour of the final output for atomic assembly.

    The real extension stays LAST ('book.part.m4b', not 'book.m4b.part')
    because ffmpeg infers the muxer from it. The final os.replace means a
    killed mux can never leave a truncated file at the user's chosen path.
    Absolutized so a relative dash-leading name can't be parsed by ffmpeg
    as an option (ffmpeg has no '--' end-of-options marker).
    """
    p = Path(output_path).absolute()
    return str(p.with_name(p.stem + '.part' + p.suffix))


def create_m4b(chapter_files, output_path, cover_image, title, creator,
               chapter_num, chapter_titles=None, progress_callback=None,
               known_durations=None, preencoded=False, bitrate='64k', vbr=False,
               chapter_numbers=None):
    with TemporaryDirectory() as tempdir:
        # Create concat file listing chapter files
        concat_file = os.path.join(tempdir, 'concat.txt')
        with open(concat_file, 'w', encoding='utf-8') as f:
            for chapter_file in chapter_files:
                f.write(_concat_entry(chapter_file))

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
            output_dir=tempdir, chapter_numbers=chapter_numbers)

        # FFmpeg arguments for cover image if present
        cover_image_args = []
        cover_image_path = None
        part_path = _part_path(output_path)
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
                part_path
            ], stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True, encoding='utf-8', errors='replace',
               **_SUBPROCESS_FLAGS)

            stderr_buf = []
            stderr_thread = threading.Thread(
                target=_drain_stderr, args=(proc, stderr_buf))
            stderr_thread.start()

            try:
                for line in proc.stdout:
                    if progress_callback and line.startswith('out_time_ms='):
                        try:
                            us = int(line.split('=', 1)[1])
                            if total_duration_us > 0:
                                pct = min(100, int(us / total_duration_us * 100))
                                progress_callback(pct)
                        except ValueError:
                            pass
            except BaseException:
                # A progress callback can raise (e.g. root.after on a
                # destroyed window) — don't orphan ffmpeg or leave the
                # non-daemon stderr drainer blocking interpreter exit.
                proc.kill()
                raise
            finally:
                proc.wait()
                stderr_thread.join()
            if proc.returncode != 0:
                stderr_text = (stderr_buf[0] if stderr_buf else '')[-2000:]
                raise RuntimeError(f"FFmpeg failed:\n{stderr_text}")
            os.replace(part_path, output_path)
        finally:
            if os.path.exists(part_path):
                unlink_with_retry(part_path)
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
    `{stem}_chapter_{hash8}.wav` in `wav_dir` (filename hashed over the
    render settings plus the chapter text so resume stays correct across
    selection AND settings changes) and submits each to
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
      wav_files         — list[str] in generation order (resumed + newly
                          done); identical-text chapters repeat their shared
                          wav path
      converted_indices — list[int], parallel to wav_files: the 0-based
                          index into chapter_texts each wav_files entry came
                          from. Kept element-for-element aligned with
                          wav_files (including the duplicate short-circuit
                          path below) so assemble_output can recover the
                          original chapter title/ordinal for every surviving
                          wav by position instead of a lossy membership scan.
      encode_futures — dict[wav_path] -> (Future, encoded_path); one entry
                       per DISTINCT wav — duplicate-text chapters reuse the
                       first occurrence's encode instead of overwriting it
      cancelled      — bool
      render_key     — the render_key() string the wav names were built
                       with; assemble_output must reuse it
    """
    wav_dir = Path(wav_dir)
    total = len(chapter_texts)
    enc_ext = _INTERMEDIATE_EXTS.get(out_format, '.m4a')
    rkey = render_key(voice, speed, chapter_gap, heteronyms, contractions,
                      auto_acronyms, substitutions, phoneme_overrides)
    wav_files = []
    converted_indices = []
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
                    'converted_indices': converted_indices,
                    'encode_futures': encode_futures,
                    'cancelled': True,
                    'render_key': rkey}

        wav_filename = chapter_wav_name(stem, text, wav_dir, rkey)
        # Absolutized like _part_path — a relative dash-leading name (CLI
        # `convert ./-draft.epub`) can't be parsed by ffmpeg as an output
        # positional (ffmpeg has no '--' end-of-options marker).
        enc_filename = str((wav_dir / f'{stem}_chapter_{i}_enc{enc_ext}').absolute())

        if wav_filename in encode_futures:
            # An identical-text chapter earlier in this run already
            # synthesized this wav and scheduled its encode (duplicate
            # chapters deliberately share a wav path). Re-synthesizing
            # would os.replace the wav under the in-flight encode reading
            # it (PermissionError on Windows), and re-submitting would
            # overwrite the dict entry — orphaning the first future so a
            # failed encode went unobserved. Reuse the scheduled encode;
            # assemble_output then lists the shared enc file once per
            # occurrence, which is the intended duplicated audio.
            if on_chapter_start is not None:
                on_chapter_start(i, total, text, True)
            wav_files.append(wav_filename)
            converted_indices.append(i - 1)
            if on_chapter_done is not None:
                on_chapter_done(i, None)
            continue

        if resume and Path(wav_filename).exists():
            if on_chapter_start is not None:
                on_chapter_start(i, total, text, True)
            wav_files.append(wav_filename)
            converted_indices.append(i - 1)
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

        # Estimate matches convert_text_to_wav_file's split_pattern (r'\n+'):
        # normalized chapter text separates paragraphs with single newlines,
        # so paragraphs ≈ segments. (The old '\n\n\n' split never matched
        # normalized text — est_segs was always 1 and per-chapter progress
        # jumped straight to ~95%.)
        est_segs = max(
            len([s for s in re.split(r'\n+', text) if s.strip()]), 1)

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
            converted_indices.append(i - 1)
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
            'converted_indices': converted_indices,
            'encode_futures': encode_futures,
            'cancelled': cancelled,
            'render_key': rkey}


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
    """Concatenate encoded chapter files into a single output file (non-m4b).

    Writes title/artist/album tags for tag-capable containers and embeds the
    cover as attached_pic for MP3/FLAC (the ogg/opus muxer doesn't support
    attached pictures; WAV carries no tags at all). Chapter markers stay the
    M4B path's job — see create_m4b.
    """
    out_ext = Path(output_path).suffix.lower()
    with TemporaryDirectory() as tempdir:
        concat_file = os.path.join(tempdir, 'concat.txt')
        with open(concat_file, 'w', encoding='utf-8') as f:
            for chapter_file in chapter_files:
                f.write(_concat_entry(chapter_file))

        total_duration_us = 0
        for cf in chapter_files:
            try:
                total_duration_us += int(probe_duration(cf) * 1_000_000)
            except Exception:
                pass

        meta_args = []
        if out_ext != '.wav':
            if title:
                meta_args += ['-metadata', f'title={title}',
                              '-metadata', f'album={title}']
            if creator:
                meta_args += ['-metadata', f'artist={creator}']
        if out_ext == '.mp3':
            # ID3v2.3 for broadest player compatibility.
            meta_args += ['-id3v2_version', '3']

        # FLAC must be re-encoded: the concat demuxer cannot stream-copy raw
        # FLAC across segment boundaries — the output silently contains ONLY
        # the first chapter (verified: two 1s chapters -c copy → 1.0s file,
        # no warning). FLAC is lossless, so re-encoding is bit-faithful.
        if out_ext == '.flac':
            audio_codec = ['-c:a', 'flac']
        else:
            audio_codec = ['-c:a', 'copy']

        cover_input = []
        cover_path = None
        part_path = _part_path(output_path)
        embed_cover = bool(cover_image) and out_ext in ('.mp3', '.flac')
        try:
            if embed_cover:
                cover_file = NamedTemporaryFile('wb', delete=False)
                cover_path = cover_file.name
                try:
                    cover_file.write(cover_image)
                finally:
                    cover_file.close()
                cover_input = ['-i', cover_path]
                stream_args = ['-map', '0:a', '-map', '1:v',
                               *audio_codec, '-c:v', 'copy',
                               '-disposition:v', 'attached_pic']
            else:
                stream_args = audio_codec

            proc = subprocess.Popen([
                'ffmpeg', '-y',
                '-safe', '0',
                '-f', 'concat',
                '-i', concat_file,
                *cover_input,
                *stream_args,
                *meta_args,
                '-progress', 'pipe:1',
                '-nostats',
                part_path
            ], stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True, encoding='utf-8', errors='replace',
               **_SUBPROCESS_FLAGS)

            stderr_buf = []
            stderr_thread = threading.Thread(
                target=_drain_stderr, args=(proc, stderr_buf))
            stderr_thread.start()

            try:
                for line in proc.stdout:
                    if progress_callback and line.startswith('out_time_ms='):
                        try:
                            us = int(line.split('=', 1)[1])
                            if total_duration_us > 0:
                                pct = min(100, int(us / total_duration_us * 100))
                                progress_callback(pct)
                        except ValueError:
                            pass
            except BaseException:
                proc.kill()
                raise
            finally:
                proc.wait()
                stderr_thread.join()
            if proc.returncode != 0:
                stderr_text = (stderr_buf[0] if stderr_buf else '')[-2000:]
                raise RuntimeError(f"FFmpeg concat failed:\n{stderr_text}")
            os.replace(part_path, output_path)
        finally:
            if os.path.exists(part_path):
                unlink_with_retry(part_path)
            if cover_path:
                unlink_with_retry(cover_path)


def assemble_output(result, chapter_texts, chapter_titles, stem, wav_dir,
                    out_format, output_path, cover_image, title, creator,
                    starting_chapter=1, bitrate='64k', vbr=False,
                    progress_callback=None):
    """Wait for background encodes and assemble the final output file.

    The single post-TTS assembly path shared by the GUI, the batch runner,
    and the CLI (each previously carried its own copy, and they had
    drifted). `result` is the dict returned by convert_chapters_to_wav;
    `chapter_texts` must be the SAME list passed to it, since
    `result['converted_indices']` indexes into it (and into the parallel
    `chapter_titles`) to recover which original chapters survived — see
    convert_chapters_to_wav's docstring. `chapter_titles` is a per-chapter
    title list parallel to chapter_texts, or None for no chapter markers.
    Returns the encoded files used.
    """
    wav_files = result['wav_files']
    converted_indices = result['converted_indices']
    encode_futures = result['encode_futures']
    encoded_files = []
    for wav_name in wav_files:
        future, enc_name = encode_futures[wav_name]
        future.result()
        encoded_files.append(enc_name)

    if out_format == 'm4b':
        # Align titles/ordinals with the chapters that actually produced
        # audio by ORIGINAL INDEX, not by a membership scan — failed/empty
        # chapters drop out of wav_files and their titles must drop out too
        # or every later marker shifts. converted_indices is returned by
        # convert_chapters_to_wav element-for-element parallel to wav_files
        # (including repeats for duplicate-text chapters), so this is
        # count- and index-exact by construction, unlike a `wav_name in
        # wav_files` membership test which can't tell two same-text
        # chapters apart.
        converted_titles = None
        if chapter_titles is not None:
            converted_titles = [chapter_titles[j] for j in converted_indices]
            if not converted_titles:
                converted_titles = None
        # Original chapter ordinals (starting_chapter-relative) for the
        # surviving chapters, parallel to encoded_files/durations — lets
        # create_index_file's generic "Chapter N" fallback keep each
        # marker's true ordinal instead of renumbering by compacted
        # position when an earlier chapter fails (§2.6).
        chapter_numbers = [int(starting_chapter) + j for j in converted_indices]
        create_m4b(encoded_files, output_path, cover_image,
                   title, creator, starting_chapter, converted_titles,
                   progress_callback=progress_callback,
                   preencoded=True, bitrate=bitrate, vbr=vbr,
                   chapter_numbers=chapter_numbers)
    else:
        concat_audio_files(encoded_files, output_path,
                           cover_image=cover_image,
                           title=title, creator=creator,
                           progress_callback=progress_callback)
    return encoded_files


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


def _check_probe(result, file_path):
    """Raise with ffprobe's stderr on failure — CalledProcessError's message
    omits it, leaving dialogs showing only 'returned non-zero exit status'."""
    if result.returncode != 0:
        stderr_text = (result.stderr or '')[-2000:]
        raise RuntimeError(
            f"ffprobe failed for {file_path}:\n{stderr_text}")


def _probe_chapters(file_path):
    """Return list of chapter dicts from an m4b file via ffprobe."""
    result = subprocess.run([
        'ffprobe', '-v', 'error',
        '-print_format', 'json',
        '-show_chapters',
        file_path
    ], capture_output=True, text=True, encoding='utf-8', errors='replace',
       timeout=30, **_SUBPROCESS_FLAGS)
    _check_probe(result, file_path)
    return json.loads(result.stdout).get('chapters', [])


def _probe_format_tags(file_path):
    """Return the format-level metadata tags from an m4b file."""
    result = subprocess.run([
        'ffprobe', '-v', 'error',
        '-print_format', 'json',
        '-show_format',
        file_path
    ], capture_output=True, text=True, encoding='utf-8', errors='replace',
       timeout=30, **_SUBPROCESS_FLAGS)
    _check_probe(result, file_path)
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
                f.write(_concat_entry(p))

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
        part_path = _part_path(output_path)
        try:
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
                part_path
            ], stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True, encoding='utf-8', errors='replace',
               **_SUBPROCESS_FLAGS)

            stderr_buf = []
            stderr_thread = threading.Thread(
                target=_drain_stderr, args=(proc, stderr_buf))
            stderr_thread.start()

            try:
                for line in proc.stdout:
                    if progress_callback and line.startswith('out_time_ms='):
                        try:
                            us = int(line.split('=', 1)[1])
                            if total_duration_us > 0:
                                pct = min(100, int(us / total_duration_us * 100))
                                progress_callback(pct)
                        except ValueError:
                            pass
            except BaseException:
                proc.kill()
                raise
            finally:
                proc.wait()
                stderr_thread.join()
            if proc.returncode != 0:
                stderr_text = (stderr_buf[0] if stderr_buf else '')[-2000:]
                raise RuntimeError(f"FFmpeg append failed:\n{stderr_text}")
            os.replace(part_path, output_path)
        finally:
            if os.path.exists(part_path):
                unlink_with_retry(part_path)


def probe_duration(file_name):
    args = ['ffprobe', '-i', file_name, '-show_entries', 'format=duration',
            '-v', 'error', '-of', 'default=noprint_wrappers=1:nokey=1']
    proc = subprocess.run(args, capture_output=True, text=True,
                          encoding='utf-8', errors='replace',
                          timeout=30, **_SUBPROCESS_FLAGS)
    _check_probe(proc, file_name)
    out = proc.stdout.strip()
    if not out:
        raise RuntimeError(
            f"ffprobe returned no duration for {file_name}")
    return float(out)


def create_index_file(title, creator, chapter_durations, chapter_num,
                      chapter_titles=None, output_dir=None,
                      chapter_numbers=None):
    """`chapter_numbers`, if given, is a list parallel to `chapter_durations`
    holding each surviving chapter's true original ordinal (starting_chapter
    + its 0-based index in the full selection). The generic "Chapter N"
    fallback below uses it instead of `chapter_num + idx` so a marker's
    number reflects its real position even when an earlier chapter failed
    and dropped out of the compacted `chapter_durations` list — falls back
    to the old contiguous-renumbering behaviour when not provided.
    """
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
            elif chapter_numbers is not None:
                ch_title = f"Chapter {chapter_numbers[idx]}"
            else:
                ch_title = f"Chapter {chapter_num + idx}"
            esc_ch_title = _escape_ffmeta(ch_title)
            f.write(f"[CHAPTER]\nTIMEBASE=1/1000\nSTART={start}\nEND={end}" +
                    f"\ntitle={esc_ch_title}\n\n")
            start = end
    return str(chapters_path)


def convert_text_to_wav_file(text, voice, speed, filename,
                             split_pattern=r'\n+', on_segment=None,
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
        except BaseException:
            # BaseException so Ctrl+C in CLI mode also cleans up the .part
            # litter instead of leaving it behind.
            try:
                Path(part_path).unlink(missing_ok=True)
            except OSError:
                pass
            raise
        return len(audio) / SAMPLE_RATE
    return None
