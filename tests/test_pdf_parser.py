import io

import pytest
from PIL import Image

from autiobooks import pdf_parser
from autiobooks.pdf_parser import _build_book_toc, _chapters_from_outline


def _shape(toc):
    """Reduce a TOC to nested title strings for easy comparison."""
    out = []
    for item in toc:
        if isinstance(item, tuple):
            out.append((item[0].title, _shape(item[1])))
        else:
            out.append(item.title)
    return out


class TestBuildBookToc:
    """Flat sibling outlines must stay flat — an earlier version nested
    every top-level entry after the first under the first entry."""

    def test_flat_outline_stays_flat(self):
        toc = _build_book_toc([(1, 'Ch1', 0), (1, 'Ch2', 10), (1, 'Ch3', 20)])
        assert _shape(toc) == ['Ch1', 'Ch2', 'Ch3']

    def test_two_parts_with_children(self):
        toc = _build_book_toc([
            (1, 'Part1', 0), (2, 'Ch1', 1), (2, 'Ch2', 10),
            (1, 'Part2', 20), (2, 'Ch3', 21),
        ])
        assert _shape(toc) == [
            ('Part1', ['Ch1', 'Ch2']),
            ('Part2', ['Ch3']),
        ]

    def test_three_levels(self):
        toc = _build_book_toc([
            (1, 'A', 0), (2, 'B', 1), (3, 'C', 2), (1, 'D', 3),
        ])
        assert _shape(toc) == [('A', [('B', ['C'])]), 'D']

    def test_skipped_level_does_not_crash(self):
        # Malformed outline jumping 1 -> 3 -> 2: deeper entries still nest
        # under the previous entry; the level-2 entry rejoins as a sibling
        # inside the open level-1 section.
        toc = _build_book_toc([
            (1, 'A', 0), (3, 'B', 1), (2, 'C', 2), (1, 'D', 3),
        ])
        assert _shape(toc) == [('A', ['B', 'C']), 'D']

    def test_empty_outline(self):
        assert _build_book_toc([]) == []

    def test_single_entry(self):
        assert _shape(_build_book_toc([(1, 'Only', 0)])) == ['Only']


class _FakePage:
    def __init__(self, text):
        self._text = text

    def extract_text(self):
        return self._text


class _FakeReader:
    """Minimal stand-in for pypdf.PdfReader exposing only what
    _chapters_from_outline touches: `.pages` (indexable, len()able) with
    `.extract_text()`."""

    def __init__(self, num_pages):
        self.pages = [_FakePage(f'page{i} text') for i in range(num_pages)]


class TestChaptersFromOutline:
    def test_out_of_order_entry_does_not_drop_or_duplicate_pages(self):
        # A@0, B@6, C@3 (page-order is really A, C, B) on a 10-page mock.
        # The naive "end = next entry in list order" makes B's range
        # [6, 6) (empty -> dropped) and C's range [3, 10) (re-reads B's
        # pages 6-9, duplicated). Fixed: every chapter's end is the
        # smallest OTHER entry's start that is strictly greater than its
        # own start, so ranges tile the book with no gaps or overlaps.
        reader = _FakeReader(10)
        outline = [(1, 'A', 0), (1, 'B', 6), (1, 'C', 3)]

        chapters = _chapters_from_outline(reader, outline)

        assert [c.display_title for c in chapters] == ['A', 'B', 'C']
        by_title = {c.display_title: c.extracted_text for c in chapters}

        # B must not be dropped.
        assert by_title['B'].strip() != ''

        # Every page appears in exactly one chapter's text — no page is
        # silently dropped, and none is synthesized (read) twice.
        for page_num in range(10):
            marker = f'page{page_num} text'
            hits = [t for t in by_title.values() if marker in t]
            assert len(hits) == 1, (
                f'page {page_num} appeared in {len(hits)} chapters, want 1')

    def test_forward_order_outline_unaffected(self):
        reader = _FakeReader(9)
        outline = [(1, 'A', 0), (1, 'B', 3), (1, 'C', 6)]
        chapters = _chapters_from_outline(reader, outline)
        assert [c.display_title for c in chapters] == ['A', 'B', 'C']
        assert 'page0 text' in chapters[0].extracted_text
        assert 'page3 text' in chapters[1].extracted_text
        assert 'page6 text' in chapters[2].extracted_text

    def test_file_names_are_index_disambiguated(self):
        reader = _FakeReader(9)
        outline = [(1, 'A', 0), (1, 'B', 3), (1, 'C', 6)]
        chapters = _chapters_from_outline(reader, outline)
        assert [c.file_name for c in chapters] == [
            'page_1_0.pdf', 'page_4_1.pdf', 'page_7_2.pdf',
        ]


