"""Headless CLI for autiobooks — epub/PDF to audiobook conversion."""

import argparse
import hashlib
import sys
import time
import warnings
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

# Suppress third-party warnings (same as GUI)
warnings.filterwarnings('ignore', category=FutureWarning, module='ebooklib.epub')
warnings.filterwarnings('ignore', message='.*dropout option adds dropout.*')
warnings.filterwarnings('ignore', category=FutureWarning,
                        module='torch.nn.utils.weight_norm')

FORMAT_EXTENSIONS = {
    'm4b': '.m4b',
    'mp3': '.mp3',
    'flac': '.flac',
    'opus': '.opus',
    'wav': '.wav',
}


_verbosity = 1  # 0=quiet, 1=normal, 2=verbose


def _stderr_is_tty():
    try:
        return sys.stderr.isatty()
    except (AttributeError, ValueError):
        return False


def _eprint(*args, level=1, **kwargs):
    """Print to stderr. level: 0=always, 1=normal, 2=verbose-only."""
    if _verbosity >= level:
        print(*args, file=sys.stderr, **kwargs)


def _eprint_progress(label, pct):
    """Emit a progress line. Uses \\r overwrite on TTY, newlines otherwise."""
    if _stderr_is_tty():
        _eprint(f"\r  {label}: {pct}%", end='')
    else:
        _eprint(f"  {label}: {pct}%")


def _parse_chapter_selection(spec, total):
    """Parse a chapter selection string like '1,3-5,8' into a set of 0-based indices."""
    selected = set()
    for part in spec.split(','):
        part = part.strip()
        if '-' in part:
            start_s, end_s = part.split('-', 1)
            start = int(start_s)
            end = int(end_s)
            for n in range(start, end + 1):
                if 1 <= n <= total:
                    selected.add(n - 1)
        else:
            n = int(part)
            if 1 <= n <= total:
                selected.add(n - 1)
    return sorted(selected)


def _content_hash(text):
    """Return a hash of the chapter text for duplicate detection."""
    return hashlib.md5(text.encode('utf-8')).hexdigest()


def _auto_select_chapters(chapters):
    """Return list of 0-based indices for non-empty, non-duplicate chapters."""
    selected = []
    seen_hashes = set()
    for i, ch in enumerate(chapters):
        text = ch.extracted_text.strip()
        if not text:
            continue
        h = _content_hash(text)
        if h in seen_hashes:
            continue
        seen_hashes.add(h)
        selected.append(i)
    return selected


def _find_duplicates(chapters):
    """Return a dict mapping 0-based index to the 1-based number of the first
    occurrence, for chapters whose content is a duplicate."""
    seen = {}
    duplicates = {}
    for i, ch in enumerate(chapters):
        text = ch.extracted_text.strip()
        if not text:
            continue
        h = _content_hash(text)
        if h in seen:
            duplicates[i] = seen[h] + 1  # 1-based
        else:
            seen[h] = i
    return duplicates


def _load_book(input_path):
    """Load an epub or PDF file. Returns (book, chapters, cover_image, is_pdf)."""
    path_lower = input_path.lower()
    if path_lower.endswith('.pdf'):
        from .pdf_parser import get_pdf_book
        book, chapters, cover_image = get_pdf_book(input_path, resized=False)
        return book, chapters, cover_image, True
    elif path_lower.endswith('.epub'):
        from .epub_parser import get_book
        book, chapters, cover_image = get_book(input_path, resized=False)
        return book, chapters, cover_image, False
    else:
        _eprint(f"Error: Unsupported file format: {input_path}")
        _eprint("Supported formats: .epub, .pdf")
        sys.exit(1)


def cmd_list_voices(args):
    """Print all available voices grouped by language."""
    from .voices_lang import voices_internal, _PREFIX_TO_LANGUAGE

    # Build language name mapping
    LANGUAGE_NAMES = {
        'en-us': 'English (US)',
        'en-gb': 'English (GB)',
        'es': 'Spanish',
        'fr-fr': 'French',
        'hi': 'Hindi',
        'it': 'Italian',
        'ja': 'Japanese',
        'pt-br': 'Portuguese (BR)',
        'cmn': 'Chinese (Mandarin)',
    }

    # Group voices by language
    groups = {}
    for voice in voices_internal:
        prefix = voice[0]
        lang_code = _PREFIX_TO_LANGUAGE.get(prefix, 'unknown')
        lang_name = LANGUAGE_NAMES.get(lang_code, lang_code)
        groups.setdefault(lang_name, []).append(voice)

    for lang_name in sorted(groups.keys()):
        print(f"\n{lang_name}:")
        for voice in sorted(groups[lang_name]):
            gender = 'female' if voice[1] == 'f' else 'male'
            name = voice.split('_', 1)[1] if '_' in voice else voice
            print(f"  {voice:<20} ({gender}, {name})")


