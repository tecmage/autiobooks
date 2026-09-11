"""Headless CLI for autiobooks — epub/PDF to audiobook conversion."""

import argparse
import shutil
import sys
import time
import warnings
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

from .selection import find_duplicates, auto_select_indices

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
    """Parse a chapter selection string like '1,3-5,8' into a set of 0-based indices.

    Parts that are entirely out of range (e.g. "99" on a 20-chapter book) or
    reversed ranges (e.g. "10-5") contribute nothing to the result; those are
    reported via a level-0 warning (visible even under -q) so a typo doesn't
    silently convert a near-empty selection with no signal. A range that is
    only partially out of range (e.g. "1-999" on a 5-chapter book) is clamped
    without a warning — that's the documented, expected behavior.
    """
    selected = set()
    out_of_range = []
    invalid_ranges = []
    for part in spec.split(','):
        part = part.strip()
        if '-' in part:
            start_s, end_s = part.split('-', 1)
            start = int(start_s)
            end = int(end_s)
            if start > end:
                invalid_ranges.append(part)
                continue
            # Clamp the loop bounds themselves rather than filtering
            # per-iteration — a literal range like "1-999999999" on a
            # small book used to walk the full unclamped span (tens of
            # minutes of pegged CPU for pathological input) even though
            # every out-of-range n was immediately discarded.
            lo, hi = max(start, 1), min(end, total)
            if lo > hi:
                out_of_range.append(part)
            else:
                selected.update(range(lo - 1, hi))
        else:
            n = int(part)
            if 1 <= n <= total:
                selected.add(n - 1)
            else:
                out_of_range.append(part)
    if out_of_range or invalid_ranges:
        msgs = []
        if out_of_range:
            msgs.append(f"chapters out of range: {', '.join(out_of_range)}")
        if invalid_ranges:
            msgs.append(f"invalid ranges: {', '.join(invalid_ranges)}")
        _eprint(f"Warning: {'; '.join(msgs)}", level=0)
    return sorted(selected)


def _load_book(input_path):
    """Load an epub or PDF file. Returns (book, chapters, cover_image, is_pdf)."""
    path_lower = input_path.lower()
    if not path_lower.endswith(('.pdf', '.epub')):
        _eprint(f"Error: Unsupported file format: {input_path}", level=0)
        _eprint("Supported formats: .epub, .pdf", level=0)
        sys.exit(1)
    try:
        if path_lower.endswith('.pdf'):
            from .pdf_parser import get_pdf_book
            book, chapters, cover_image = get_pdf_book(
                input_path, resized=False)
            return book, chapters, cover_image, True
        from .epub_parser import get_book
        book, chapters, cover_image = get_book(input_path, resized=False)
        return book, chapters, cover_image, False
    except Exception as e:
        # Friendly message instead of a raw traceback for encrypted PDFs,
        # corrupt archives, etc.
        _eprint(f"Error: Could not open {input_path}:\n{e}", level=0)
        sys.exit(1)