class TestSamePageParentChildCollapse:
    """A parent bookmark and its first child landing on the same page (a
    subsection heading that's the first thing on the parent chapter's
    opening page — a typical technical-PDF layout) must not fabricate a
    duplicate chapter: bounding by 'next later start page' alone gives both
    entries the identical [start, end) range, producing byte-identical
    text under two titles."""

    def test_same_page_child_dropped_not_duplicated(self):
        # Audit repro: outline [(1,'Chapter 1',4), (2,'1.1 Overview',4),
        # (2,'1.2 Details',8), (1,'Chapter 2',19)] on a 25-page mock.
        reader = _FakeReader(25)
        outline = [
            (1, 'Chapter 1', 4),
            (2, '1.1 Overview', 4),
            (2, '1.2 Details', 8),
            (1, 'Chapter 2', 19),
        ]
        chapters = _chapters_from_outline(reader, outline)

        # The same-page child must not appear as its own chapter.
        titles = [c.display_title for c in chapters]
        assert '1.1 Overview' not in titles
        assert titles == ['Chapter 1', '1.2 Details', 'Chapter 2']

        by_title = {c.display_title: c for c in chapters}
        # Chapter 1 is bounded by the NEXT distinct-page entry (1.2
        # Details, page 8) — not widened to swallow it.
        assert 'page4 text' in by_title['Chapter 1'].extracted_text
        assert 'page7 text' in by_title['Chapter 1'].extracted_text
        assert 'page8 text' not in by_title['Chapter 1'].extracted_text
        assert 'page8 text' in by_title['1.2 Details'].extracted_text
        assert 'page18 text' in by_title['1.2 Details'].extracted_text
        assert 'page19 text' not in by_title['1.2 Details'].extracted_text
        assert 'page19 text' in by_title['Chapter 2'].extracted_text

        # No page is voiced twice.
        for page_num in range(25):
            marker = f'page{page_num} text'
            hits = [c for c in chapters if marker in c.extracted_text]
            assert len(hits) <= 1, (
                f'page {page_num} appeared in {len(hits)} chapters, want <=1')

        # file_names stay unique even though the dropped entry shared a
        # start page with its parent.
        assert len({c.file_name for c in chapters}) == len(chapters)

    def test_same_page_grandchild_also_dropped(self):
        # A 3-level chain all opening on the same page: only the outermost
        # (first-seen) entry survives.
        reader = _FakeReader(10)
        outline = [
            (1, 'Part', 0),
            (2, 'Chapter', 0),
            (3, 'Section', 0),
            (1, 'Next Part', 5),
        ]
        chapters = _chapters_from_outline(reader, outline)
        titles = [c.display_title for c in chapters]
        assert titles == ['Part', 'Next Part']

    def test_distinct_pages_all_kept(self):
        # Sanity: when no two entries share a start page, nothing is
        # dropped — this is the existing two-level nested case.
        reader = _FakeReader(20)
        outline = [
            (1, 'Part1', 0), (2, 'Ch1', 1), (2, 'Ch2', 10),
            (1, 'Part2', 15),
        ]
        chapters = _chapters_from_outline(reader, outline)
        titles = [c.display_title for c in chapters]
        assert titles == ['Part1', 'Ch1', 'Ch2', 'Part2']


