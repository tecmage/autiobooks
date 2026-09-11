import html
import io
import os
import re
import warnings
from urllib.parse import unquote
import ebooklib
from ebooklib import epub
from bs4 import BeautifulSoup, NavigableString
from PIL import Image

# Suppress ebooklib's internal XML query warning
warnings.filterwarnings('ignore', category=FutureWarning, module='ebooklib.epub')

_chapter_cache = {}


# Elements to remove entirely (including their children)
SKIP_TAGS = {'script', 'style', 'nav', 'svg', 'math'}

# Block-level elements that should create paragraph breaks
BLOCK_TAGS = {
    'p', 'div', 'h1', 'h2', 'h3', 'h4', 'h5', 'h6',
    'blockquote', 'pre', 'li',
    'section', 'article', 'aside', 'header', 'footer', 'main',
    # `td`/`th` deliberately excluded — they're joined inline per row (see
    # the table-flattening pass in extract_text_from_html) so each ROW is one
    # line/TTS-segment. As block tags every cell landed on its own line and
    # the TTS loop read each one as an isolated utterance (slow, choppy stat
    # blocks in LitRPG fiction).
    'table', 'tr', 'caption',
    'dt', 'dd', 'dl',
    'figure', 'figcaption', 'address',
}

# Class names that indicate footnote/endnote references
FOOTNOTE_CLASSES = {'noteref', 'footnote-ref', 'endnote-ref', 'fn-ref'}

# Filename stems (basename minus extension, lowercased) for non-content
# documents that get spuriously listed as chapters — covers, title pages,
# colophons, etc. The book's actual cover image is rendered separately;
# the title page mostly carries duplicate metadata.
_EXCLUDED_FILE_STEMS = {
    'cover', 'titlepage', 'title_page', 'title-page',
}

# TOC labels (lowercased, stripped) that mark non-content entries.
_EXCLUDED_TOC_TITLES = {
    'cover', 'cover page', 'title page', 'titlepage',
}


def _is_footnote_ref(tag):
    """Detect footnote/endnote reference links that clutter TTS output."""
    # epub:type is a space-separated token list (EPUB 3 spec), not a single
    # value — lxml preserves it as one raw attribute string
    # (e.g. "noteref backlink"), so an == check misses every multi-token
    # case. Only 'noteref' opts in; 'backlink' (the link back to the note
    # body) and 'footnote' (the note body itself) are legitimate content
    # and must not be swallowed here.
    epub_type = tag.get('epub:type', '')
    if 'noteref' in epub_type.split():
        return True
    classes = set(tag.get('class', []))
    if classes & FOOTNOTE_CLASSES:
        return True
    href = tag.get('href', '')
    if href.startswith('#') and tag.find('sup'):
        return True
    return False


def extract_text_from_html(html_content):
    """Extract readable text from epub HTML with proper structure."""
    soup = BeautifulSoup(html_content, features='lxml')

    # Remove non-content elements
    for el in soup.find_all(SKIP_TAGS):
        el.decompose()

    # Remove footnote/endnote reference links
    for a in soup.find_all('a'):
        if _is_footnote_ref(a):
            a.decompose()

    # Replace <br> with newline text nodes
    for br in soup.find_all('br'):
        br.replace_with('\n')

    # Replace <img> with alt text
    for img in soup.find_all('img'):
        alt = img.get('alt', '').strip()
        if alt:
            img.replace_with(alt)
        else:
            img.decompose()

    # Replace <hr> with a blank line (natural pause)
    for hr in soup.find_all('hr'):
        hr.replace_with('\n\n')

    # Flatten table cells inline so each ROW (not each cell) is one line and
    # thus one TTS segment. Cells join with ', '; a cell ending in ':' (a stat
    # label) joins to its value with a space instead, so "Strength: 432, Mana:
    # 306" reads naturally. The whole row is rebuilt (not just separators
    # appended) because the inter-cell whitespace/newlines in the source HTML
    # would otherwise survive and re-split the row. Empty cells are dropped.
    for tr in soup.find_all('tr'):
        # Skip rows that contain a nested table — an outer cell's get_text()
        # already includes the inner table's text, so processing this row
        # AND the inner table's rows double-speaks every inner cell. Only
        # the innermost rows (no descendant <table>) get flattened; the
        # outer row is left for the generic block-tag pass to separate.
        if tr.find('table'):
            continue
        cells = [c.get_text(' ', strip=True) for c in tr.find_all(['td', 'th'])]
        cells = [c for c in cells if c]
        parts = []
        for i, text in enumerate(cells):
            parts.append(text)
            if i < len(cells) - 1:
                parts.append(' ' if text.endswith(':') else ', ')
        tr.clear()
        tr.append(NavigableString(''.join(parts)))

    # Insert newline markers around block-level elements
    for tag in soup.find_all(BLOCK_TAGS):
        tag.insert_before(NavigableString('\n'))
        tag.append(NavigableString('\n'))

    # Extract all text and normalize whitespace
    raw = soup.get_text()
    lines = []
    for line in raw.split('\n'):
        line = ' '.join(line.split())
        if line:
            lines.append(line)

    return '\n'.join(lines)