def cmd_list_voices(args):
    """Print all available voices grouped by language."""
    from .voices_lang import (
        voices_internal, _PREFIX_TO_LANGUAGE,
        discover_custom_voices, get_voices_dir,
    )

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

    # Custom voices win name collisions with built-ins (resolve_voice checks
    # the custom dir first) — exclude collisions from the built-in language
    # groups so a colliding name is listed once, under Custom.
    custom = discover_custom_voices()
    custom_set = set(custom)

    # Group voices by language
    groups = {}
    for voice in voices_internal:
        if voice in custom_set:
            continue
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

    if custom:
        print(f"\nCustom (from {get_voices_dir()}):")
        for voice in custom:
            lang_code = _PREFIX_TO_LANGUAGE.get(voice[0], 'unknown')
            lang_name = LANGUAGE_NAMES.get(lang_code, lang_code)
            print(f"  {voice:<20} ({lang_name})")


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

    duplicates = find_duplicates(chapters)

    for i, ch in enumerate(chapters):
        word_count = len(ch.extracted_text.split())
        title = (titles[i] if titles and titles[i] else '') or ''
        num = i + 1

        if word_count == 0:
            label = "(empty)"
        elif i in duplicates:
            dup_of = duplicates[i] + 1  # display is 1-based
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

    # On Windows, ffmpeg may only exist in the GUI's managed download dir,
    # which a separate CLI process never sees on PATH otherwise.
    from . import runtime
    runtime.ensure_bin_in_path()

    # Both binaries are checked: assemble_output probes durations with ffprobe
    # just as surely as it muxes with ffmpeg, and a distro that splits them
    # into separate packages can leave ffprobe missing. Checking only ffmpeg
    # let such a box get all the way through a full TTS run before dying at
    # probe time.
    missing = [b for b in ('ffmpeg', 'ffprobe') if shutil.which(b) is None]
    if missing:
        # Fail now, not after a full TTS run at mux/encode time.
        _eprint(f"Error: {' and '.join(missing)} not found on PATH — install "
                f"{'it' if len(missing) == 1 else 'them'} or run the GUI "
                "once to download the managed copy.",
                level=0)
        sys.exit(1)

    input_path = args.input
    if not Path(input_path).exists():
        _eprint(f"Error: File not found: {input_path}", level=0)
        sys.exit(1)

    # Determine output path
    out_format = args.format
    if args.output:
        output_path = args.output
    else:
        ext = FORMAT_EXTENSIONS[out_format]
        output_path = str(Path(input_path).with_suffix(ext))
    out_parent = Path(output_path).parent
    if not out_parent.is_dir():
        # Fail now, not after hours of TTS at mux time.
        _eprint(f"Error: Output directory does not exist: {out_parent}", level=0)
        sys.exit(1)

    # Saved GUI preferences serve as defaults so the same book converts to
    # the same audio from either interface; explicit flags still override.
    from .config import load_config
    config = load_config()

    # Validate voice — default is the saved GUI voice, then af_heart
    from .voices_lang import (voices_internal, discover_custom_voices,
                              deemojify_voice)
    voice = args.voice
    if voice is None:
        saved = config.get('voice')
        voice = (deemojify_voice(saved)
                 if isinstance(saved, str) and saved else 'af_heart')
        if (voice not in voices_internal
                and voice not in discover_custom_voices()):
            voice = 'af_heart'
    if voice not in voices_internal and voice not in discover_custom_voices():
        _eprint(f"Error: Unknown voice '{voice}'.", level=0)
        _eprint("Use 'list-voices' to see available voices.", level=0)
        sys.exit(1)

    # Validate speed
    speed = args.speed
    if not (0.5 <= speed <= 2.0):
        _eprint("Error: Speed must be between 0.5 and 2.0.", level=0)
        sys.exit(1)

    _eprint(f"Loading {input_path}...")
    book, chapters, cover_image, is_pdf = _load_book(input_path)

    if not chapters:
        _eprint("Error: No chapters found in the input file.", level=0)
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
            _eprint(f"Error: Invalid chapter selection: {args.chapters}",
                    level=0)
            _eprint("Use comma-separated numbers or ranges, e.g., '1,3-5,8'",
                    level=0)
            sys.exit(1)
        if not selected_indices:
            _eprint("Error: No valid chapters in selection.", level=0)
            sys.exit(1)
    else:
        selected_indices = auto_select_indices(chapters)

    chapters_selected = [chapters[i] for i in selected_indices]
    if not chapters_selected:
        _eprint("Error: No chapters selected (all empty or duplicate?).",
                level=0)
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
    from .config import sanitize_dict_list
    substitutions = sanitize_dict_list(
        config.get('word_substitutions'), str_keys=('find', 'replace'))
    phoneme_overrides = sanitize_dict_list(
        config.get('phoneme_overrides'), str_keys=('word', 'ipa'))
    auto_acronyms = config.get('auto_acronyms', False)

    # GPU setup — autodetect CUDA unless --no-gpu
    from .engine import set_gpu_acceleration, get_gpu_acceleration_available
    if args.no_gpu:
        use_gpu = False
    else:
        use_gpu = get_gpu_acceleration_available()
    set_gpu_acceleration(use_gpu)

    # Conversion settings. Pronunciation toggles default to the saved GUI
    # preferences (the CLI previously hardcoded them on, silently ignoring
    # a user who disabled heteronyms in Preferences).
    bitrate = args.bitrate
    vbr = args.vbr
    chapter_gap = args.chapter_gap
    starting_chapter = args.starting_chapter
    heteronyms = (args.heteronyms if args.heteronyms is not None
                  else bool(config.get('heteronyms', True)))
    contractions = (args.contractions if args.contractions is not None
                    else bool(config.get('contractions', False)))
    read_title_author = (args.read_title_author
                         if args.read_title_author is not None
                         else bool(config.get('read_title_author', True)))

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
    from .engine import (convert_chapters_to_wav, assemble_output,
                         _INTERMEDIATE_EXTS, safe_stem,
                         find_chapter_wavs, unlink_with_retry)

    wav_dir = Path(input_path).parent
    stem = safe_stem(Path(input_path).stem, wav_dir)
    enc_ext = _INTERMEDIATE_EXTS.get(out_format, '.m4a')

    chapter_texts = [ch.extracted_text for ch in chapters_selected]
    if read_title_author and chapter_texts:
        # Same format string as the GUI so chapter-1 WAV names (hashed from
        # the text) match and resume caches interoperate.
        chapter_texts[0] = f"{title} by {author}.\n{chapter_texts[0]}"
    all_chapter_enc_files = [
        str(wav_dir / f'{stem}_chapter_{i}_enc{enc_ext}')
        for i in range(1, len(chapters_selected) + 1)
    ]

    resume = not args.no_resume
    if not resume:
        # Stem-wide sweep, not current-key names: --no-resume must clear
        # WAVs cached under ANY prior settings, whose render_key-hashed
        # filenames a current-key list cannot reproduce.
        for wav in find_chapter_wavs(stem, wav_dir):
            err = unlink_with_retry(wav)
            if err is not None:
                _eprint(f"Warning: could not remove {wav}: {err}")

    encode_executor = ThreadPoolExecutor(max_workers=1)
    conversion_success = False

    try:
        # ETA tracking
        word_counts = [len(ch.extracted_text.split())
                       for ch in chapters_selected]
        total_words = sum(word_counts)
        state = {'words_done': 0, 'start_time': time.time(),
                 'words_remaining': total_words}
        resumed_indices = set()
        failed_chapters = []

        def on_start(i, total, text, is_resume):
            if is_resume:
                resumed_indices.add(i)
                # Resumed-from-disk / duplicate-reuse chapters cost ~0
                # wall clock — drop them from the remaining-work total
                # immediately so the words/sec rate isn't computed
                # against instantaneous "free" progress.
                state['words_remaining'] -= word_counts[i - 1]
                _eprint(f"  Chapter {i}/{total}: resuming (wav exists)")
                return
            eta_str = ""
            elapsed = time.time() - state['start_time']
            if state['words_done'] > 0 and elapsed > 0:
                wps = state['words_done'] / elapsed
                if wps > 0:
                    remaining_secs = state['words_remaining'] / wps
                    if remaining_secs >= 60:
                        eta_str = f" (~{int(remaining_secs / 60)} min remaining)"
                    else:
                        eta_str = f" (~{int(remaining_secs)}s remaining)"
            _eprint(f"  Chapter {i}/{total}: "
                    f"{word_counts[i-1]} words{eta_str}")

        def on_segment(i, seg_count, est_segs):
            _eprint(f"    segment {seg_count}", level=2)

        def on_done(i, duration):
            # Resumed/duplicate chapters already had their words dropped
            # from words_remaining in on_start; crediting them here too
            # would double count and re-poison the words/sec rate.
            if i not in resumed_indices:
                state['words_done'] += word_counts[i - 1]
                state['words_remaining'] -= word_counts[i - 1]
            if duration is not None:
                _eprint(f"    {duration:.1f}s audio generated", level=2)

        def on_error(i, exc):
            _eprint(f"  Chapter {i} failed: {exc}")
            state['words_done'] += word_counts[i - 1]
            state['words_remaining'] -= word_counts[i - 1]
            failed_chapters.append((i, exc))

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
        progress_label = "Muxing" if out_format == 'm4b' else "Concatenating"

        def progress_cb(pct):
            _eprint_progress(progress_label, pct)

        # cover_image is already full-size bytes (resized=False) for both
        # epub and PDF from _load_book
        assemble_output(result, chapter_texts, chapter_titles,
                        stem, wav_dir, out_format, output_path,
                        cover_image, title, author,
                        starting_chapter=starting_chapter,
                        bitrate=bitrate, vbr=vbr,
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

        if failed_chapters:
            fail_desc = ", ".join(f"#{i} ({exc})" for i, exc in failed_chapters)
            _eprint(f"WARNING: {len(failed_chapters)} chapter(s) failed and "
                    f"are missing from the output: {fail_desc}", level=0)
            sys.exit(1)

    except KeyboardInterrupt:
        _eprint("\nCancelled by user. WAV files kept for resume.", level=0)
        sys.exit(130)
    except Exception as e:
        _eprint(f"\nConversion failed: {e}", level=0)
        sys.exit(1)
    finally:
        encode_executor.shutdown(wait=True)
        # unlink_with_retry never raises (returns the error instead), so a
        # Windows file-lock during cleanup can't mask the real failure that
        # got us into this finally block.
        if conversion_success:
            # Clean up wav files on success — stem-wide, so orphans left
            # by an earlier cancelled run under different settings (other
            # render_key, other filenames) are reclaimed too. Cancel and
            # failure keep WAVs for resume.
            for wav_file in find_chapter_wavs(stem, wav_dir):
                err = unlink_with_retry(wav_file)
                if err is not None:
                    _eprint(f"Warning: could not remove {wav_file}: {err}")
        # Always remove intermediate encoded files
        for enc_file in all_chapter_enc_files:
            err = unlink_with_retry(enc_file)
            if err is not None:
                _eprint(f"Warning: could not remove {enc_file}: {err}")


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
        '--voice', default=None,
        help='Voice name (default: saved GUI preference, else af_heart)')
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
        '--heteronyms', action=argparse.BooleanOptionalAction, default=None,
        help='Heteronym resolution (default: saved GUI preference, else on)')
    convert_parser.add_argument(
        '--contractions', action=argparse.BooleanOptionalAction, default=None,
        help='Contraction expansion (default: saved GUI preference, else off)')
    convert_parser.add_argument(
        '--read-title-author', action=argparse.BooleanOptionalAction,
        default=None,
        help='Prepend "Title by Author" to the first chapter '
             '(default: saved GUI preference, else on)')
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
