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
    auto_select, mark_duplicates.
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