def cmd_list_chapters(args):
    """Print numbered list of chapters with word counts."""
    input_path = args.input
    if not Path(input_path).exists():
        _eprint(f"Error: File not found: {input_path}")
        sys.exit(1)

    book, chapters, _, is_pdf = _load_book(input_path)

    if not chapters:
        _eprint("No chapters found.")
        sys.exit(1)

    # Get titles
    if is_pdf:
        titles = [getattr(ch, 'display_title', None) for ch in chapters]
    else:
        from .epub_parser import get_chapter_titles
        titles = get_chapter_titles(book, chapters)

    duplicates = _find_duplicates(chapters)

    for i, ch in enumerate(chapters):
        word_count = len(ch.extracted_text.split())
        title = (titles[i] if titles and titles[i] else '') or ''
        num = i + 1

        if word_count == 0:
            label = "(empty)"
        elif i in duplicates:
            dup_of = duplicates[i]
            label = f"{title} (Duplicate of #{dup_of})" if title else f"(Duplicate of #{dup_of})"
        else:
            label = title

        print(f"  {num:>3}. [{word_count:>5} words] {label}")


def cmd_convert(args):
    """Run the conversion pipeline."""
    global _verbosity
    if args.quiet:
        _verbosity = 0
    elif args.verbose:
        _verbosity = 2
    else:
        _verbosity = 1

    input_path = args.input
    if not Path(input_path).exists():
        _eprint(f"Error: File not found: {input_path}")
        sys.exit(1)

    # Determine output path
    out_format = args.format
    if args.output:
        output_path = args.output
    else:
        ext = FORMAT_EXTENSIONS[out_format]
        output_path = str(Path(input_path).with_suffix(ext))

    # Validate voice
    from .voices_lang import voices_internal
    voice = args.voice
    if voice not in voices_internal:
        _eprint(f"Error: Unknown voice '{voice}'.")
        _eprint(f"Use 'list-voices' to see available voices.")
        sys.exit(1)

    # Validate speed
    speed = args.speed
    if not (0.5 <= speed <= 2.0):
        _eprint("Error: Speed must be between 0.5 and 2.0.")
        sys.exit(1)

    _eprint(f"Loading {input_path}...")
    book, chapters, cover_image, is_pdf = _load_book(input_path)

    if not chapters:
        _eprint("Error: No chapters found in the input file.")
        sys.exit(1)

    # Get metadata — both EpubBook and PdfBook expose get_metadata()
    from .epub_parser import get_title, get_author
    title = args.title if args.title else get_title(book)
    author = args.author if args.author else get_author(book)

    # Chapter selection
    if args.chapters:
        try:
            selected_indices = _parse_chapter_selection(args.chapters,
                                                        len(chapters))
        except ValueError:
            _eprint(f"Error: Invalid chapter selection: {args.chapters}")
            _eprint("Use comma-separated numbers or ranges, e.g., '1,3-5,8'")
            sys.exit(1)
        if not selected_indices:
            _eprint("Error: No valid chapters in selection.")
            sys.exit(1)
    else:
        selected_indices = _auto_select_chapters(chapters)

    chapters_selected = [chapters[i] for i in selected_indices]
    if not chapters_selected:
        _eprint("Error: No chapters selected (all empty or duplicate?).")
        sys.exit(1)

    # Get chapter titles
    detect_titles = not args.no_titles
    if detect_titles:
        if is_pdf:
            chapter_titles = [getattr(ch, 'display_title', None)
                              for ch in chapters_selected]
        else:
            from .epub_parser import get_chapter_titles
            chapter_titles = get_chapter_titles(book, chapters_selected)
    else:
        chapter_titles = None

    # Load word substitutions from config
    from .config import load_config
    config = load_config()
    substitutions = config.get('word_substitutions')
    phoneme_overrides = config.get('phoneme_overrides')
    auto_acronyms = config.get('auto_acronyms', False)

    # GPU setup — autodetect CUDA unless --no-gpu
    from .engine import set_gpu_acceleration, get_gpu_acceleration_available
    if args.no_gpu:
        use_gpu = False
    else:
        use_gpu = get_gpu_acceleration_available()
    set_gpu_acceleration(use_gpu)

    # Conversion settings
    bitrate = args.bitrate
    vbr = args.vbr
    chapter_gap = args.chapter_gap
    starting_chapter = args.starting_chapter
    heteronyms = not args.no_heteronyms
    contractions = not args.no_contractions

    _eprint(f"Title:    {title}")
    _eprint(f"Author:   {author}")
    _eprint(f"Voice:    {voice}")
    _eprint(f"Speed:    {speed}")
    _eprint(f"Format:   {out_format}")
    _eprint(f"GPU:      {'enabled' if use_gpu else 'disabled'}")
    _eprint(f"Chapters: {len(chapters_selected)} selected")
    _eprint(f"Output:   {output_path}")
    _eprint("")

    # Conversion
    from .engine import (convert_chapters_to_wav, create_m4b,
                         concat_audio_files, _INTERMEDIATE_EXTS, safe_stem,
                         chapter_wav_name)

    wav_dir = Path(input_path).parent
    stem = safe_stem(Path(input_path).stem, wav_dir)
    enc_ext = _INTERMEDIATE_EXTS.get(out_format, '.m4a')

    chapter_texts = [ch.extracted_text for ch in chapters_selected]
    all_chapter_wav_files = [
        chapter_wav_name(stem, t, wav_dir) for t in chapter_texts
    ]
    all_chapter_enc_files = [
        str(wav_dir / f'{stem}_chapter_{i}_enc{enc_ext}')
        for i in range(1, len(chapters_selected) + 1)
    ]

    resume = not args.no_resume
    if not resume:
        for wav in all_chapter_wav_files:
            try:
                Path(wav).unlink(missing_ok=True)
            except OSError:
                pass

    encode_executor = ThreadPoolExecutor(max_workers=1)
    conversion_success = False

    try:
        # ETA tracking
        word_counts = [len(ch.extracted_text.split())
                       for ch in chapters_selected]
        total_words = sum(word_counts)
        state = {'words_done': 0, 'start_time': time.time()}

        def on_start(i, total, text, is_resume):
            if is_resume:
                _eprint(f"  Chapter {i}/{total}: resuming (wav exists)")
                return
            eta_str = ""
            elapsed = time.time() - state['start_time']
            if state['words_done'] > 0 and elapsed > 0:
                wps = state['words_done'] / elapsed
                if wps > 0:
                    remaining_secs = (total_words - state['words_done']) / wps
                    if remaining_secs >= 60:
                        eta_str = f" (~{int(remaining_secs / 60)} min remaining)"
                    else:
                        eta_str = f" (~{int(remaining_secs)}s remaining)"
            _eprint(f"  Chapter {i}/{total}: "
                    f"{word_counts[i-1]} words{eta_str}")

        def on_segment(i, seg_count, est_segs):
            _eprint(f"    segment {seg_count}", level=2)

        def on_done(i, duration):
            state['words_done'] += word_counts[i - 1]
            if duration is not None:
                _eprint(f"    {duration:.1f}s audio generated", level=2)

        def on_error(i, exc):
            _eprint(f"  Chapter {i} failed: {exc}")
            state['words_done'] += word_counts[i - 1]

        result = convert_chapters_to_wav(
            chapter_texts,
            voice, speed, wav_dir, stem, encode_executor,
            out_format=out_format, bitrate=bitrate, vbr=vbr,
            chapter_gap=chapter_gap, substitutions=substitutions,
            phoneme_overrides=phoneme_overrides,
            auto_acronyms=auto_acronyms,
            heteronyms=heteronyms, contractions=contractions,
            resume=resume,
            on_chapter_start=on_start,
            on_segment=on_segment if _verbosity >= 2 else None,
            on_chapter_done=on_done,
            on_chapter_error=on_error)
        wav_files = result['wav_files']
        encode_futures = result['encode_futures']

        if not wav_files:
            _eprint("Error: No chapters were converted.", level=0)
            sys.exit(1)

        # Wait for background encoding to finish
        _eprint(f"\nAssembling {out_format} file...")
        encoded_files = []
        for wav_name in wav_files:
            future, enc_name = encode_futures[wav_name]
            future.result()
            encoded_files.append(enc_name)

        if out_format == 'm4b':
            # Build titles list aligned with successfully converted chapters
            converted_titles = None
            if chapter_titles is not None:
                converted_titles = []
                for i, chapter in enumerate(chapters_selected):
                    wav_name = chapter_wav_name(stem, chapter_texts[i], wav_dir)
                    if wav_name in wav_files:
                        converted_titles.append(chapter_titles[i])
                if not converted_titles:
                    converted_titles = None

            # cover_image is already full-size bytes (resized=False) for
            # both epub and PDF from _load_book
            final_cover = cover_image

            def progress_cb(pct):
                _eprint_progress("Muxing", pct)

            create_m4b(encoded_files, output_path, final_cover,
                       title, author, starting_chapter,
                       converted_titles,
                       progress_callback=progress_cb,
                       preencoded=True,
                       bitrate=bitrate, vbr=vbr)
            if _stderr_is_tty():
                _eprint("")
        else:
            def progress_cb(pct):
                _eprint_progress("Concatenating", pct)

            concat_audio_files(encoded_files, output_path,
                               progress_callback=progress_cb)
            if _stderr_is_tty():
                _eprint("")

        conversion_success = True
        elapsed = time.time() - state['start_time']
        if elapsed >= 60:
            elapsed_str = f"{int(elapsed / 60)}m {int(elapsed % 60)}s"
        else:
            elapsed_str = f"{int(elapsed)}s"
        _eprint(f"\nDone: {output_path} ({elapsed_str})", level=0)

    except KeyboardInterrupt:
        _eprint("\nCancelled by user. WAV files kept for resume.", level=0)
        sys.exit(130)
    except Exception as e:
        _eprint(f"\nConversion failed: {e}", level=0)
        sys.exit(1)
    finally:
        encode_executor.shutdown(wait=True)
        if conversion_success:
            # Clean up wav files on success
            for wav_file in all_chapter_wav_files:
                Path(wav_file).unlink(missing_ok=True)
        # Always remove intermediate encoded files
        for enc_file in all_chapter_enc_files:
            Path(enc_file).unlink(missing_ok=True)