def get_book(file_path, resized):
    book = epub.read_epub(file_path, options={'ignore_ncx': True})
    # A pure EPUB2 whose NCX navMap yields zero DAISY-namespace navPoints
    # (e.g. an empty <navMap/>) makes ebooklib set book.toc to a bare Link
    # object instead of a list — truthy but not iterable, so every
    # downstream `if toc:` / `for entry in toc:` would raise TypeError.
    if not isinstance(getattr(book, 'toc', None), (list, tuple)):
        book.toc = []
    chapters = find_document_chapters_and_extract_texts(book)
    # A cover PIL can't decode (SVG cover item, corrupt JPEG) must not
    # abort the load of an otherwise readable book — fall back to the
    # placeholder cover instead.
    try:
        cover_image = get_cover_image(book, resized=resized)
    except Exception:
        cover_image = None
    return (book, chapters, cover_image)


def get_book_cached(file_path, resized):
    """Return (book, chapters, cover_image), cached per
    (path, mtime, size, resized).

    Re-parses when the file is modified on disk. Cover images are PhotoImage
    objects when resized=True; callers must keep a reference to prevent GC.
    Size is included alongside mtime because some copy/sync tools preserve
    the original mtime on a replaced file, which would otherwise serve a
    stale cached parse forever.
    """
    try:
        st = os.stat(file_path)
        mod_time = st.st_mtime
        size = st.st_size
    except OSError:
        mod_time = 0
        size = 0
    cache_key = (str(file_path), mod_time, size, bool(resized))
    cached = _chapter_cache.get(cache_key)
    if cached is not None:
        return cached
    result = get_book(file_path, resized)
    _chapter_cache[cache_key] = result
    return result


def clear_chapter_cache(file_path=None):
    """Clear the chapter cache; if file_path is given, only drop its entries."""
    if file_path is None:
        _chapter_cache.clear()
        return
    path_str = str(file_path)
    stale = [k for k in _chapter_cache if k[0] == path_str]
    for k in stale:
        del _chapter_cache[k]


def is_valid_chapter(chapter):
    if chapter.get_type() == ebooklib.ITEM_DOCUMENT:
        return True
    if chapter.get_type() == ebooklib.ITEM_UNKNOWN:
        if chapter.media_type == 'text/html':
            return True
    return False


def _get_chapter_html(chapter):
    """Return raw HTML for a chapter, trying get_body_content() then get_content()."""
    try:
        return chapter.get_body_content()
    except Exception:
        try:
            return chapter.get_content()
        except Exception:
            return None


def _is_excluded_chapter(chapter, toc_titles):
    """Skip cover/title-page documents that aren't real chapters."""
    basename = chapter.file_name.rsplit('/', 1)[-1].lower()
    stem = basename.rsplit('.', 1)[0] if '.' in basename else basename
    if stem in _EXCLUDED_FILE_STEMS:
        return True
    toc_title = (toc_titles.get(chapter.file_name) or '').strip().lower()
    if toc_title in _EXCLUDED_TOC_TITLES:
        return True
    return False


