import os

import pytest

ebooklib = pytest.importorskip('ebooklib')
pytest.importorskip('bs4')

from ebooklib import epub as ebooklib_epub  # noqa: E402

from autiobooks import epub_parser  # noqa: E402


def _make_epub(tmp_path, spine_reversed):
    """Write a 2-chapter epub whose manifest order is always ch1, ch2 but
    whose spine (reading order) is optionally reversed."""
    book = ebooklib_epub.EpubBook()
    book.set_identifier('test-spine-order')
    book.set_title('Spine Order Test')
    book.add_author('Nobody')

    c1 = ebooklib_epub.EpubHtml(title='One', file_name='ch1.xhtml')
    c1.content = ('<html><body><h1>One</h1>'
                  '<p>First chapter body text.</p></body></html>')
    c2 = ebooklib_epub.EpubHtml(title='Two', file_name='ch2.xhtml')
    c2.content = ('<html><body><h1>Two</h1>'
                  '<p>Second chapter body text.</p></body></html>')

    # Manifest order: c1 then c2 — regardless of spine order below.
    book.add_item(c1)
    book.add_item(c2)
    book.toc = (
        ebooklib_epub.Link('ch1.xhtml', 'One', 'one'),
        ebooklib_epub.Link('ch2.xhtml', 'Two', 'two'),
    )
    book.add_item(ebooklib_epub.EpubNcx())
    book.add_item(ebooklib_epub.EpubNav())
    book.spine = ['nav', c2, c1] if spine_reversed else ['nav', c1, c2]

    path = tmp_path / 'book.epub'
    ebooklib_epub.write_epub(str(path), book)
    return str(path)


def _chapter_order(path):
    _book, chapters, _cover = epub_parser.get_book(path, resized=False)
    texts = [c.extracted_text for c in chapters]
    i1 = next(i for i, t in enumerate(texts) if 'First chapter body' in t)
    i2 = next(i for i, t in enumerate(texts) if 'Second chapter body' in t)
    return i1, i2


class TestSpineOrder:
    def test_reversed_spine_wins_over_manifest(self, tmp_path):
        # Manifest says ch1, ch2 but the spine (reading order) says ch2, ch1
        # — audio chapters must follow the spine.
        path = _make_epub(tmp_path, spine_reversed=True)
        i1, i2 = _chapter_order(path)
        assert i2 < i1

    def test_normal_spine_keeps_order(self, tmp_path):
        path = _make_epub(tmp_path, spine_reversed=False)
        i1, i2 = _chapter_order(path)
        assert i1 < i2


class TestExtractTextFromHtml:
    def test_script_and_style_stripped(self):
        html = ('<html><body><p>Keep me.</p>'
                '<script>var x = 1;</script>'
                '<style>p { color: red }</style></body></html>')
        out = epub_parser.extract_text_from_html(html)
        assert 'Keep me.' in out
        assert 'var x' not in out
        assert 'color' not in out

    def test_br_becomes_line_break(self):
        html = '<html><body><p>line one<br/>line two</p></body></html>'
        out = epub_parser.extract_text_from_html(html)
        assert 'line one' in out and 'line two' in out
        assert 'oneline' not in out.replace(' ', '')

    def test_table_row_is_one_line(self):
        # A stat-table row must flatten to a SINGLE line (one TTS segment),
        # not one line per cell — else the TTS reads each cell as an isolated
        # utterance (the LitRPG status-block slowdown). Label cells ending in
        # ':' join to their value with a space; other boundaries get ', '.
        html = ('<html><body><table><tr>'
                '<td>Strength:</td><td>432</td>'
                '<td>Mana:</td><td>306</td>'
                '</tr></table></body></html>')
        out = epub_parser.extract_text_from_html(html)
        stat_lines = [ln for ln in out.split('\n') if 'Strength' in ln]
        assert stat_lines == ['Strength: 432, Mana: 306']

    def test_table_two_rows_two_lines(self):
        html = ('<html><body><table>'
                '<tr><td>Tier:</td><td>5</td></tr>'
                '<tr><td>Level:</td><td>64</td></tr>'
                '</table></body></html>')
        out = epub_parser.extract_text_from_html(html)
        lines = [ln for ln in out.split('\n') if ln.strip()]
        assert 'Tier: 5' in lines
        assert 'Level: 64' in lines

    def test_table_empty_cells_no_double_comma(self):
        html = ('<html><body><table><tr>'
                '<td>HP:</td><td>10</td><td></td><td></td>'
                '</tr></table></body></html>')
        out = epub_parser.extract_text_from_html(html)
        assert ', ,' not in out
        assert 'HP: 10' in out

    def test_nested_table_cells_not_doubled(self):
        # An outer row's cell already contains the inner table's rendered
        # text via get_text(); flattening the outer row AND the inner rows
        # used to speak every inner cell twice.
        html = ('<html><body><table><tr>'
                '<td>Outer label</td>'
                '<td><table><tr><td>InnerA</td><td>InnerB</td></tr></table></td>'
                '<td>OuterVal</td>'
                '</tr></table></body></html>')
        out = epub_parser.extract_text_from_html(html)
        assert out.count('InnerA') == 1
        assert out.count('InnerB') == 1
        assert 'Outer label' in out
        assert 'OuterVal' in out


