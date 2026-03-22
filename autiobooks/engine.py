import subprocess
import warnings
import numpy as np
import soundfile
import torch
import os
from pathlib import Path
from kokoro import KPipeline
from tempfile import NamedTemporaryFile, TemporaryDirectory
from concurrent.futures import ThreadPoolExecutor
from text_processing import normalize_text

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
    for gs, ps, audio in pipeline(text, voice=voice, speed=speed,
                                  split_pattern=split_pattern):
        audio_segments.append(audio)
        if on_segment:
            on_segment(len(audio_segments))
    return audio_segments


def convert_wav_to_m4a(wav_file_path, m4a_file_path):
    subprocess.run([
        'ffmpeg', '-y',
        '-i', wav_file_path,
        '-c:a', 'aac',
        '-b:a', '64k',
        m4a_file_path
    ])


def create_m4b(chapter_files, output_path, cover_image, title):
    with TemporaryDirectory() as tempdir:
        # Create concat file
        concat_file = os.path.join(tempdir, 'concat.txt')
        with open(concat_file, 'w') as file:
            for wav_file in chapter_files:
                m4a_file_path = os.path.join(tempdir, Path(wav_file).stem + '.m4a')
                file.write(f"file '{m4a_file_path}'\n")

        # Convert the wav files to m4a in parallel
        with ThreadPoolExecutor() as tpe:
            futures = []
            for wav_file in chapter_files:
                m4a_file_path = os.path.join(tempdir, Path(wav_file).stem + '.m4a')
                futures.append(tpe.submit(convert_wav_to_m4a, wav_file,
                                          m4a_file_path))

        # Wait for all conversions to finish
        for future in futures:
            future.result()

        # Check if all expected m4a files exist before merging
        for line in open(concat_file):
            file_path = line.strip().split("file ")[-1].strip("'")
            if not os.path.exists(file_path):
                print(f"Missing file: {file_path}")

        # FFmpeg arguments for cover image if present
        cover_image_args = []
        if cover_image:
            cover_image_file = NamedTemporaryFile("wb", delete=False)
            cover_image_file.write(cover_image)
            cover_image_file.close()
            cover_image_args = [
                "-i", cover_image_file.name,
                '-disposition:v', 'attached_pic'
            ]
        # Merge all the converted m4a files into one big file (no encoding needed)
        subprocess.run([
            'ffmpeg', '-y',
            '-safe', '0',
            '-f', 'concat',
            '-i', concat_file,
            '-i', 'chapters.txt',
            *cover_image_args,
            '-map_metadata','1',
            '-c', 'copy',
            output_path
        ], check=True)


def probe_duration(file_name):
    args = ['ffprobe', '-i', file_name, '-show_entries', 'format=duration',
            '-v', 'quiet', '-of', 'default=noprint_wrappers=1:nokey=1']
    proc = subprocess.run(args, capture_output=True, text=True, check=True)
    return float(proc.stdout.strip())


def create_index_file(title, creator, chapter_files, chapter_num,
                      chapter_titles=None):
    with open("chapters.txt", "w") as f:
        f.write(f";FFMETADATA1\ntitle={title}\nartist={creator}\nalbum={title}\n\n")
        start = 0
        chapter_num = int(chapter_num)
        for idx, c in enumerate(chapter_files):
            duration = probe_duration(c)
            end = start + (int)(duration * 1000)
            if chapter_titles and chapter_titles[idx]:
                ch_title = chapter_titles[idx]
            else:
                ch_title = f"Chapter {chapter_num + idx}"
            f.write(f"[CHAPTER]\nTIMEBASE=1/1000\nSTART={start}\nEND={end}" +
                    f"\ntitle={ch_title}\n\n")
            start = end


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
        return True
    return False
