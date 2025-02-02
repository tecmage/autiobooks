
import time
import shutil
import subprocess
import numpy as np
import soundfile
import ebooklib
import warnings
import re
import torch
from pathlib import Path
from string import Formatter
from bs4 import BeautifulSoup
from kokoro import KPipeline
from ebooklib import epub
from pydub import AudioSegment
from pick import pick
from tempfile import NamedTemporaryFile


sample_rate = 24000


def main(file_path, voice, speed, chapters_by_name):
    filename = Path(file_path).name
    warnings.simplefilter("ignore")
    book = epub.read_epub(file_path)
    meta_title = book.get_metadata('DC', 'title')
    title = meta_title[0][0] if meta_title else ''
    meta_creator = book.get_metadata('DC', 'creator')
    by_creator = 'by ' + meta_creator[0][0] if meta_creator else ''

    cover_maybe = [c for c in book.get_items() if c.get_type() == ebooklib.ITEM_COVER]
    cover_image = cover_maybe[0].get_content() if cover_maybe else b""
    if cover_maybe:
        print(f'Found cover image {cover_maybe[0].file_name} in {cover_maybe[0].media_type} format')

    intro = f'{title} {by_creator}'
    print(intro)
    print('Found Chapters:', [c.get_name() for c in book.get_items() if c.get_type() == ebooklib.ITEM_DOCUMENT])

    document_chapters = find_document_chapters_and_extract_texts(book)

    chapters = []
    for chapter_by_name in chapters_by_name:
        for item in document_chapters:
            if item.get_name() == chapter_by_name:
                chapters.append(item)
                break

    texts = [c.extracted_text for c in chapters]

    has_ffmpeg = shutil.which('ffmpeg') is not None
    if not has_ffmpeg:
        print('\033[91m' + 'ffmpeg not found. Please install ffmpeg to create mp3 and m4b audiobook files.' + '\033[0m')

    total_chars, processed_chars = sum(map(len, texts)), 0
    print('Started at:', time.strftime('%H:%M:%S'))
    print(f'Total characters: {total_chars:,}')
    print('Total words:', len(' '.join(texts).split()))
    chars_per_sec = 500 if torch.cuda.is_available() else 50
    print(f'Estimated time remaining (assuming {chars_per_sec} chars/sec): {strfdelta((total_chars - processed_chars) / chars_per_sec)}')

    chapter_mp3_files = []
    for i, text in enumerate(texts, start=1):
        chapter_filename = filename.replace('.epub', f'_chapter_{i}.wav')
        chapter_mp3_files.append(chapter_filename)
        if Path(chapter_filename).exists():
            print(f'File for chapter {i} already exists. Skipping')
            continue
        if len(text.strip()) < 10:
            print(f'Skipping empty chapter {i}')
            chapter_mp3_files.remove(chapter_filename)
            continue
        print(f'Reading chapter {i} ({len(text):,} characters)...')
        if i == 1:
            text = intro + '.\n\n' + text
        start_time = time.time()

        audio_segments = gen_audio_segments(text, voice, speed)
        if audio_segments:
            final_audio = np.concatenate(audio_segments)
            soundfile.write(chapter_filename, final_audio, sample_rate)
            end_time = time.time()
            delta_seconds = end_time - start_time
            chars_per_sec = len(text) / delta_seconds
            processed_chars += len(text)
            print(f'Estimated time remaining: {strfdelta((total_chars - processed_chars) / chars_per_sec)}')
            print('Chapter written to', chapter_filename)
            print(f'Chapter {i} read in {delta_seconds:.2f} seconds ({chars_per_sec:.0f} characters per second)')
            progress = processed_chars * 100 // total_chars
            print('Progress:', f'{progress}%\n')
        else:
            print(f'Warning: No audio generated for chapter {i}')
            chapter_mp3_files.remove(chapter_filename)

    if has_ffmpeg:
        create_index_file(title, by_creator, chapter_mp3_files)
        create_m4b(chapter_mp3_files, filename, cover_image)


def gen_audio_segments(text, voice, speed):
    pipeline = KPipeline(lang_code=voice[0])  # a for american or b for british etc.
    audio_segments = []
    for gs, ps, audio in pipeline(text, voice=voice, speed=speed, split_pattern=r'\n+'):
        audio_segments.append(audio)
    return audio_segments


def find_document_chapters_and_extract_texts(book):
    """Returns every chapter that is an ITEM_DOCUMENT and enriches each chapter with extracted_text."""
    document_chapters = []
    for chapter in book.get_items():
        if chapter.get_type() != ebooklib.ITEM_DOCUMENT:
            continue
        xml = chapter.get_body_content()
        soup = BeautifulSoup(xml, features='lxml')
        chapter_text = ''
        html_content_tags = ['title', 'p', 'h1', 'h2', 'h3', 'h4', 'li']
        for child in soup.find_all(html_content_tags):
            inner_text = child.text.strip() if child.text else ""
            if inner_text:
                chapter_text += inner_text + '\n'
        chapter.extracted_text = chapter_text
        document_chapters.append(chapter)
    return document_chapters