class TestBuildBookTocFileNameParity:
    """_build_book_toc's Link hrefs must use the same page_{n}_{i} naming
    _chapters_from_outline gives its chapters (same outline, same index) —
    otherwise the chapter tree's TOC-href matching silently loses every
    PDF chapter's hierarchy, not just the collapsed duplicates."""

    def test_link_hrefs_match_chapter_file_names(self):
        reader = _FakeReader(25)
        outline = [
            (1, 'Chapter 1', 4),
            (2, '1.1 Overview', 4),
            (2, '1.2 Details', 8),
            (1, 'Chapter 2', 19),
        ]
        chapters = _chapters_from_outline(reader, outline)
        toc = _build_book_toc(outline)

        toc_hrefs = set()

        def collect(entries):
            for entry in entries:
                if isinstance(entry, tuple):
                    toc_hrefs.add(entry[0].href)
                    collect(entry[1])
                else:
                    toc_hrefs.add(entry.href)

        collect(toc)

        chapter_names = {c.file_name for c in chapters}
        # Every surviving chapter's file_name must be reachable via some
        # TOC Link href (the collapsed duplicate's href is present in the
        # TOC but simply matches no chapter, which is fine).
        assert chapter_names <= toc_hrefs


class _FakeFilterArray(list):
    """Stand-in for pypdf's ArrayObject: a list subclass whose elements
    stringify to the PDF name, e.g. str(NameObject('/DCTDecode')) ==
    '/DCTDecode'."""
    pass


class TestExtractCoverFilterArray:
    """`/Filter` may legally be an array (single filter `[/DCTDecode]` or a
    decode chain `[/FlateDecode /DCTDecode]`); pypdf applies filters in
    order so the LAST element determines the final byte encoding."""

    def _make_reader_with_image(self, filt, width=200, height=300):
        img = Image.new('RGB', (width, height), 'white')
        buf = io.BytesIO()
        img.save(buf, format='JPEG')
        jpeg_bytes = buf.getvalue()

        class FakeStreamObj:
            def get_object(self):
                return self

            def get(self, key, default=None):
                return {
                    '/Subtype': '/Image',
                    '/Width': width,
                    '/Height': height,
                    '/ColorSpace': '/DeviceRGB',
                    '/BitsPerComponent': 8,
                    '/Filter': filt,
                }.get(key, default)

            def get_data(self):
                return jpeg_bytes

        class FakeXObject(dict):
            pass

        x_object = FakeXObject({'/Im0': FakeStreamObj()})

        class FakePageWithImage:
            def get(self, key, default=None):
                if key == '/Resources':
                    return {'/XObject': x_object}
                return default

        class FakeReaderWithCover:
            def __init__(self):
                self.pages = [FakePageWithImage()]

        return FakeReaderWithCover()

    def test_single_element_filter_array_matches_dct_decode(self):
        reader = self._make_reader_with_image(_FakeFilterArray(['/DCTDecode']))
        cover_bytes = pdf_parser._extract_cover(reader, resized=False)
        assert cover_bytes is not None

    def test_filter_chain_uses_last_element(self):
        reader = self._make_reader_with_image(
            _FakeFilterArray(['/FlateDecode', '/DCTDecode']))
        cover_bytes = pdf_parser._extract_cover(reader, resized=False)
        assert cover_bytes is not None

    def test_plain_name_filter_still_works(self):
        reader = self._make_reader_with_image('/DCTDecode')
        cover_bytes = pdf_parser._extract_cover(reader, resized=False)
        assert cover_bytes is not None


class _FakeEncryptedReader:
    """Stand-in for a pypdf PdfReader with is_encrypted=True. `decrypt_ok`
    controls whether the (simulated) empty-password decrypt leaves the
    pages genuinely accessible."""

    def __init__(self, decrypt_ok):
        self.is_encrypted = True
        self._decrypt_ok = decrypt_ok
        self.metadata = None

    def decrypt(self, password):
        return None

    @property
    def pages(self):
        if not self._decrypt_ok:
            raise Exception('cannot read: no user password provided')
        return [_FakePage('owner-password page text')]


class TestOwnerPasswordDecrypt:
    def test_owner_password_only_pdf_loads(self):
        reader = _FakeEncryptedReader(decrypt_ok=True)
        # Must not raise.
        pdf_parser._decrypt_or_raise(reader)

    def test_genuinely_locked_pdf_raises_friendly_message(self):
        reader = _FakeEncryptedReader(decrypt_ok=False)
        with pytest.raises(ValueError, match='password-protected'):
            pdf_parser._decrypt_or_raise(reader)