class TestCoverImageSelection:
    def test_item_cover_wins_over_earlier_named_image(self):
        # A back-cover.png ITEM_IMAGE earlier in the manifest must not beat
        # the actually-flagged ITEM_COVER item that appears later.
        book = ebooklib_epub.EpubBook()
        back = ebooklib_epub.EpubImage(uid='back-img', file_name='images/back-cover.png')
        back.content = b'BACK-COVER-BYTES'
        real_cover = ebooklib_epub.EpubCover(uid='cover-img', file_name='images/cover.png')
        real_cover.content = b'REAL-COVER-BYTES'
        book.add_item(back)
        book.add_item(real_cover)

        result = epub_parser.get_cover_image(book, resized=False)
        assert result == b'REAL-COVER-BYTES'

    def test_name_heuristic_fallback_when_no_item_cover(self):
        book = ebooklib_epub.EpubBook()
        img = ebooklib_epub.EpubImage(uid='cover-named', file_name='images/cover.png')
        img.content = b'NAME-HEURISTIC-BYTES'
        book.add_item(img)

        result = epub_parser.get_cover_image(book, resized=False)
        assert result == b'NAME-HEURISTIC-BYTES'


class TestBuildTocMapSectionHref:
    def test_section_with_href_is_mapped(self):
        # ebooklib puts a parent navPoint's own document href on the
        # Section object, not just on its children — a Section-form "Part
        # I" page must still get a title-map entry (and be visible to
        # _is_excluded_chapter for a Section-form "Cover" label).
        section = ebooklib_epub.Section('Part One', href='part1.xhtml')
        child = ebooklib_epub.Link('ch1.xhtml', 'Chapter 1', 'ch1')
        toc = ((section, [child]),)
        result = epub_parser._build_toc_map(toc)
        assert result.get('part1.xhtml') == 'Part One'
        assert result.get('ch1.xhtml') == 'Chapter 1'

    def test_section_without_href_not_mapped(self):
        section = ebooklib_epub.Section('Part One', href='')
        child = ebooklib_epub.Link('ch1.xhtml', 'Chapter 1', 'ch1')
        toc = ((section, [child]),)
        result = epub_parser._build_toc_map(toc)
        assert 'part1.xhtml' not in result
        assert result.get('ch1.xhtml') == 'Chapter 1'

    def test_section_href_with_fragment_is_stripped(self):
        section = ebooklib_epub.Section('Cover', href='cover.xhtml#top')
        toc = ((section, []),)
        result = epub_parser._build_toc_map(toc)
        assert result.get('cover.xhtml') == 'Cover'


