import subprocess
import warnings
import numpy as np
import soundfile
import torch
import os
from pathlib import Path
from kokoro import KPipeline
from tempfile import NamedTemporaryFile, TemporaryDirectory
from .text_processing import normalize_text

# Suppress torch warnings from Kokoro's model internals
warnings.filterwarnings('ignore', message='.*dropout option adds dropout.*')
warnings.filterwarnings('ignore', category=FutureWarning, module='torch.nn.utils.weight_norm')


SAMPLE_RATE = 24000


def set_gpu_acceleration(enabled):
    if enabled:
        if torch.cuda.is_available():
            print('CUDA GPU available')
            torch.set_default_device('cuda')
        else:
            print('CUDA GPU not available. Defaulting to CPU')


def get_gpu_acceleration_available():
    return torch.cuda.is_available()


_pipeline_cache = {}


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
               chapter_num, chapter_titles=None):
    with TemporaryDirectory() as tempdir:
        # Create concat file listing WAV files directly
        concat_file = os.path.join(tempdir, 'concat.txt')
        with open(concat_file, 'w') as f:
            for wav_file in chapter_files:
                safe_path = wav_file.replace("'", "'\\''")
                f.write(f"file '{safe_path}'\n")

        # Probe WAV durations for chapter timestamps.
        # Encoding all chapters in one ffmpeg pass means there is only one AAC
        # encoder delay at the very start of the file. All chapter boundaries
        # are positions within a continuous stream, so WAV-based timestamps
        # match the encoded output exactly.
        wav_durations = [probe_duration(f) for f in chapter_files]
        chapters_file = create_index_file(
            title, creator, wav_durations, chapter_num, chapter_titles,
            output_dir=tempdir)

        # FFmpeg arguments for cover image if present
        cover_image_args = []
        cover_image_path = None
        if cover_image:
            cover_image_file = NamedTemporaryFile("wb", delete=False)
            cover_image_file.write(cover_image)
            cover_image_file.close()
            cover_image_path = cover_image_file.name
            cover_image_args = [
                "-i", cover_image_path,
                '-disposition:v', 'attached_pic'
            ]
        # Encode all WAV chapters to AAC in a single pass with chapter metadata
        try:
            result = subprocess.run([
                'ffmpeg', '-y',
                '-safe', '0',
                '-f', 'concat',
                '-i', concat_file,
                '-i', chapters_file,
                *cover_image_args,
                '-c:a', 'aac',
                '-b:a', '64k',
                '-c:v', 'copy',
                '-map_metadata', '1',
                output_path
            ], capture_output=True)
            if result.returncode != 0:
                stderr_text = result.stderr.decode('utf-8', errors='replace')[-2000:]
                raise RuntimeError(f"FFmpeg failed:\n{stderr_text}")
        finally:
            if cover_image_path and os.path.exists(cover_image_path):
                os.unlink(cover_image_path)


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
    text = normalize_text(text)
    audio = gen_audio_segments(text, voice, speed, split_pattern, on_segment)
    if audio:
        audio = np.concatenate(audio)
        if trailing_silence > 0:
            silence = np.zeros(int(SAMPLE_RATE * trailing_silence))
            audio = np.concatenate([audio, silence])
        soundfile.write(filename, audio, SAMPLE_RATE)
        return len(audio) / SAMPLE_RATE
    return None