def _spine_ordered_items(book):
    """Return the book's items in spine (reading) order, manifest order after.

    get_items() yields manifest order, which some EPUBs write differently
    from the spine — converting in manifest order shuffles audio chapters
    while the TOC-driven tree still looks correct. The spine is the
    authoritative reading order; items not in the spine (cover docs, images,
    css) keep their manifest order, after the spine block.
    """
    items = list(book.get_items())
    spine_order = {}
    for idx, entry in enumerate(getattr(book, 'spine', None) or []):
        if isinstance(entry, (tuple, list)):
            idref = entry[0]
        elif hasattr(entry, 'id'):
            idref = entry.id
        else:
            idref = entry
        spine_order.setdefault(idref, idx)
    in_spine = sorted(
        (it for it in items if getattr(it, 'id', None) in spine_order),
        key=lambda it: spine_order[it.id])
    rest = [it for it in items if getattr(it, 'id', None) not in spine_order]
    return in_spine + rest


# Calibre's file-size splitter names the pieces of an oversized source file
# `part0004_split_000.html`, `part0004_split_001.html`, … — the split is
# purely technical, not a chapter boundary.
_CALIBRE_SPLIT_RE = re.compile(r'^(?P<stem>.+)_split_\d+$')


def _split_group_key(file_name):
    """Group key for Calibre `_split_NNN` sibling files: (dir, base stem),
    or None when the name doesn't follow the split convention."""
    dirname, _, base = file_name.rpartition('/')
    stem = base.rsplit('.', 1)[0] if '.' in base else base
    m = _CALIBRE_SPLIT_RE.match(stem)
    if not m:
        return None
    return (dirname, m.group('stem'))


def _toc_href_to_filename(href):
    """Normalize a TOC href for matching against manifest file_names: strip
    the #fragment, unquote percent-encoding (ebooklib's manifest parsing
    unquotes but _parse_ncx/_parse_nav don't), and fold backslashes to
    forward slashes (OCF forbids backslash in names, but ebooklib's own
    write_epub emits backslash hrefs when run on Windows)."""
    return unquote(href.split('#')[0]).replace('\\', '/')


def _toc_referenced_files(toc, result=None):
    """Set of every document filename any TOC entry (Link or Section) points
    at. Unlike _build_toc_map this keeps hrefs whose entry has no title —
    a title-less entry still claims the file as a chapter start."""
    if result is None:
        result = set()
    for entry in toc:
        if isinstance(entry, tuple):
            section, children = entry
            href = getattr(section, 'href', '') or ''
            if href:
                result.add(_toc_href_to_filename(href))
            _toc_referenced_files(children, result)
        else:
            href = getattr(entry, 'href', '') or ''
            if href:
                result.add(_toc_href_to_filename(href))
    return result


def find_document_chapters_and_extract_texts(book):
    """Returns every chapter that is an ITEM_DOCUMENT
    and enriches each chapter with extracted_text.

    Calibre `_split_NNN` continuation files are folded into their preceding
    sibling: some split books put the whole chapter body in a `_split_001`
    file the TOC never references (one had a 2-word "Chapter 1" stub as the
    TOC-referenced `_split_000`), so kept separate they surface as dozens of
    untitled leftover chapters and doubled m4b markers. A split sibling the
    TOC *does* reference stays its own chapter — that's the page-break kind
    of split, where the TOC points at each piece as a real chapter start.
    """
    toc = getattr(book, 'toc', None) or []
    toc_titles = _build_toc_map(toc) if toc else {}
    toc_refs = _toc_referenced_files(toc) if toc else set()
    document_chapters = []
    for chapter in _spine_ordered_items(book):
        if not is_valid_chapter(chapter):
            continue
        if _is_excluded_chapter(chapter, toc_titles):
            continue
        xml = _get_chapter_html(chapter)
        if xml is None:
            continue
        chapter.extracted_text = extract_text_from_html(xml)
        if (document_chapters
                and chapter.file_name not in toc_refs
                and _split_group_key(chapter.file_name) is not None
                and (_split_group_key(chapter.file_name)
                     == _split_group_key(document_chapters[-1].file_name))):
            # A merged group keeps the FIRST sibling's file_name, so a
            # _split_002 still matches its group and TOC/title lookups
            # (keyed on the _split_000 name) work on the merged chapter.
            prev = document_chapters[-1]
            pieces = (prev.extracted_text.rstrip(),
                      chapter.extracted_text.lstrip())
            prev.extracted_text = '\n'.join(p for p in pieces if p)
            continue
        document_chapters.append(chapter)
    return document_chapters


def _extract_heading(chapter):
    """Extract the first heading from a chapter's HTML as a fallback title."""
    xml = _get_chapter_html(chapter)
    if xml is None:
        return None
    soup = BeautifulSoup(xml, features='lxml')
    for tag_name in ['h1', 'h2', 'h3', 'title']:
        tag = soup.find(tag_name)
        if tag:
            text = tag.get_text(strip=True)
            if text:
                return text
    return None


