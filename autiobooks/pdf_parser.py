"""PDF parsing support using pypdf (BSD licensed)."""

import io
import re
from PIL import Image
from pypdf import PdfReader


class PdfChapter:
    """Mimics ebooklib chapter interface for compatibility."""

    def __init__(self, title, text, file_name):
        self.extracted_text = text
        self.display_title = title
        self.file_name = file_name


class PdfBook:
    """Minimal book interface matching what autiobooks expects from ebooklib."""

    def __init__(self, metadata, toc):
        self._metadata = metadata
        self.toc = toc

    def get_metadata(self, namespace, key):
        val = self._metadata.get(f'{namespace}:{key}')
        if val is not None:
            return [(val, {})]
        return []


def _clean_pdf_text(text):
    """Clean text extracted from PDF pages."""
    text = re.sub(r'^\s*\d+\s*$', '', text, flags=re.MULTILINE)
    text = re.sub(r'\n{3,}', '\n\n', text)
    lines = []
    for line in text.split('\n'):
        line = line.rstrip()
        if line:
            lines.append(line)
    return '\n'.join(lines)


_ENCRYPTED_MESSAGE = (
    'This PDF is password-protected. Remove the password '
    '(e.g. with a PDF tool) and try again.')


def _decrypt_or_raise(reader):
    """Handle an encrypted reader: owner-password-only PDFs (the common
    "no copy/no print" restriction, with an EMPTY user password) are
    transparently readable by pypdf — `PdfReader.__init__` already tries an
    empty password automatically, so `is_encrypted` alone can't tell us
    whether the content is actually accessible. Try `decrypt('')` and then
    probe real access; raise the friendly message only when that genuinely
    fails (a real user password is required).
    """
    try:
        reader.decrypt('')
    except Exception:
        pass
    try:
        if len(reader.pages) == 0:
            raise ValueError('empty document')
        _ = reader.pages[0]
    except Exception:
        raise ValueError(_ENCRYPTED_MESSAGE)


def get_pdf_book(file_path, resized=True):
    """Parse a PDF file and return (book, chapters, cover_image).

    Uses the PDF's outline (bookmarks) for chapter structure. Falls back
    to page groups if no outline is present.
    """
    reader = PdfReader(file_path)
    if reader.is_encrypted:
        _decrypt_or_raise(reader)

    meta = reader.metadata or {}
    # creation_date_raw looks like 'D:20230115120000' — strip the prefix so
    # the year regex in get_publication_year (which requires a word
    # boundary after the year) can match.
    raw_date = str(getattr(meta, 'creation_date_raw', '') or '')
    if raw_date.startswith('D:') and len(raw_date) >= 6:
        raw_date = raw_date[2:6]
    metadata = {
        'DC:title': getattr(meta, 'title', '') or '',
        'DC:creator': getattr(meta, 'author', '') or '',
        'DC:publisher': '',
        'DC:date': raw_date,
        'DC:description': getattr(meta, 'subject', '') or '',
    }

    cover_image = _extract_cover(reader, resized)

    outline = _get_outline(reader)
    if outline and len(outline) > 1:
        chapters = _chapters_from_outline(reader, outline)
        book_toc = _build_book_toc(outline)
    else:
        chapters = _chapters_from_pages(reader)
        book_toc = []

    book = PdfBook(metadata, book_toc)
    return (book, chapters, cover_image)


def _get_outline(reader):
    """Extract flat list of (level, title, page_number) from PDF outline."""
    try:
        raw_outline = reader.outline
    except Exception:
        return []
    if not raw_outline:
        return []

    entries = []

    def walk(items, level=1):
        for item in items:
            if isinstance(item, list):
                walk(item, level + 1)
            else:
                title = item.title or ''
                try:
                    page_num = reader.get_destination_page_number(item)
                except Exception:
                    continue
                # pypdf returns -1 for unresolvable destinations; without this
                # guard, range(-1, end) reads reader.pages[-1] (last page).
                if page_num is None or page_num < 0:
                    continue
                entries.append((level, title, page_num))

    walk(raw_outline)
    return entries


