import html
import io
import os
import re
import warnings
import ebooklib
from ebooklib import epub
from bs4 import BeautifulSoup, NavigableString
from PIL import Image, ImageTk

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
    'table', 'tr', 'td', 'th', 'caption',
    'dt', 'dd', 'dl',
    'figure', 'figcaption', 'address',
}

# Class names that indicate footnote/endnote references
FOOTNOTE_CLASSES = {'noteref', 'footnote-ref', 'endnote-ref', 'fn-ref'}


def _is_footnote_ref(tag):
    """Detect footnote/endnote reference links that clutter TTS output."""
    epub_type = tag.get('epub:type', '')
    if epub_type == 'noteref':
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
    chapters = find_document_chapters_and_extract_texts(book)
    cover_image = get_cover_image(book, resized=resized)
    return (book, chapters, cover_image)


def get_book_cached(file_path, resized):
    """Return (book, chapters, cover_image), cached per (path, mtime, resized).

    Re-parses when the file is modified on disk. Cover images are PhotoImage
    objects when resized=True; callers must keep a reference to prevent GC.
    """
    try:
        mod_time = os.path.getmtime(file_path)
    except OSError:
        mod_time = 0
    cache_key = (str(file_path), mod_time, bool(resized))
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


def find_document_chapters_and_extract_texts(book):
    """Returns every chapter that is an ITEM_DOCUMENT
    and enriches each chapter with extracted_text."""
    document_chapters = []
    for chapter in book.get_items():
        if not is_valid_chapter(chapter):
            continue
        xml = _get_chapter_html(chapter)
        if xml is None:
            continue
        chapter.extracted_text = extract_text_from_html(xml)
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
            # (Section, [children]) — recurse into children
            _build_toc_map(entry[1], result)
        else:
            # epub.Link object with .href and .title
            href = getattr(entry, 'href', '') or ''
            title = getattr(entry, 'title', '') or ''
            if href and title:
                # Strip fragment identifiers (e.g. "chapter1.xhtml#section2")
                filename = href.split('#')[0]
                result[filename] = title
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
    return ImageTk.PhotoImage(background)


def get_cover_image(book, resized):
    for item in book.get_items():
        if item.get_type() == ebooklib.ITEM_COVER:
            if resized:
                return resized_image(item)
            else:
                return item.get_content()
        if item.get_type() == ebooklib.ITEM_IMAGE:
            if 'cover' in item.get_name().lower():
                if resized:
                    return resized_image(item)
                else:
                    return item.get_content()
    return None