def is_chapter(c):
    name = c.get_name().lower()
    has_min_len = len(c.get_body_content()) > 100
    title_looks_like_chapter = bool(
        'chapter' in name.lower()
        or re.search(r'part\d{1,3}', name)
        or re.search(r'ch\d{1,3}', name)
        or re.search(r'chap\d{1,3}', name)
    )
    return has_min_len and title_looks_like_chapter


def find_chapters(document_chapters, verbose=False):
    chapters = [c for c in document_chapters if c.get_type() == ebooklib.ITEM_DOCUMENT and is_chapter(c)]
    if verbose:
        for item in chapters:
            if item.get_type() == ebooklib.ITEM_DOCUMENT:
                print(f"'{item.get_name()}'" + ', #' + str(len(item.get_body_content())))
    if len(chapters) == 0:
        print('Not easy to find the chapters, defaulting to all available documents.')
        chapters = [c for c in document_chapters if c.get_type() == ebooklib.ITEM_DOCUMENT]
    return chapters


def pick_chapters(chapters):
    all_chapters_names = [c.get_name() for c in chapters if c.get_type() == ebooklib.ITEM_DOCUMENT]
    title = 'Select which chapters to read in the audiobook'
    selected_chapters_names = pick(all_chapters_names, title, multiselect=True, min_selection_count=1)
    selected_chapters_names = [c[0] for c in selected_chapters_names]
    selected_chapters = [c for c in chapters if c.get_name() in selected_chapters_names]
    return selected_chapters


def strfdelta(tdelta, fmt='{D:02}d {H:02}h {M:02}m {S:02}s'):
    remainder = int(tdelta)
    f = Formatter()
    desired_fields = [field_tuple[1] for field_tuple in f.parse(fmt)]
    possible_fields = ('W', 'D', 'H', 'M', 'S')
    constants = {'W': 604800, 'D': 86400, 'H': 3600, 'M': 60, 'S': 1}
    values = {}
    for field in possible_fields:
        if field in desired_fields and field in constants:
            values[field], remainder = divmod(remainder, constants[field])
    return f.format(fmt, **values)


def create_m4b(chapter_files, filename, cover_image):
    tmp_filename = filename.replace('.epub', '.tmp.mp4')
    if not Path(tmp_filename).exists():
        combined_audio = AudioSegment.empty()
        for wav_file in chapter_files:
            audio = AudioSegment.from_wav(wav_file)
            combined_audio += audio
        print('Converting to Mp4...')
        combined_audio.export(tmp_filename, format="mp4", codec="aac", bitrate="64k")
    final_filename = filename.replace('.epub', '.m4b')
    print('Creating M4B file...')

    if cover_image:
        cover_image_file = NamedTemporaryFile("wb")
        cover_image_file.write(cover_image)
        cover_image_args = ["-i", cover_image_file.name, "-map", "0:a", "-map", "2:v"]
    else:
        cover_image_args = []

    proc = subprocess.run([
        'ffmpeg',
        '-i', f'{tmp_filename}',
        '-i', 'chapters.txt',
        *cover_image_args,
        '-map', '0',
        '-map_metadata', '1',
        '-c:a', 'copy',
        '-c:v', 'copy',
        '-disposition:v', 'attached_pic',
        '-c', 'copy',
        '-f', 'mp4',
        f'{final_filename}'
    ])
    Path(tmp_filename).unlink()
    if proc.returncode == 0:
        print(f'{final_filename} created. Enjoy your audiobook.')
        print('Feel free to delete the intermediary .wav chapter files, the .m4b is all you need.')


def probe_duration(file_name):
    args = ['ffprobe', '-i', file_name, '-show_entries', 'format=duration', '-v', 'quiet', '-of', 'default=noprint_wrappers=1:nokey=1']
    proc = subprocess.run(args, capture_output=True, text=True, check=True)
    return float(proc.stdout.strip())


def create_index_file(title, creator, chapter_mp3_files):
    with open("chapters.txt", "w") as f:
        f.write(f";FFMETADATA1\ntitle={title}\nartist={creator}\n\n")
        start = 0
        i = 0
        for c in chapter_mp3_files:
            duration = probe_duration(c)
            end = start + (int)(duration * 1000)
            f.write(f"[CHAPTER]\nTIMEBASE=1/1000\nSTART={start}\nEND={end}\ntitle=Chapter {i}\n\n")
            i += 1
            start = end
