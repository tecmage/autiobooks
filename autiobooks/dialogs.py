import json
import os
import sys
import threading
import tkinter as tk
from pathlib import Path
from tkinter import filedialog, messagebox, ttk

from .engine import _probe_chapters, append_m4b, probe_duration


def show_append_dialog(parent):
    dialog = tk.Toplevel(parent)
    dialog.title('Append M4B Files')
    dialog.geometry('700x250')
    dialog.resizable(False, False)
    dialog.grab_set()

    for row, label in enumerate(['Base file:', 'Append file:', 'Output file:']):
        tk.Label(dialog, text=label).grid(row=row * 2, column=0, sticky='e',
                                          padx=10, pady=(6, 0))
    base_var = tk.StringVar()
    append_var = tk.StringVar()
    output_var = tk.StringVar()

    for row, var in enumerate([base_var, append_var, output_var]):
        ttk.Entry(dialog, textvariable=var, width=45).grid(
            row=row * 2, column=1, padx=5, pady=(6, 0))

    base_info_label = ttk.Label(dialog, text='', style='Summary.TLabel')
    base_info_label.grid(row=1, column=1, sticky='w', padx=5)

    append_info_label = ttk.Label(dialog, text='', style='Summary.TLabel')
    append_info_label.grid(row=3, column=1, sticky='w', padx=5)

    def load_file_info(path, label):
        def run():
            try:
                dur = probe_duration(path)
                chs = _probe_chapters(path)
                h, m = int(dur // 3600), int((dur % 3600) // 60)
                dur_str = f'{h}h {m}m' if h else f'{m}m'
                text = f'{len(chs)} chapter(s) · {dur_str}'
            except Exception as e:
                print(f'probe failed for {path}: {e}', file=sys.stderr)
                text = f'could not read file ({type(e).__name__})'
            if dialog.winfo_exists():
                dialog.after(0, lambda t=text: label.config(text=t))
        threading.Thread(target=run, daemon=True).start()

    def browse_open(var, info_label):
        p = filedialog.askopenfilename(
            parent=dialog, filetypes=[('M4B files', '*.m4b')])
        if p:
            var.set(p)
            info_label.config(text='reading...')
            load_file_info(p, info_label)

    def browse_save(var):
        p = filedialog.asksaveasfilename(
            parent=dialog, filetypes=[('M4B files', '*.m4b')],
            defaultextension='.m4b')
        if p:
            var.set(p)

    ttk.Button(dialog, text='Browse',
               command=lambda: browse_open(base_var, base_info_label)).grid(
        row=0, column=2, padx=5, pady=(6, 0))
    ttk.Button(dialog, text='Browse',
               command=lambda: browse_open(append_var, append_info_label)).grid(
        row=2, column=2, padx=5, pady=(6, 0))
    ttk.Button(dialog, text='Browse',
               command=lambda: browse_save(output_var)).grid(
        row=4, column=2, padx=5, pady=(6, 0))

    status_label = tk.Label(dialog, text='')
    status_label.grid(row=5, column=0, columnspan=3, pady=6)

    def do_append():
        base = base_var.get().strip()
        append = append_var.get().strip()
        output = output_var.get().strip()
        if not base or not append or not output:
            messagebox.showerror('Error', 'Please select all three files.',
                                 parent=dialog)
            return
        for path, label in [(base, 'Base'), (append, 'Append')]:
            if not Path(path).exists():
                messagebox.showerror(
                    'Error', f'{label} file does not exist:\n{path}',
                    parent=dialog)
                return
            if not path.lower().endswith('.m4b'):
                messagebox.showerror(
                    'Error', f'{label} file must be an .m4b file.',
                    parent=dialog)
                return
        output_parent = Path(output).parent
        if not output_parent.is_dir():
            messagebox.showerror(
                'Error',
                f'Output directory does not exist:\n{output_parent}',
                parent=dialog)
            return
        if not os.access(output_parent, os.W_OK):
            messagebox.showerror(
                'Error',
                f'Output directory is not writable:\n{output_parent}',
                parent=dialog)
            return
        append_btn.configure(state='disabled')
        status_label.config(text='Appending... 0%')

        def run():
            try:
                def progress(pct):
                    dialog.after(0, lambda p=pct: status_label.config(
                        text=f'Appending... {p}%'))
                append_m4b(base, append, output, progress_callback=progress)
                dialog.after(0, lambda: status_label.config(text='Done!'))
            except Exception as e:
                dialog.after(0, lambda err=e: messagebox.showerror(
                    'Error', str(err), parent=dialog))
                dialog.after(0, lambda: status_label.config(text='Error'))
            finally:
                dialog.after(0, lambda: append_btn.configure(state='normal'))

        threading.Thread(target=run, daemon=True).start()

    append_btn = ttk.Button(dialog, text='Append', command=do_append)
    append_btn.grid(row=6, column=1, pady=6)


def show_preferences(parent, prefs, apply_theme, save_current_config, add_tooltip):
    """Open the Preferences dialog.

    prefs: dict of tk variables — theme_var, heteronyms, contractions,
    auto_select, mark_duplicates, auto_acronyms.
    apply_theme: callable(theme_name) that restyles the app.
    save_current_config: callable() that persists the current config.
    add_tooltip: callable(widget, text) that attaches a tooltip.
    """
    dlg = tk.Toplevel(parent)
    dlg.title('Preferences')
    dlg.geometry('500x380')
    dlg.resizable(False, False)
    dlg.grab_set()

    tf = ttk.LabelFrame(dlg, text='Appearance', padding=10)
    tf.pack(fill=tk.X, padx=15, pady=(15, 5))
    tk.Label(tf, text='Theme:').pack(side=tk.LEFT)
    theme_var = prefs['theme_var']
    for tname in ('light', 'dark'):
        tk.Radiobutton(tf, text=tname.capitalize(), variable=theme_var,
                       value=tname,
                       command=lambda: apply_theme(theme_var.get())
                       ).pack(side=tk.LEFT, padx=10)

    tp = ttk.LabelFrame(dlg, text='Text Processing', padding=10)
    tp.pack(fill=tk.X, padx=15, pady=5)
    tk.Checkbutton(tp, text='Heteronym disambiguation (read, lead, wind...)',
                   variable=prefs['heteronyms']).pack(anchor='w')
    add_tooltip(tp.winfo_children()[-1],
                'Use spaCy POS tagging to resolve ambiguous words.\n'
                'Requires spaCy + en_core_web_sm.')
    tk.Checkbutton(tp, text='Contraction resolution (\'s, \'d)',
                   variable=prefs['contractions']).pack(anchor='w')
    add_tooltip(tp.winfo_children()[-1],
                'Expand ambiguous contractions using spaCy context.\n'
                'Requires spaCy + en_core_web_sm.')
    if 'auto_acronyms' in prefs:
        tk.Checkbutton(tp, text='Spell out unknown acronyms (CIA, FBI, HTML)',
                       variable=prefs['auto_acronyms']).pack(anchor='w')
        add_tooltip(tp.winfo_children()[-1],
                    'All-caps words (2-6 letters) not already in the TTS\n'
                    'lexicon get spelled out letter-by-letter. Known\n'
                    'acronyms like NATO / NASA keep their word pronunciation.')

    cl = ttk.LabelFrame(dlg, text='Chapter Loading', padding=10)
    cl.pack(fill=tk.X, padx=15, pady=5)
    tk.Checkbutton(cl, text='Auto-select chapters on load',
                   variable=prefs['auto_select']).pack(anchor='w')
    add_tooltip(cl.winfo_children()[-1],
                'Automatically check non-empty, non-duplicate chapters\n'
                'when a book is opened.')
    tk.Checkbutton(cl, text='Mark duplicate chapters',
                   variable=prefs['mark_duplicates']).pack(anchor='w')
    add_tooltip(cl.winfo_children()[-1],
                'Detect and label chapters with identical content.\n'
                'Duplicates are excluded from auto-select.')

    def save_and_close():
        save_current_config()
        dlg.destroy()

    ttk.Button(dlg, text='Close', command=save_and_close).pack(
        side=tk.RIGHT, padx=15, pady=15)


def show_substitutions_dialog(parent, initial_subs, on_save):
    """Open the word substitutions editor.

    initial_subs: current list of substitution dicts.
    on_save: callable(new_subs) invoked when the user clicks Save.
    """
    dlg = tk.Toplevel(parent)
    dlg.title('Word Substitutions')
    dlg.geometry('900x500')
    dlg.resizable(True, True)
    dlg.grab_set()

    tk.Label(dlg, text='Fix recurring mispronunciations by adding '
             'find/replace pairs applied before TTS.',
             wraplength=860, justify='left').pack(padx=10, pady=(10, 5))

    list_frame = ttk.Frame(dlg)
    list_frame.pack(fill=tk.BOTH, expand=True, padx=10, pady=5)

    cols = ('find', 'replace', 'case', 'whole')
    tree = ttk.Treeview(list_frame, columns=cols, show='headings', height=10)
    tree.heading('find', text='Find')
    tree.heading('replace', text='Replace')
    tree.heading('case', text='Case')
    tree.heading('whole', text='Whole Word')
    tree.column('find', width=280)
    tree.column('replace', width=280)
    tree.column('case', width=80, anchor='center')
    tree.column('whole', width=100, anchor='center')
    vsb = ttk.Scrollbar(list_frame, orient='vertical', command=tree.yview)
    tree.configure(yscrollcommand=vsb.set)
    vsb.pack(side=tk.RIGHT, fill=tk.Y)
    tree.pack(fill=tk.BOTH, expand=True)

    local_subs = [dict(s) for s in initial_subs]

    def refresh():
        tree.delete(*tree.get_children())
        for s in local_subs:
            cs = 'Yes' if s.get('case_sensitive') else 'No'
            ww = 'Yes' if s.get('whole_word', True) else 'No'
            tree.insert('', 'end', values=(
                s.get('find', ''), s.get('replace', ''), cs, ww))

    refresh()

    btn_frame = ttk.Frame(dlg)
    btn_frame.pack(fill=tk.X, padx=10, pady=5)

    add_frame = ttk.Frame(dlg)
    add_frame.pack(fill=tk.X, padx=10, pady=(0, 5))
    tk.Label(add_frame, text='Find:').pack(side=tk.LEFT)
    find_entry = ttk.Entry(add_frame, width=20)
    find_entry.pack(side=tk.LEFT, padx=(2, 10))
    tk.Label(add_frame, text='Replace:').pack(side=tk.LEFT)
    replace_entry = ttk.Entry(add_frame, width=20)
    replace_entry.pack(side=tk.LEFT, padx=(2, 10))
    case_var = tk.BooleanVar(value=False)
    tk.Checkbutton(add_frame, text='Case sensitive',
                   variable=case_var).pack(side=tk.LEFT)
    whole_var = tk.BooleanVar(value=True)
    tk.Checkbutton(add_frame, text='Whole word',
                   variable=whole_var).pack(side=tk.LEFT)

    def add_sub():
        f = find_entry.get().strip()
        if not f:
            return
        local_subs.append({
            'find': f,
            'replace': replace_entry.get(),
            'case_sensitive': case_var.get(),
            'whole_word': whole_var.get(),
        })
        find_entry.delete(0, tk.END)
        replace_entry.delete(0, tk.END)
        refresh()

    def remove_sub():
        sel = tree.selection()
        if not sel:
            return
        idx = tree.index(sel[0])
        del local_subs[idx]
        refresh()

    ttk.Button(btn_frame, text='Add', command=add_sub).pack(
        side=tk.LEFT, padx=3)
    ttk.Button(btn_frame, text='Remove Selected', command=remove_sub).pack(
        side=tk.LEFT, padx=3)

    def save_and_close():
        on_save(local_subs)
        dlg.destroy()

    ttk.Button(dlg, text='Save', command=save_and_close).pack(
        side=tk.RIGHT, padx=10, pady=10)
    ttk.Button(dlg, text='Cancel', command=dlg.destroy).pack(
        side=tk.RIGHT, pady=10)


def show_phoneme_overrides_dialog(parent, initial, on_save):
    """Open the pronunciation-overrides editor.

    Each entry forces a specific pronunciation for a word by wrapping
    matches in misaki's inline-phoneme markdown (`[word](/IPA/)`). Use
    this for names, technical terms, and initialisms the lexicon
    mispronounces. English voices only.

    initial: current list of override dicts ({word, ipa, case_sensitive, enabled}).
    on_save: callable(new_overrides) invoked when the user clicks Save.
    """
    dlg = tk.Toplevel(parent)
    dlg.title('Pronunciation Overrides')
    dlg.geometry('900x520')
    dlg.resizable(True, True)
    dlg.grab_set()

    tk.Label(
        dlg,
        text='Force a specific pronunciation by entering IPA phonemes. '
             'Applied to English voices only. Names with apostrophes or '
             'hyphens (O\'Brien, Anne-Marie) match without word boundaries.',
        wraplength=860, justify='left').pack(padx=10, pady=(10, 0))
    tk.Label(
        dlg,
        text='IPA cheat-sheet: ˈ primary stress · ˌ secondary · '
             'ə schwa · ɜ her · æ cat · ʃ ship · θ think · ð this',
        fg='#555',
        wraplength=860, justify='left').pack(padx=10, pady=(0, 5))

    list_frame = ttk.Frame(dlg)
    list_frame.pack(fill=tk.BOTH, expand=True, padx=10, pady=5)

    cols = ('word', 'ipa', 'case', 'enabled')
    tree = ttk.Treeview(list_frame, columns=cols, show='headings',
                        height=10, selectmode='extended')
    tree.heading('word', text='Word')
    tree.heading('ipa', text='IPA Phonemes')
    tree.heading('case', text='Case')
    tree.heading('enabled', text='Enabled')
    tree.column('word', width=220)
    tree.column('ipa', width=320)
    tree.column('case', width=80, anchor='center')
    tree.column('enabled', width=80, anchor='center')
    vsb = ttk.Scrollbar(list_frame, orient='vertical', command=tree.yview)
    tree.configure(yscrollcommand=vsb.set)
    vsb.pack(side=tk.RIGHT, fill=tk.Y)
    tree.pack(fill=tk.BOTH, expand=True)

    local = [dict(o) for o in initial]

    def refresh():
        tree.delete(*tree.get_children())
        for o in local:
            cs = 'Yes' if o.get('case_sensitive') else 'No'
            en = 'Yes' if o.get('enabled', True) else 'No'
            tree.insert('', 'end', values=(
                o.get('word', ''), o.get('ipa', ''), cs, en))

    refresh()

    btn_frame = ttk.Frame(dlg)
    btn_frame.pack(fill=tk.X, padx=10, pady=5)

    add_frame = ttk.Frame(dlg)
    add_frame.pack(fill=tk.X, padx=10, pady=(0, 5))
    tk.Label(add_frame, text='Word:').pack(side=tk.LEFT)
    word_entry = ttk.Entry(add_frame, width=18)
    word_entry.pack(side=tk.LEFT, padx=(2, 10))
    tk.Label(add_frame, text='IPA:').pack(side=tk.LEFT)
    ipa_entry = ttk.Entry(add_frame, width=22)
    ipa_entry.pack(side=tk.LEFT, padx=(2, 10))
    case_var = tk.BooleanVar(value=False)
    tk.Checkbutton(add_frame, text='Case sensitive',
                   variable=case_var).pack(side=tk.LEFT)
    enabled_var = tk.BooleanVar(value=True)
    tk.Checkbutton(add_frame, text='Enabled',
                   variable=enabled_var).pack(side=tk.LEFT)

    def add_override():
        w = word_entry.get().strip()
        i = ipa_entry.get().strip()
        if not w or not i:
            return
        local.append({
            'word': w,
            'ipa': i,
            'case_sensitive': case_var.get(),
            'enabled': enabled_var.get(),
        })
        word_entry.delete(0, tk.END)
        ipa_entry.delete(0, tk.END)
        refresh()

    def remove_override():
        sel = tree.selection()
        if not sel:
            return
        for idx in sorted((tree.index(s) for s in sel), reverse=True):
            del local[idx]
        refresh()

    def toggle_enabled():
        sel = tree.selection()
        if not sel:
            return
        idxs = [tree.index(s) for s in sel]
        new_value = not all(local[i].get('enabled', True) for i in idxs)
        for i in idxs:
            local[i]['enabled'] = new_value
        refresh()
        for i in idxs:
            tree.selection_add(tree.get_children()[i])

    def toggle_case_sensitive():
        sel = tree.selection()
        if not sel:
            return
        idxs = [tree.index(s) for s in sel]
        new_value = not all(local[i].get('case_sensitive', False) for i in idxs)
        for i in idxs:
            local[i]['case_sensitive'] = new_value
        refresh()
        for i in idxs:
            tree.selection_add(tree.get_children()[i])

    def import_json():
        path = filedialog.askopenfilename(
            parent=dlg, title='Import pronunciation overrides',
            filetypes=[('JSON files', '*.json'), ('All files', '*.*')])
        if not path:
            return
        try:
            with open(path, encoding='utf-8') as f:
                data = json.load(f)
        except (OSError, json.JSONDecodeError) as e:
            messagebox.showerror('Import failed', f'Could not read {path}:\n{e}', parent=dlg)
            return
        if not isinstance(data, list):
            messagebox.showerror('Import failed',
                                 'Expected a JSON list of override objects.', parent=dlg)
            return
        existing = {o.get('word', '').lower() for o in local}
        added = skipped = 0
        for entry in data:
            if not isinstance(entry, dict):
                continue
            w = str(entry.get('word', '')).strip()
            i = str(entry.get('ipa', '')).strip()
            if not w or not i:
                continue
            if w.lower() in existing:
                skipped += 1
                continue
            local.append({
                'word': w,
                'ipa': i,
                'case_sensitive': bool(entry.get('case_sensitive', False)),
                'enabled': bool(entry.get('enabled', True)),
            })
            existing.add(w.lower())
            added += 1
        refresh()
        messagebox.showinfo(
            'Import complete',
            f'Added {added} override(s). Skipped {skipped} duplicate(s).',
            parent=dlg)

    ttk.Button(btn_frame, text='Add', command=add_override).pack(
        side=tk.LEFT, padx=3)
    ttk.Button(btn_frame, text='Remove Selected',
               command=remove_override).pack(side=tk.LEFT, padx=3)
    ttk.Button(btn_frame, text='Toggle Enabled',
               command=toggle_enabled).pack(side=tk.LEFT, padx=3)
    ttk.Button(btn_frame, text='Toggle Case Sensitive',
               command=toggle_case_sensitive).pack(side=tk.LEFT, padx=3)
    ttk.Button(btn_frame, text='Import JSON…',
               command=import_json).pack(side=tk.LEFT, padx=3)

    def export_json():
        if not local:
            messagebox.showinfo(
                'Nothing to export',
                'There are no pronunciation overrides to export.',
                parent=dlg)
            return
        path = filedialog.asksaveasfilename(
            parent=dlg, title='Export pronunciation overrides',
            defaultextension='.json',
            initialfile='pronunciation_overrides.json',
            filetypes=[('JSON files', '*.json'), ('All files', '*.*')])
        if not path:
            return
        try:
            with open(path, 'w', encoding='utf-8') as f:
                json.dump(local, f, ensure_ascii=False, indent=2)
        except OSError as e:
            messagebox.showerror(
                'Export failed', f'Could not write {path}:\n{e}', parent=dlg)
            return
        messagebox.showinfo(
            'Export complete',
            f'Exported {len(local)} override(s) to:\n{path}',
            parent=dlg)

    ttk.Button(btn_frame, text='Export JSON…',
               command=export_json).pack(side=tk.LEFT, padx=3)

    def save_and_close():
        on_save(local)
        dlg.destroy()

    ttk.Button(dlg, text='Save', command=save_and_close).pack(
        side=tk.RIGHT, padx=10, pady=10)
    ttk.Button(dlg, text='Cancel', command=dlg.destroy).pack(
        side=tk.RIGHT, pady=10)
