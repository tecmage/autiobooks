"""PDF parsing support using pypdf (BSD licensed)."""

import io
import re
from PIL import Image, ImageTk
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


def get_pdf_book(file_path, resized=True):
    """Parse a PDF file and return (book, chapters, cover_image).

    Uses the PDF's outline (bookmarks) for chapter structure. Falls back
    to page groups if no outline is present.
    """
    reader = PdfReader(file_path)

    meta = reader.metadata or {}
    metadata = {
        'DC:title': getattr(meta, 'title', '') or '',
        'DC:creator': getattr(meta, 'author', '') or '',
        'DC:publisher': '',
        'DC:date': getattr(meta, 'creation_date_raw', '') or '',
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
                    return ImageTk.PhotoImage(background)
                else:
                    buf = io.BytesIO()
                    img.save(buf, format='PNG')
                    return buf.getvalue()
    except Exception:
        pass
    return None


def _chapters_from_outline(reader, outline):
    """Build chapters from PDF outline entries."""
    chapters = []
    total_pages = len(reader.pages)

    for i, (level, title, start_page) in enumerate(outline):
        if i + 1 < len(outline):
            end_page = outline[i + 1][2]
        else:
            end_page = total_pages

        text_parts = []
        for page_idx in range(start_page, min(end_page, total_pages)):
            page_text = reader.pages[page_idx].extract_text()
            if page_text and page_text.strip():
                text_parts.append(page_text)

        text = _clean_pdf_text('\n'.join(text_parts))
        if text.strip():
            ch = PdfChapter(title, text, f'page_{start_page + 1}.pdf')
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
    """Convert flat outline into ebooklib-style nested TOC structure."""
    from ebooklib.epub import Link

    result = []
    stack = [(0, result)]

    for level, title, page_num in outline:
        link = Link(f'page_{page_num + 1}.pdf', title, '')
        while len(stack) > 1 and stack[-1][0] >= level:
            stack.pop()
        parent_list = stack[-1][1]
        if level > stack[-1][0] and parent_list:
            last = parent_list[-1]
            if not isinstance(last, tuple):
                children = []
                parent_list[-1] = (last, children)
                stack.append((level, children))
                stack[-1][1].append(link)
            else:
                stack.append((level, last[1]))
                stack[-1][1].append(link)
        else:
            parent_list.append(link)

    return result
