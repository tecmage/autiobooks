import subprocess
import sys
import threading
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

# Suppress torch warnings from Kokoro's model internals
warnings.filterwarnings('ignore', message='.*dropout option adds dropout.*')
warnings.filterwarnings('ignore', category=FutureWarning, module='torch.nn.utils.weight_norm')


SAMPLE_RATE = 24000


def set_gpu_acceleration(enabled):
    if enabled:
        if torch.cuda.is_available():
            print('CUDA GPU available', file=sys.stderr)
            torch.set_default_device('cuda')
        else:
            print('CUDA GPU not available. Defaulting to CPU', file=sys.stderr)


def get_gpu_acceleration_available():
    if torch.cuda.is_available():
        return True
    from .runtime import check_nvidia_gpu, _cuda_installed
    if check_nvidia_gpu() and _cuda_installed():
        return True
    return False


_pipeline_cache = {}
_pipeline_lock = threading.Lock()


def create_pipeline(lang_code):
    """Create a KPipeline instance with proper UTF-8 encoding handling"""
    import builtins
    original_open = builtins.open

    def utf8_open(file, mode='r', *args, **kwargs):
        if 'b' not in mode and 'encoding' not in kwargs:
            kwargs['encoding'] = 'utf-8'
        return original_open(file, mode, *args, **kwargs)

    try:
        builtins.open = utf8_open
        return KPipeline(lang_code=lang_code)
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
        with open(concat_file, 'w') as f:
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
                durations = list(pool.map(probe_duration, chapter_files))
        else:
            known = known_durations or {}
            files_to_probe = [f for f in chapter_files if f not in known]
            if files_to_probe:
                with ThreadPoolExecutor(
                        max_workers=min(len(files_to_probe), 8)) as pool:
                    probed = dict(zip(files_to_probe,
                                      pool.map(probe_duration, files_to_probe)))
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
                cover_image_file.write(cover_image)
                cover_image_file.close()
                cover_image_path = cover_image_file.name
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
            ], stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)

            stderr_buf = []
            stderr_thread = threading.Thread(
                target=lambda: stderr_buf.append(proc.stderr.read()))
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
                os.unlink(cover_image_path)


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
    ], capture_output=True)
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
    ], capture_output=True, text=True, check=True)
    return json.loads(result.stdout).get('chapters', [])


def _probe_format_tags(file_path):
    """Return the format-level metadata tags from an m4b file."""
    result = subprocess.run([
        'ffprobe', '-v', 'quiet',
        '-print_format', 'json',
        '-show_format',
        file_path
    ], capture_output=True, text=True, check=True)
    return json.loads(result.stdout).get('format', {}).get('tags', {})


def append_m4b(base_path, append_path, output_path, progress_callback=None):
    """Append append_path onto base_path, writing the result to output_path.

    Cover art and global metadata are taken from base_path. Chapter markers
    from both files are merged with timestamps adjusted accordingly.
    """
    with TemporaryDirectory() as tempdir:
        # Concat list
        concat_file = os.path.join(tempdir, 'concat.txt')
        with open(concat_file, 'w') as f:
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
        with open(chapters_file, 'w') as f:
            f.write(f";FFMETADATA1\ntitle={title}\nartist={artist}\nalbum={album}\n\n")

            def write_chapters(chapters, offset_ms=0):
                for ch in chapters:
                    tb_num, tb_den = map(int, ch['time_base'].split('/'))
                    start_ms = int(ch['start'] * tb_num * 1000 / tb_den) + offset_ms
                    end_ms = int(ch['end'] * tb_num * 1000 / tb_den) + offset_ms
                    ch_title = ch.get('tags', {}).get('title', '')
                    f.write(f"[CHAPTER]\nTIMEBASE=1/1000\nSTART={start_ms}"
                            f"\nEND={end_ms}\ntitle={ch_title}\n\n")

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
        ], stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)

        stderr_buf = []
        stderr_thread = threading.Thread(
            target=lambda: stderr_buf.append(proc.stderr.read()))
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
    proc = subprocess.run(args, capture_output=True, text=True, check=True)
    return float(proc.stdout.strip())


def create_index_file(title, creator, chapter_durations, chapter_num,
                      chapter_titles=None, output_dir=None):
    chapters_path = Path(output_dir or '.') / 'chapters.txt'
    with open(chapters_path, "w") as f:
        f.write(f";FFMETADATA1\ntitle={title}\nartist={creator}\nalbum={title}\n\n")
        start = 0
        chapter_num = int(chapter_num)
        for idx, duration in enumerate(chapter_durations):
            end = start + int(duration * 1000)
            if chapter_titles and chapter_titles[idx]:
                ch_title = chapter_titles[idx]
            else:
                ch_title = f"Chapter {chapter_num + idx}"
            f.write(f"[CHAPTER]\nTIMEBASE=1/1000\nSTART={start}\nEND={end}" +
                    f"\ntitle={ch_title}\n\n")
            start = end
    return str(chapters_path)


def convert_text_to_wav_file(text, voice, speed, filename,
                             split_pattern=r'\n\n\n', on_segment=None,
                             trailing_silence=0):
    if Path(filename).exists():
        Path(filename).unlink()
    text = normalize_text(text, lang=get_language_from_voice(voice))
    audio = gen_audio_segments(text, voice, speed, split_pattern, on_segment)
    if audio:
        audio = np.concatenate(audio)
        if trailing_silence > 0:
            silence = np.zeros(int(SAMPLE_RATE * trailing_silence))
            audio = np.concatenate([audio, silence])
        soundfile.write(filename, audio, SAMPLE_RATE)
        return len(audio) / SAMPLE_RATE
    return None