class TestBuildTocMapPercentEncodedHref:
    """Percent-encoding is the only spec-legal way to reference a filename
    containing a space, so a TOC href like 'Chapter%201.xhtml' must still
    match the unquoted manifest file_name 'Chapter 1.xhtml' — mirrors
    ebooklib's own unquote() of manifest hrefs (epub.py), which _parse_ncx
    and _parse_nav don't apply."""

    def test_link_href_with_percent_encoded_space_matches_filename(self):
        link = ebooklib_epub.Link('Chapter%201.xhtml', 'The Awakening', 'ch1')
        result = epub_parser._build_toc_map((link,))
        assert result.get('Chapter 1.xhtml') == 'The Awakening'
        assert 'Chapter%201.xhtml' not in result

    def test_section_href_with_percent_encoded_space_matches_filename(self):
        section = ebooklib_epub.Section('Cover', href='Cover%20Page.xhtml')
        toc = ((section, []),)
        result = epub_parser._build_toc_map(toc)
        assert result.get('Cover Page.xhtml') == 'Cover'

    def test_percent_encoded_fragment_is_still_stripped(self):
        link = ebooklib_epub.Link('Chapter%201.xhtml#top', 'One', 'ch1')
        result = epub_parser._build_toc_map((link,))
        assert result.get('Chapter 1.xhtml') == 'One'

    def test_end_to_end_get_chapter_titles_resolves_encoded_href(self, tmp_path):
        # Full repro from the audit: a real chapter whose manifest filename
        # contains a space, referenced from the TOC with the spec-legal
        # percent-encoded form.
        book = ebooklib_epub.EpubBook()
        book.set_identifier('percent-encoded-toc')
        book.set_title('Percent Encoded TOC')
        book.add_author('Nobody')

        c1 = ebooklib_epub.EpubHtml(title='One', file_name='Chapter 1.xhtml')
        c1.content = '<html><body><h1>Raw H1 Text</h1><p>Body one.</p></body></html>'
        c2 = ebooklib_epub.EpubHtml(title='Two', file_name='ch2.xhtml')
        c2.content = '<html><body><h1>Raw H2</h1><p>Body two.</p></body></html>'

        book.add_item(c1)
        book.add_item(c2)
        book.toc = (
            ebooklib_epub.Link('Chapter%201.xhtml', 'The Awakening', 'ch1'),
            ebooklib_epub.Link('ch2.xhtml', 'The Return', 'ch2'),
        )
        book.add_item(ebooklib_epub.EpubNcx())
        book.add_item(ebooklib_epub.EpubNav())
        book.spine = ['nav', c1, c2]

        path = tmp_path / 'book.epub'
        ebooklib_epub.write_epub(str(path), book)

        result_book, chapters, _cover = epub_parser.get_book(str(path), resized=False)
        titles = epub_parser.get_chapter_titles(result_book, chapters)
        assert 'The Awakening' in titles
        assert 'The Return' in titles


class TestIsFootnoteRefMultiToken:
    """epub:type is a space-separated token list per the EPUB 3 spec; lxml
    preserves it as one raw attribute string, so an == comparison misses
    every multi-token case."""

    def _tag(self, html):
        from bs4 import BeautifulSoup
        return BeautifulSoup(html, features='lxml').find('a')

    def test_single_token_noteref_detected(self):
        tag = self._tag('<a epub:type="noteref" href="#n2">2</a>')
        assert epub_parser._is_footnote_ref(tag) is True

    def test_multi_token_noteref_backlink_detected(self):
        tag = self._tag('<a epub:type="noteref backlink" href="#n2">2</a>')
        assert epub_parser._is_footnote_ref(tag) is True

    def test_multi_token_reversed_order_detected(self):
        tag = self._tag('<a epub:type="backlink noteref" href="#n2">2</a>')
        assert epub_parser._is_footnote_ref(tag) is True

    def test_bare_backlink_not_treated_as_noteref(self):
        # backlink (link back to the text from the note body) and footnote
        # (the note body itself) are legitimate content and must not be
        # swallowed by the noteref check.
        tag = self._tag('<a epub:type="backlink" href="#n2">back</a>')
        assert epub_parser._is_footnote_ref(tag) is False

    def test_multi_token_noteref_not_glued_to_preceding_word(self):
        html = ('<p>Word<a epub:type="noteref backlink" href="#n2">2</a>'
                ' more.</p>')
        out = epub_parser.extract_text_from_html(html)
        assert out == 'Word more.'