def _extract_cover(reader, resized):
    """Extract cover image from the first page's resources."""
    try:
        page = reader.pages[0]
        x_object = page.get('/Resources', {}).get('/XObject', {})
        if hasattr(x_object, 'get_object'):
            x_object = x_object.get_object()
        if not x_object:
            return None

        for obj_name in x_object:
            obj = x_object[obj_name].get_object()
            if obj.get('/Subtype') == '/Image':
                width = obj.get('/Width', 0)
                height = obj.get('/Height', 0)
                if width < 100 or height < 100:
                    continue
                data = obj.get_data()
                color_space = obj.get('/ColorSpace')
                bits = obj.get('/BitsPerComponent', 8)
                filt = obj.get('/Filter')
                # /Filter may be an ArrayObject (list subclass) for a single
                # filter (`[/DCTDecode]`) or a decode chain
                # (`[/FlateDecode /DCTDecode]`); pypdf applies filters in
                # order, so the LAST element is the one that determines the
                # final encoding of the stored bytes returned by get_data().
                if isinstance(filt, (list, tuple)):
                    filt = str(filt[-1]) if filt else None

                if filt in ('/DCTDecode', '/JPXDecode'):
                    img = Image.open(io.BytesIO(data))
                elif filt == '/FlateDecode':
                    mode = 'RGB' if str(color_space) == '/DeviceRGB' else 'L'
                    try:
                        img = Image.frombytes(mode, (width, height), data)
                    except Exception:
                        continue
                else:
                    continue

                if resized:
                    img.thumbnail((200, 300))
                    ratio = min(200 / img.width, 300 / img.height)
                    new_size = (int(img.width * ratio), int(img.height * ratio))
                    img = img.resize(new_size, Image.Resampling.LANCZOS)
                    background = Image.new('RGB', (200, 300), 'gray')
                    offset = ((200 - new_size[0]) // 2,
                              (300 - new_size[1]) // 2)
                    background.paste(img, offset)
                    # Lazy: PIL.ImageTk imports tkinter, which headless
                    # CLI runs (resized=False) must not require.
                    from PIL import ImageTk
                    return ImageTk.PhotoImage(background)
                else:
                    buf = io.BytesIO()
                    img.save(buf, format='PNG')
                    return buf.getvalue()
    except Exception:
        pass
    return None


def get_pdf_cover_bytes(file_path):
    """Full-size PNG cover bytes for embedding in output files, or None.

    Re-opens the PDF on demand — callers usually hold only a PdfBook (which
    keeps no reader) or a bare path, and the cover is needed once per
    conversion.
    """
    try:
        reader = PdfReader(file_path)
    except Exception:
        return None
    return _extract_cover(reader, resized=False)


def _chapters_from_outline(reader, outline):
    """Build chapters from PDF outline entries.

    Page ranges are [start, end) where end is the smallest LATER entry's
    start page that is strictly greater than this entry's own start page
    (falling back to total_pages). A plain "next entry" lookup breaks on an
    out-of-order outline (e.g. a trailing bookmark pointing backward): the
    naive range would be empty (dropping the chapter) while the following
    chapter's range re-reads the same pages (duplicated audio). Scanning
    ALL later entries for the smallest qualifying start handles outlines
    that are out of order without reordering the chapters themselves.

    A child bookmark can land on the exact same page as its parent (e.g. a
    "1.1 Overview" subsection heading that's the first thing on "Chapter
    1"'s opening page). Bounding by "next later start page" alone gives
    both entries the identical [start, end) range — byte-identical text
    under two titles, not merely overlapping — so `ancestor_stack` tracks
    each entry's nearest shallower-level ancestor by (level, start_page)
    and such same-page children are dropped outright rather than emitted
    as a phantom duplicate chapter. Widening the parent's own range to
    swallow the child instead would turn the exact duplicate into
    overlapping duplication and defeat `find_duplicates`.
    """
    chapters = []
    total_pages = len(reader.pages)
    ancestor_stack = []  # (level, start_page) of currently-open ancestors

    for i, (level, title, start_page) in enumerate(outline):
        while ancestor_stack and ancestor_stack[-1][0] >= level:
            ancestor_stack.pop()
        parent_start = ancestor_stack[-1][1] if ancestor_stack else None
        ancestor_stack.append((level, start_page))

        if parent_start is not None and start_page == parent_start:
            continue

        end_page = total_pages
        # Scan ALL entries (not just the ones after this one in list order)
        # for the smallest start page that is strictly greater than this
        # chapter's own start. An out-of-order outline entry (e.g. a
        # trailing bookmark whose destination page is earlier than the
        # previous entry's) would otherwise make the naive "next in list"
        # lookup produce an empty range for one chapter while a later
        # chapter's range re-reads the same pages.
        for _level2, _title2, other_start in outline:
            if other_start > start_page and other_start < end_page:
                end_page = other_start

        text_parts = []
        for page_idx in range(start_page, min(end_page, total_pages)):
            page_text = reader.pages[page_idx].extract_text()
            if page_text and page_text.strip():
                text_parts.append(page_text)

        text = _clean_pdf_text('\n'.join(text_parts))
        if text.strip():
            # Index-suffixed so colliding bookmarks (same start page) never
            # share a TOC key — must match the naming _build_book_toc uses
            # for the same outline index, or the chapter tree's TOC-href
            # matching silently loses hierarchy for every PDF chapter.
            ch = PdfChapter(title, text, f'page_{start_page + 1}_{i}.pdf')
            chapters.append(ch)

    return chapters


def _chapters_from_pages(reader):
    """Fall back to one chapter per page group (~10 pages each)."""
    PAGES_PER_CHAPTER = 10
    chapters = []
    total = len(reader.pages)

    for start in range(0, total, PAGES_PER_CHAPTER):
        end = min(start + PAGES_PER_CHAPTER, total)
        text_parts = []
        for page_idx in range(start, end):
            page_text = reader.pages[page_idx].extract_text()
            if page_text and page_text.strip():
                text_parts.append(page_text)

        text = _clean_pdf_text('\n'.join(text_parts))
        if text.strip():
            title = f'Pages {start + 1}-{end}'
            ch = PdfChapter(title, text, f'pages_{start+1}_{end}.pdf')
            chapters.append(ch)

    return chapters


def _build_book_toc(outline):
    """Convert flat outline into ebooklib-style nested TOC structure.

    An entry nests under the PREVIOUS entry only when its level is strictly
    deeper; same-level entries stay siblings. (An earlier version compared
    against the container's level seeded at 0, so every top-level entry
    after the first — all at level 1 > 0 — was nested under the first,
    turning chapter one into a section holding the rest of the book.)

    Link hrefs are built with the same `page_{n}_{i}` naming
    `_chapters_from_outline` uses (index `i` is this entry's position in
    the same flat `outline` list both functions iterate, so the two stay in
    sync) — the chapter tree matches TOC hrefs against `chapter.file_name`
    exactly, and a same-page child that `_chapters_from_outline` drops as a
    duplicate simply matches no chapter here and is skipped, rather than
    every entry losing its match because the naming diverged.
    """
    from ebooklib.epub import Link

    result = []
    stack = []  # (level, children_list) of currently-open ancestors
    prev_level = None
    prev_container = result

    for i, (level, title, page_num) in enumerate(outline):
        link = Link(f'page_{page_num + 1}_{i}.pdf', title, '')
        if prev_level is not None and level > prev_level:
            # Deeper than the previous entry: promote it to a section.
            last = prev_container[-1]
            if isinstance(last, tuple):
                children = last[1]
            else:
                children = []
                prev_container[-1] = (last, children)
            stack.append((prev_level, children))
        else:
            # Sibling or shallower: close ancestors at or below this level.
            while stack and stack[-1][0] >= level:
                stack.pop()
        container = stack[-1][1] if stack else result
        container.append(link)
        prev_level = level
        prev_container = container

    return result