def build_parser():
    """Build the argparse parser for the CLI."""
    parser = argparse.ArgumentParser(
        prog='autiobooks',
        description='Convert epub/PDF files to audiobooks using Kokoro TTS.')

    subparsers = parser.add_subparsers(dest='command', help='Available commands')

    # convert
    convert_parser = subparsers.add_parser(
        'convert', help='Convert an epub/PDF to audiobook')
    convert_parser.add_argument(
        'input', help='Path to epub or PDF file')
    convert_parser.add_argument(
        '-o', '--output', help='Output file path (default: auto from input)')
    convert_parser.add_argument(
        '--voice', default='af_heart',
        help='Voice name (default: af_heart)')
    convert_parser.add_argument(
        '--speed', type=float, default=1.0,
        help='Speed multiplier, 0.5-2.0 (default: 1.0)')
    convert_parser.add_argument(
        '--format', choices=FORMAT_EXTENSIONS.keys(), default='m4b',
        help='Output format (default: m4b)')
    convert_parser.add_argument(
        '--chapters',
        help='Chapter selection, e.g., "1,3-5,8" (default: auto-select)')
    convert_parser.add_argument(
        '--bitrate', choices=['64k', '128k', '192k'], default='64k',
        help='AAC bitrate (default: 64k)')
    convert_parser.add_argument(
        '--vbr', action='store_true',
        help='Use VBR encoding')
    convert_parser.add_argument(
        '--no-gpu', action='store_true',
        help='Disable GPU acceleration (GPU is autodetected by default)')
    convert_parser.add_argument(
        '--chapter-gap', type=float, default=2.0,
        help='Silence between chapters in seconds (default: 2.0)')
    convert_parser.add_argument(
        '--no-titles', action='store_true',
        help='Skip chapter title detection')
    convert_parser.add_argument(
        '--starting-chapter', type=int, default=1,
        help='Starting chapter number (default: 1)')
    convert_parser.add_argument(
        '--title', help='Override book title')
    convert_parser.add_argument(
        '--author', help='Override book author')
    convert_parser.add_argument(
        '--no-heteronyms', action='store_true',
        help='Disable heteronym resolution')
    convert_parser.add_argument(
        '--no-contractions', action='store_true',
        help='Disable contraction expansion')
    convert_parser.add_argument(
        '--no-resume', action='store_true',
        help='Re-convert all chapters even if cached WAV files exist')
    verbosity = convert_parser.add_mutually_exclusive_group()
    verbosity.add_argument(
        '-q', '--quiet', action='store_true',
        help='Suppress progress output')
    verbosity.add_argument(
        '-v', '--verbose', action='store_true',
        help='Show detailed per-segment progress')

    # list-chapters
    chapters_parser = subparsers.add_parser(
        'list-chapters', help='List chapters in an epub/PDF file')
    chapters_parser.add_argument(
        'input', help='Path to epub or PDF file')

    # list-voices
    subparsers.add_parser(
        'list-voices', help='List all available TTS voices')

    return parser


def main(argv=None):
    """CLI entry point."""
    parser = build_parser()
    args = parser.parse_args(argv)

    if args.command is None:
        parser.print_help()
        sys.exit(0)

    if args.command == 'list-voices':
        cmd_list_voices(args)
    elif args.command == 'list-chapters':
        cmd_list_chapters(args)
    elif args.command == 'convert':
        cmd_convert(args)


if __name__ == '__main__':
    main()