class TestDegenerateNcxToc:
    def test_bare_link_toc_normalized_to_list(self, monkeypatch):
        # A pure EPUB2 whose NCX navMap yields zero DAISY-namespace navPoints
        # (e.g. an empty <navMap/>) makes ebooklib's _get_children return a
        # bare Link instead of a list — truthy but not iterable. Stand in
        # for that by handing get_book() a book whose .toc is a Link.
        book = ebooklib_epub.EpubBook()
        book.set_identifier('degenerate-ncx')
        book.set_title('Degenerate NCX')
        book.add_author('Nobody')
        c1 = ebooklib_epub.EpubHtml(title='One', file_name='ch1.xhtml')
        c1.content = '<html><body><h1>One</h1><p>Chapter body text.</p></body></html>'
        book.add_item(c1)
        book.add_item(ebooklib_epub.EpubNcx())
        book.spine = [c1]
        book.toc = ebooklib_epub.Link('ch1.xhtml', 'One', 'one')
        assert bool(book.toc) is True
        with pytest.raises(TypeError):
            list(book.toc)

        monkeypatch.setattr(epub_parser.epub, 'read_epub', lambda *a, **k: book)

        result_book, chapters, _cover = epub_parser.get_book('unused.epub', resized=False)
        assert isinstance(result_book.toc, (list, tuple))
        assert len(chapters) == 1

        # get_chapter_titles must also see the normalized list, not raise.
        titles = epub_parser.get_chapter_titles(result_book, chapters)
        assert titles == ['One']


class TestGetBookCachedKey:
    def test_same_mtime_different_size_reparse(self, tmp_path, monkeypatch):
        # A file replaced with a preserved mtime (cp -p, some sync tools)
        # must still bust the cache — size is included in the key.
        path = tmp_path / 'book.epub'
        path.write_bytes(b'placeholder')

        calls = []

        def fake_get_book(file_path, resized):
            calls.append(file_path)
            return (f'book-{len(calls)}', [], None)

        monkeypatch.setattr(epub_parser, 'get_book', fake_get_book)
        epub_parser.clear_chapter_cache()

        fixed_mtime = 1_700_000_000.0

        class FakeStat:
            def __init__(self, size):
                self.st_mtime = fixed_mtime
                self.st_size = size

        monkeypatch.setattr(os, 'stat', lambda p: FakeStat(100))
        first = epub_parser.get_book_cached(str(path), resized=False)
        assert first[0] == 'book-1'
        # Same mtime, same size -> cache hit, no re-parse.
        second = epub_parser.get_book_cached(str(path), resized=False)
        assert second[0] == 'book-1'
        assert len(calls) == 1

        # Same mtime, DIFFERENT size -> must re-parse.
        monkeypatch.setattr(os, 'stat', lambda p: FakeStat(200))
        third = epub_parser.get_book_cached(str(path), resized=False)
        assert third[0] == 'book-2'
        assert len(calls) == 2

        epub_parser.clear_chapter_cache()


def _make_split_epub(tmp_path, toc_refs_split_001=False, three_way=False):
    """Calibre-split shape: ch1 split into a 2-word `_split_000` stub and a
    `_split_001` body file; ch2 unsplit. The TOC references only the
    `_split_000` stub unless toc_refs_split_001 is set (the page-break kind
    of split, where each piece is a real chapter start)."""
    book = ebooklib_epub.EpubBook()
    book.set_identifier('test-calibre-split')
    book.set_title('Split Test')
    book.add_author('Nobody')

    stub = ebooklib_epub.EpubHtml(file_name='text/part0004_split_000.html')
    stub.content = '<html><body><p>Chapter 1</p></body></html>'
    body = ebooklib_epub.EpubHtml(file_name='text/part0004_split_001.html')
    body.content = ('<html><body><h3>Limburger Hollow</h3>'
                    '<p>Split continuation body text.</p></body></html>')
    ch2 = ebooklib_epub.EpubHtml(file_name='text/part0005.html')
    ch2.content = '<html><body><h3>Two</h3><p>Second chapter body.</p></body></html>'

    items = [stub, body]
    if three_way:
        tail = ebooklib_epub.EpubHtml(file_name='text/part0004_split_002.html')
        tail.content = '<html><body><p>Third piece of chapter one.</p></body></html>'
        items.append(tail)
    items.append(ch2)
    for it in items:
        book.add_item(it)

    toc = [ebooklib_epub.Link('text/part0004_split_000.html', '1. Limburger Hollow', 'c1')]
    if toc_refs_split_001:
        toc.append(ebooklib_epub.Link('text/part0004_split_001.html', '2. Second Piece', 'c1b'))
    toc.append(ebooklib_epub.Link('text/part0005.html', 'Two', 'c2'))
    book.toc = toc
    book.add_item(ebooklib_epub.EpubNcx())
    book.add_item(ebooklib_epub.EpubNav())
    book.spine = ['nav'] + items

    path = tmp_path / 'split.epub'
    ebooklib_epub.write_epub(str(path), book)
    return str(path)


