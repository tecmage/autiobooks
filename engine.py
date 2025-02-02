
import argparse
import sys
import time
import shutil
import subprocess
import soundfile as sf
import ebooklib
import warnings
import re
from pathlib import Path
from string import Formatter
from bs4 import BeautifulSoup
from kokoro_onnx import Kokoro
from ebooklib import epub
from pydub import AudioSegment
from pick import pick


def main(kokoro, file_path, lang, voice, pick_manually, speed):
    filename = Path(file_path).name
    with warnings.catch_warnings():
        book = epub.read_epub(file_path)
    title = book.get_metadata('DC', 'title')[0][0]
    creator = book.get_metadata('DC', 'creator')[0][0]
    intro = f'{title} by {creator}'
    print(intro)
    print('Found Chapters:', [c.get_name() for c in book.get_items() if c.get_type() == ebooklib.ITEM_DOCUMENT])
    if pick_manually:
        chapters = pick_chapters(book)
    else:
        chapters = find_chapters(book)
    print('Selected chapters:', [c.get_name() for c in chapters])
    texts = extract_texts(chapters)
    has_ffmpeg = shutil.which('ffmpeg') is not None
    if not has_ffmpeg:
        print('\033[91m' + 'ffmpeg not found. Please install ffmpeg to create mp3 and m4b audiobook files.' + '\033[0m')
    total_chars = sum([len(t) for t in texts])
    print('Started at:', time.strftime('%H:%M:%S'))
    print(f'Total characters: {total_chars:,}')
    print('Total words:', len(' '.join(texts).split(' ')))

    i = 1
    chapter_mp3_files = []
    for text in texts:
        if len(text) == 0:
            continue
        chapter_filename = filename.replace('.epub', f'_chapter_{i}.wav')
        chapter_mp3_files.append(chapter_filename)
        if Path(chapter_filename).exists():
            print(f'File for chapter {i} already exists. Skipping')
            i += 1
            continue
        if len(text.strip()) < 10:
            print(f'Skipping empty chapter {i}')
            i += 1
            continue
        print(f'Reading chapter {i} ({len(text):,} characters)...')
        if i == 1:
            text = intro + '.\n\n' + text
        start_time = time.time()
        samples, sample_rate = kokoro.create(text, voice=voice, speed=speed, lang=lang)
        sf.write(f'{chapter_filename}', samples, sample_rate)
        end_time = time.time()
        delta_seconds = end_time - start_time
        chars_per_sec = len(text) / delta_seconds
        remaining_chars = sum([len(t) for t in texts[i - 1:]])
        remaining_time = remaining_chars / chars_per_sec
        print(f'Estimated time remaining: {strfdelta(remaining_time)}')
        print('Chapter written to', chapter_filename)
        print(f'Chapter {i} read in {delta_seconds:.2f} seconds ({chars_per_sec:.0f} characters per second)')
        progress = int((total_chars - remaining_chars) / total_chars * 100)
        print('Progress:', f'{progress}%')
        i += 1
    if has_ffmpeg:
        create_m4b(chapter_mp3_files, filename, title, creator)


def extract_texts(chapters):
    texts = []
    for chapter in chapters:
        xml = chapter.get_body_content()
        soup = BeautifulSoup(xml, features='lxml')
        chapter_text = ''
        html_content_tags = ['title', 'p', 'h1', 'h2', 'h3', 'h4']
        for child in soup.find_all(html_content_tags):
            inner_text = child.text.strip() if child.text else ""
            if inner_text:
                chapter_text += inner_text + '\n'
        texts.append(chapter_text)
    return texts


def is_chapter(c):
    name = c.get_name().lower()
    part = r"part\d{1,3}"
    if re.search(part, name):
        return True
    ch = r"ch\d{1,3}"
    if re.search(ch, name):
        return True
    chap = r"chap\d{1,3}"
    if re.search(chap, name):
        return True
    if 'chapter' in name:
        return True


def find_chapters(book, verbose=False):
    chapters = [c for c in book.get_items() if c.get_type() == ebooklib.ITEM_DOCUMENT and is_chapter(c)]
    if verbose:
        for item in book.get_items():
            if item.get_type() == ebooklib.ITEM_DOCUMENT:
                print(f"'{item.get_name()}'" + ', #' + str(len(item.get_body_content())))
                # print(f'{item.get_name()}'.ljust(60), str(len(item.get_body_content())).ljust(15), 'X' if item in chapters else '-')
    if len(chapters) == 0:
        print('Not easy to find the chapters, defaulting to all available documents.')
        chapters = [c for c in book.get_items() if c.get_type() == ebooklib.ITEM_DOCUMENT]
    return chapters


def pick_chapters(book):
    all_chapters_names = [c.get_name() for c in book.get_items() if c.get_type() == ebooklib.ITEM_DOCUMENT]
    title = 'Select which chapters to read in the audiobook'
    selected_chapters_names = pick(all_chapters_names, title, multiselect=True, min_selection_count=1)
    selected_chapters_names = [c[0] for c in selected_chapters_names]
    selected_chapters = []
    for chapter_name in selected_chapters_names:
        matching_chapters = [c for c in book.get_items()
                             if c.get_name() == chapter_name]
        if len(matching_chapters) != 1:
            print(f'Error: could not find chapter {chapter_name}')
            sys.exit(1)
        selected_chapters.append(matching_chapters[0])
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


def create_m4b(chapter_files, filename, title, author):
    tmp_filename = filename.replace('.epub', '.tmp.m4a')
    if not Path(tmp_filename).exists():
        combined_audio = AudioSegment.empty()
        for wav_file in chapter_files:
            audio = AudioSegment.from_wav(wav_file)
            combined_audio += audio
        print('Converting to Mp4...')
        combined_audio.export(tmp_filename, format="mp4", codec="aac", bitrate="64k")
    final_filename = filename.replace('.epub', '.m4b')
    print('Creating M4B file...')
    proc = subprocess.run([
        'ffmpeg', '-i', f'{tmp_filename}', '-c', 'copy', '-f', 'mp4',
        '-metadata', f'title={title}',
        '-metadata', f'author={author}',
        f'{final_filename}'
    ])
    Path(tmp_filename).unlink()
    if proc.returncode == 0:
        print(f'{final_filename} created. Enjoy your audiobook.')
        print('Feel free to delete the intermediary .wav chapter files, the .m4b is all you need.')

