"""Hierarchical chapter selector with ttk.Treeview, checkboxes, and preview."""

import tkinter as tk
from tkinter import ttk
from PIL import Image, ImageDraw, ImageTk

from .epub_parser import _extract_heading, _toc_href_to_filename
from .selection import find_duplicates


class ChapterTreeView:
    """Treeview-based chapter selector replacing the flat checkbox list.

    Provides hierarchical display from EPUB TOC, image-based checkboxes with
    parent-child propagation, a content preview panel, book info display,
    auto-select, and duplicate detection.
    """

    _checkbox_images = {}
    _checkbox_images_master = None

    @classmethod
    def _init_checkbox_images(cls, master):
        # ImageTk.PhotoImage objects are bound to the Tk interpreter that
        # created them. A second start_gui() in the same process creates a
        # new Tk root/interpreter — reusing the old cache raises TclError
        # ("image ... doesn't exist") the first time it's used. Identify the
        # interpreter by its Tcl interpreter object (`widget.tk`), not by
        # widget path string — two different roots can both be named ".".
        interp = master.tk
        if cls._checkbox_images and cls._checkbox_images_master is interp:
            return
        cls._checkbox_images = {}
        for name in ('unchecked', 'checked', 'half'):
            img = Image.new('RGBA', (16, 16), (0, 0, 0, 0))
            draw = ImageDraw.Draw(img)
            draw.rectangle([1, 1, 14, 14], outline='#666666')
            if name == 'checked':
                draw.line([3, 8, 6, 11, 13, 3], fill='#2e7d32', width=2)
            elif name == 'half':
                draw.line([3, 8, 13, 8], fill='#666666', width=2)
            cls._checkbox_images[name] = ImageTk.PhotoImage(img, master=master)
        cls._checkbox_images_master = interp

    def __init__(self, parent, book, chapters, metadata, *,
                 on_selection_change=None, auto_select=True,
                 mark_duplicates=True, on_play_preview=None):
        self._init_checkbox_images(parent)
        self.parent = parent
        self.book = book
        self.chapters = chapters
        self.metadata = metadata
        self.on_selection_change = on_selection_change
        self.on_play_preview = on_play_preview

        self._item_to_chapter = {}
        self._chapter_to_item = {}
        self._selected = set()
        self._selected_preview_chapter = None
        self.play_button = None

        self._build_ui()
        self._build_tree()
        if mark_duplicates:
            self._detect_duplicates()
        if auto_select:
            self._auto_select()

    def _build_ui(self):
        self.frame = ttk.Frame(self.parent)
        self.frame.pack(fill=tk.BOTH, expand=True)

        paned = ttk.PanedWindow(self.frame, orient=tk.HORIZONTAL)
        paned.pack(fill=tk.BOTH, expand=True)

        # Left: tree
        left = ttk.Frame(paned)
        paned.add(left, weight=2)

        # Toolbar
        toolbar = ttk.Frame(left)
        toolbar.pack(fill=tk.X, pady=(0, 2))
        ttk.Button(toolbar, text='Expand All',
                   command=self.expand_all).pack(side=tk.LEFT, padx=2)
        ttk.Button(toolbar, text='Collapse All',
                   command=self.collapse_all).pack(side=tk.LEFT, padx=2)
        if self.on_play_preview is not None:
            self.play_button = ttk.Button(
                toolbar, text='▶️',
                command=self._on_play_preview_click, state='disabled')
            self.play_button.pack(side=tk.LEFT, padx=2)

        tree_frame = ttk.Frame(left)
        tree_frame.pack(fill=tk.BOTH, expand=True)

        self.tree = ttk.Treeview(tree_frame, show='tree', selectmode='browse')
        vsb = ttk.Scrollbar(tree_frame, orient='vertical',
                            command=self.tree.yview)
        self.tree.configure(yscrollcommand=vsb.set)
        vsb.pack(side=tk.RIGHT, fill=tk.Y)
        self.tree.pack(side=tk.LEFT, fill=tk.BOTH, expand=True)

        self.tree.bind('<Button-1>', self._on_tree_click)
        self.tree.bind('<<TreeviewSelect>>', self._on_tree_select)

        # Right: preview
        right = ttk.Frame(paned)
        paned.add(right, weight=3)

        self.preview_info_label = ttk.Label(right, text='', anchor='w')
        self.preview_info_label.pack(fill=tk.X, padx=4, pady=(4, 0))

        self.preview_text = tk.Text(right, wrap=tk.WORD, state=tk.DISABLED,
                                    font=('Arial', 11))
        preview_vsb = ttk.Scrollbar(right, orient='vertical',
                                    command=self.preview_text.yview)
        self.preview_text.configure(yscrollcommand=preview_vsb.set)
        preview_vsb.pack(side=tk.RIGHT, fill=tk.Y)
        self.preview_text.pack(fill=tk.BOTH, expand=True, padx=(4, 0),
                               pady=4)

        self._show_book_info()

    # ── Tree construction ──────────────────────────────────────────────

    def _build_tree(self):
        toc = getattr(self.book, 'toc', None)
        if isinstance(toc, (list, tuple)) and toc:
            href_to_chapters = self._build_href_map()
            self._process_toc(toc, '', href_to_chapters)
            # Insert any chapters not referenced by the TOC at their SPINE
            # position — as a sibling right after the nearest preceding
            # chapter that got a node — not appended at the bottom. Dumping
            # them at the end made the tree read out of order on books whose
            # TOC skips real content documents, even though conversion
            # (spine-sorted) was always correct. Titled by first heading
            # when one exists; file name otherwise.
            prev_iid = None
            for ch in self.chapters:
                iid = self._chapter_to_item.get(id(ch))
                if iid is not None:
                    prev_iid = iid
                    continue
                if not ch.extracted_text.strip():
                    continue
                if prev_iid is not None:
                    parent = self.tree.parent(prev_iid)
                    index = self.tree.index(prev_iid) + 1
                else:
                    parent, index = '', 0
                prev_iid = self._insert_chapter_node(
                    ch, _extract_heading(ch), parent, index=index)
        else:
            self._build_flat_tree()

    def _build_href_map(self):
        result = {}
        for ch in self.chapters:
            fname = ch.file_name
            result.setdefault(fname, []).append(ch)
            base = fname.split('#')[0]
            if base != fname:
                result.setdefault(base, []).append(ch)
        return result

    def _process_toc(self, entries, parent_iid, href_map):
        for entry in entries:
            if isinstance(entry, tuple):
                section, children = entry
                title = getattr(section, 'title', None) or 'Section'
                iid = self.tree.insert(
                    parent_iid, 'end', text=title,
                    image=self._checkbox_images['unchecked'],
                    values=('section', '', 0, 'False', 'False'))
                # ebooklib puts the parent navPoint's own document href on
                # the Section itself, not only on its children. A Section
                # with a truthy href is a real spine document (e.g. a
                # "Part I" page) — match it against the chapter map the
                # same way a Link href is matched, and insert it as the
                # section's own first chapter node, so it isn't lost to
                # the "leftover chapters" pass at the end of _build_tree.
                section_href = getattr(section, 'href', '') or ''
                if section_href:
                    self._match_and_insert_href(
                        section_href, title, iid, href_map)
                self._process_toc(children, iid, href_map)
                self._update_parent_state(iid)
            else:
                href = getattr(entry, 'href', '') or ''
                toc_title = getattr(entry, 'title', None)
                self._match_and_insert_href(
                    href, toc_title, parent_iid, href_map)

    def _match_and_insert_href(self, href, toc_title, parent_iid, href_map):
        """Match a TOC href (from a Link or a chapter-bearing Section)
        against the chapter map and insert the chapter node under
        parent_iid. Shared by Link entries and Section.href entries."""
        # Normalized like epub_parser._build_toc_map: TOC hrefs may be
        # percent-encoded (spaces, accents) or backslashed (ebooklib's
        # write_epub on Windows) while chapter.file_name (and the href_map
        # keyed on it) is not.
        fname = _toc_href_to_filename(href)
        matched = href_map.get(fname, [])
        if matched:
            ch = matched[0]
            if id(ch) not in self._chapter_to_item:
                self._insert_chapter_node(ch, toc_title, parent_iid)
            return
        # Fallback: TOC href and chapter file_name may have different path
        # prefixes (OEBPS/, text/, etc.). Match on the bare basename, but
        # only with == — substring matching falsely conflates ch01.xhtml
        # with ch010.xhtml.
        target_base = fname.rsplit('/', 1)[-1] if fname else ''
        if target_base:
            for ch in self.chapters:
                if id(ch) in self._chapter_to_item:
                    continue
                ch_base = ch.file_name.rsplit('/', 1)[-1]
                if ch_base == target_base:
                    self._insert_chapter_node(ch, toc_title, parent_iid)
                    break

    def _build_flat_tree(self):
        for ch in self.chapters:
            if ch.extracted_text.strip():
                self._insert_chapter_node(ch, None, '')

    def _insert_chapter_node(self, chapter, toc_title, parent_iid,
                             index='end'):
        display_title = (getattr(chapter, 'display_title', None)
                         or toc_title
                         or chapter.file_name)
        word_count = len(chapter.extracted_text.split())
        is_empty = not chapter.extracted_text.strip()

        iid = self.tree.insert(
            parent_iid, index,
            text=display_title,
            image=self._checkbox_images['unchecked'],
            values=('chapter', str(id(chapter)), str(word_count),
                    str(is_empty), 'False'))
        self._item_to_chapter[iid] = chapter
        self._chapter_to_item[id(chapter)] = iid
        return iid

    # ── Duplicate detection ────────────────────────────────────────────

    def _detect_duplicates(self):
        iids = list(self._item_to_chapter.keys())
        chapter_list = [self._item_to_chapter[iid] for iid in iids]
        for idx in find_duplicates(chapter_list):
            iid = iids[idx]
            title = self.tree.item(iid, 'text')
            self.tree.item(iid, text=f'{title} (Duplicate)')
            vals = list(self.tree.item(iid, 'values'))
            if len(vals) > 4:
                vals[4] = 'True'
                self.tree.item(iid, values=vals)

    # ── Auto-select ────────────────────────────────────────────────────

    def _auto_select(self):
        for iid in self._all_items():
            vals = self.tree.item(iid, 'values')
            if not vals or vals[0] != 'chapter':
                continue
            word_count = int(vals[2]) if len(vals) > 2 else 0
            is_empty = vals[3] == 'True' if len(vals) > 3 else False
            is_dup = vals[4] == 'True' if len(vals) > 4 else False
            if word_count > 0 and not is_empty and not is_dup:
                self.tree.item(iid, image=self._checkbox_images['checked'])
                self._selected.add(iid)
        # Fix parent states bottom-up
        self._refresh_all_parents()
        if self.on_selection_change:
            self.on_selection_change()

    # ── Click / selection ──────────────────────────────────────────────

    def _on_tree_click(self, event):
        region = self.tree.identify_region(event.x, event.y)
        iid = self.tree.identify_row(event.y)
        if not iid:
            return
        if region == 'image':
            self._toggle_item(iid)
            return 'break'
        if region == 'tree':
            element = self.tree.identify_element(event.x, event.y)
            if 'image' in element:
                self._toggle_item(iid)
                return 'break'

    def _on_tree_select(self, _event):
        sel = self.tree.selection()
        if sel:
            self._update_preview(sel[0])

    def _toggle_item(self, iid):
        vals = self.tree.item(iid, 'values')
        is_section = not (vals and vals[0] == 'chapter')
        if (is_section and not self._has_checked_children(iid)
                and not self._descendant_chapter_states(iid)):
            # A section with no chapter descendants at all always computes
            # is_checked=False, so it would re-check on every click and
            # never actually select anything. Make it a no-op instead of a
            # permanently-stuck checkbox.
            return
        is_checked = iid in self._selected or self._has_checked_children(iid)
        new_checked = not is_checked
        self._set_item_state(iid, new_checked)
        self._propagate_to_children(iid, new_checked)
        self._refresh_ancestors(iid)
        if self.on_selection_change:
            self.on_selection_change()

    def _set_item_state(self, iid, checked):
        vals = self.tree.item(iid, 'values')
        is_chapter = vals and vals[0] == 'chapter'
        if checked:
            self.tree.item(iid, image=self._checkbox_images['checked'])
            if is_chapter:
                self._selected.add(iid)
        else:
            self.tree.item(iid, image=self._checkbox_images['unchecked'])
            self._selected.discard(iid)

    def _propagate_to_children(self, iid, checked):
        for child in self.tree.get_children(iid):
            self._set_item_state(child, checked)
            self._propagate_to_children(child, checked)

    def _has_checked_children(self, iid):
        for child in self.tree.get_children(iid):
            if child in self._selected:
                return True
            if self._has_checked_children(child):
                return True
        return False

    def _refresh_ancestors(self, iid):
        parent = self.tree.parent(iid)
        while parent:
            self._update_parent_state(parent)
            parent = self.tree.parent(parent)

    def _descendant_chapter_states(self, iid):
        """Checked-state of every chapter under iid (recursing sections)."""
        states = []
        for child in self.tree.get_children(iid):
            vals = self.tree.item(child, 'values')
            if vals and vals[0] == 'chapter':
                states.append(child in self._selected)
            else:
                states.extend(self._descendant_chapter_states(child))
        return states

    def _update_parent_state(self, iid):
        # Derive the section state from the selection set, not from
        # tree.item(child, 'image') string matching — Tk reports image names
        # like 'pyimage3', so comparing against 'checked'/'half' never
        # matched and fully-selected sections rendered as indeterminate.
        states = self._descendant_chapter_states(iid)
        if not states:
            return
        if all(states):
            img = self._checkbox_images['checked']
        elif any(states):
            img = self._checkbox_images['half']
        else:
            img = self._checkbox_images['unchecked']
        self.tree.item(iid, image=img)

    def _refresh_all_parents(self):
        def refresh(iid):
            children = self.tree.get_children(iid)
            for child in children:
                refresh(child)
            vals = self.tree.item(iid, 'values')
            if vals and vals[0] == 'section':
                self._update_parent_state(iid)
        for iid in self.tree.get_children(''):
            refresh(iid)

    # ── Preview panel ──────────────────────────────────────────────────

    def _update_preview(self, iid):
        vals = self.tree.item(iid, 'values')
        if not vals:
            return
        if vals[0] == 'chapter':
            ch = self._item_to_chapter.get(iid)
            if ch:
                self._show_chapter_preview(ch)
        elif vals[0] == 'section':
            title = self.tree.item(iid, 'text')
            self.preview_text.config(state=tk.NORMAL)
            self.preview_text.delete('1.0', tk.END)
            self.preview_text.insert('1.0', f'Section: {title}')
            self.preview_text.config(state=tk.DISABLED)
            self.preview_info_label.config(text='')
            self._selected_preview_chapter = None
            if self.play_button is not None:
                self.play_button.configure(state='disabled')

    def _on_play_preview_click(self):
        if self._selected_preview_chapter is None:
            return
        if self.on_play_preview is None or self.play_button is None:
            return
        self.on_play_preview(self._selected_preview_chapter, self.play_button)

    def _show_chapter_preview(self, chapter):
        text = chapter.extracted_text
        word_count = len(text.split())
        self.preview_text.config(state=tk.NORMAL)
        self.preview_text.delete('1.0', tk.END)
        self.preview_text.insert('1.0', text)
        self.preview_text.config(state=tk.DISABLED)
        word_str = 'word' if word_count == 1 else 'words'
        self.preview_info_label.config(text=f'{word_count:,} {word_str}')
        self._selected_preview_chapter = chapter
        if self.play_button is not None:
            self.play_button.configure(state='normal')

    def _show_book_info(self):
        md = self.metadata
        lines = []
        if md.get('title'):
            lines.append(f"Title: {md['title']}")
        authors = md.get('authors') or []
        if authors:
            lines.append(f"Author: {', '.join(authors)}")
        if md.get('publisher'):
            lines.append(f"Publisher: {md['publisher']}")
        if md.get('publication_year'):
            lines.append(f"Year: {md['publication_year']}")
        if md.get('description'):
            lines.append(f"\nDescription:\n{md['description']}")
        self.preview_text.config(state=tk.NORMAL)
        self.preview_text.delete('1.0', tk.END)
        self.preview_text.insert(
            '1.0', '\n'.join(lines) if lines else 'No metadata available')
        self.preview_text.config(state=tk.DISABLED)
        self.preview_info_label.config(text='Book Info')
        self._selected_preview_chapter = None
        if self.play_button is not None:
            self.play_button.configure(state='disabled')

    # ── Public API ─────────────────────────────────────────────────────

    def get_selected_chapters(self):
        selected = []
        for iid in self._selected:
            ch = self._item_to_chapter.get(iid)
            if ch:
                selected.append(ch)
        # Maintain chapter order
        order = {id(ch): i for i, ch in enumerate(self.chapters)}
        selected.sort(key=lambda ch: order.get(id(ch), 0))
        return selected

    def select_all(self):
        for iid in self._all_items():
            vals = self.tree.item(iid, 'values')
            if vals and vals[0] == 'chapter':
                self.tree.item(iid, image=self._checkbox_images['checked'])
                self._selected.add(iid)
            elif vals and vals[0] == 'section':
                self.tree.item(iid, image=self._checkbox_images['checked'])
        if self.on_selection_change:
            self.on_selection_change()

    def clear_all(self):
        for iid in self._all_items():
            self.tree.item(iid, image=self._checkbox_images['unchecked'])
        self._selected.clear()
        if self.on_selection_change:
            self.on_selection_change()

    def expand_all(self):
        for iid in self._all_items():
            self.tree.item(iid, open=True)

    def collapse_all(self):
        for iid in self.tree.get_children(''):
            self.tree.item(iid, open=False)

    def destroy(self):
        self.frame.destroy()

    # ── Helpers ────────────────────────────────────────────────────────

    def _all_items(self):
        result = []
        def walk(parent):
            for iid in self.tree.get_children(parent):
                result.append(iid)
                walk(iid)
        walk('')
        return result