def _build_toc_map(toc, result=None):
    """Recursively walk the TOC tree and build a filename → title dict."""
    if result is None:
        result = {}
    for entry in toc:
        if isinstance(entry, tuple):
            # (Section, [children]) — ebooklib puts the parent navPoint's
            # own document href on the Section, not just its children. A
            # Section with a truthy href is itself a real spine document
            # (e.g. a "Part I" page) and must be mapped like a Link before
            # recursing, or it loses its TOC title and (for a Section-form
            # "Cover" label) becomes invisible to _is_excluded_chapter.
            section, children = entry
            section_href = getattr(section, 'href', '') or ''
            section_title = getattr(section, 'title', '') or ''
            if section_href and section_title:
                filename = _toc_href_to_filename(section_href)
                result.setdefault(filename, section_title)
            _build_toc_map(children, result)
        else:
            # epub.Link object with .href and .title
            href = getattr(entry, 'href', '') or ''
            title = getattr(entry, 'title', '') or ''
            if href and title:
                # Fragment stripped (e.g. "chapter1.xhtml#section2").
                # First entry wins for files with multiple TOC entries — the
                # chapter tree shows the first section title, so the m4b
                # chapter marker must match it.
                filename = _toc_href_to_filename(href)
                result.setdefault(filename, title)
    return result


def get_chapter_titles(book, chapters):
    """Return a list of chapter titles matching the given chapters.
    Uses TOC titles when available, falls back to first heading in HTML.
    Guarantees every entry is a string (empty if nothing could be extracted).
    """
    toc_map = _build_toc_map(book.toc)
    titles = []
    for chapter in chapters:
        title = toc_map.get(chapter.file_name)
        if not title:
            title = _extract_heading(chapter)
        titles.append(title or '')
    return titles


def get_title(book):
    try:
        return book.get_metadata('DC', 'title')[0][0] or ''
    except (IndexError, TypeError):
        return ''


def get_author(book):
    try:
        return book.get_metadata('DC', 'creator')[0][0] or ''
    except (IndexError, TypeError):
        return ''


def get_publisher(book):
    try:
        return book.get_metadata('DC', 'publisher')[0][0] or ''
    except (IndexError, TypeError):
        return ''


def get_publication_year(book):
    try:
        date = book.get_metadata('DC', 'date')[0][0] or ''
        m = re.search(r'\b(19|20)\d{2}\b', date)
        return m.group(0) if m else ''
    except (IndexError, TypeError):
        return ''


def get_description(book):
    try:
        raw = book.get_metadata('DC', 'description')[0][0] or ''
    except (IndexError, TypeError):
        return ''
    if not raw:
        return ''
    decoded = html.unescape(raw)
    return extract_text_from_html(decoded)


def resized_image(item):
    image_data = item.get_content()
    image = Image.open(io.BytesIO(image_data))
    image.thumbnail((200, 300))
    ratio = min(200/image.width, 300/image.height)
    new_size = (int(image.width * ratio), int(image.height * ratio))
    resized = image.resize(new_size, Image.Resampling.LANCZOS)
    background = Image.new('RGB', (200, 300), 'gray')
    offset = ((200 - new_size[0])//2, (300 - new_size[1])//2)
    background.paste(resized, offset)
    # Imported lazily: PIL.ImageTk imports tkinter at module top, which
    # must not be required for headless CLI runs (resized=False paths).
    from PIL import ImageTk
    return ImageTk.PhotoImage(background)


def get_cover_image(book, resized):
    # Two passes: a flagged ITEM_COVER always wins, regardless of manifest
    # order. Scanning both types in one loop let an earlier-in-manifest
    # ITEM_IMAGE named e.g. "back-cover.png" beat the real ITEM_COVER that
    # happened to appear later.
    items = list(book.get_items())
    for item in items:
        if item.get_type() == ebooklib.ITEM_COVER:
            if resized:
                return resized_image(item)
            else:
                return item.get_content()
    for item in items:
        if item.get_type() == ebooklib.ITEM_IMAGE:
            if 'cover' in item.get_name().lower():
                if resized:
                    return resized_image(item)
                else:
                    return item.get_content()
    return None