class TestCalibreSplitMerge:
    def test_unreferenced_continuation_merges_into_predecessor(self, tmp_path):
        path = _make_split_epub(tmp_path)
        _book, chapters, _cover = epub_parser.get_book(path, resized=False)
        names = [c.file_name for c in chapters]
        assert 'text/part0004_split_001.html' not in names
        merged = next(c for c in chapters
                      if c.file_name == 'text/part0004_split_000.html')
        # Stub text first, continuation after — reading order inside the merge.
        assert merged.extracted_text.index('Chapter 1') \
            < merged.extracted_text.index('Split continuation body')

    def test_merged_chapter_keeps_toc_title(self, tmp_path):
        path = _make_split_epub(tmp_path)
        book, chapters, _cover = epub_parser.get_book(path, resized=False)
        titles = epub_parser.get_chapter_titles(book, chapters)
        by_name = dict(zip((c.file_name for c in chapters), titles))
        assert by_name['text/part0004_split_000.html'] == '1. Limburger Hollow'

    def test_toc_referenced_split_stays_separate(self, tmp_path):
        # Page-break-style split: the TOC points at BOTH pieces — each is a
        # real chapter start and must NOT merge.
        path = _make_split_epub(tmp_path, toc_refs_split_001=True)
        _book, chapters, _cover = epub_parser.get_book(path, resized=False)
        names = [c.file_name for c in chapters]
        assert 'text/part0004_split_000.html' in names
        assert 'text/part0004_split_001.html' in names

    def test_three_way_split_chain_merges(self, tmp_path):
        path = _make_split_epub(tmp_path, three_way=True)
        _book, chapters, _cover = epub_parser.get_book(path, resized=False)
        names = [c.file_name for c in chapters]
        assert 'text/part0004_split_001.html' not in names
        assert 'text/part0004_split_002.html' not in names
        merged = next(c for c in chapters
                      if c.file_name == 'text/part0004_split_000.html')
        text = merged.extracted_text
        assert text.index('Chapter 1') < text.index('Split continuation body') \
            < text.index('Third piece')

    def test_non_split_unreferenced_file_not_merged(self, tmp_path):
        path = _make_split_epub(tmp_path)
        _book, chapters, _cover = epub_parser.get_book(path, resized=False)
        # part0005.html follows the split group but isn't a split sibling —
        # it stays its own chapter.
        assert any(c.file_name == 'text/part0005.html' for c in chapters)


class TestSplitGroupKey:
    def test_split_names_share_key(self):
        k0 = epub_parser._split_group_key('text/part0004_split_000.html')
        k1 = epub_parser._split_group_key('text/part0004_split_001.html')
        assert k0 is not None and k0 == k1

    def test_different_stems_differ(self):
        assert epub_parser._split_group_key('text/part0004_split_001.html') \
            != epub_parser._split_group_key('text/part0005_split_000.html')

    def test_different_dirs_differ(self):
        assert epub_parser._split_group_key('a/part0004_split_000.html') \
            != epub_parser._split_group_key('b/part0004_split_000.html')

    def test_non_split_name_is_none(self):
        assert epub_parser._split_group_key('text/part0005.html') is None
        assert epub_parser._split_group_key('chapter_split.html') is None
