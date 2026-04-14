import tkinter as tk
from tkinter import ttk


THEMES = {
    'light': {
        'bg': '#f0f0f0', 'fg': '#000000', 'entry_bg': '#ffffff',
        'entry_fg': '#000000', 'select_bg': '#0078d7',
        'select_fg': '#ffffff', 'frame_bg': '#f0f0f0',
        'label_fg': '#333333', 'summary_fg': '#555555',
        'preview_fg': '#666666',
        'tooltip_bg': '#ffffe0', 'tooltip_fg': '#000000',
        'cancel_fg': '#c0392b', 'cancel_hover_bg': '#c0392b',
        'cancel_hover_fg': '#ffffff',
        'button_bg': '#e1e1e1', 'button_border': '#adadad',
    },
    'dark': {
        'bg': '#2b2b2b', 'fg': '#e0e0e0', 'entry_bg': '#3c3c3c',
        'entry_fg': '#e0e0e0', 'select_bg': '#264f78',
        'select_fg': '#ffffff', 'frame_bg': '#2b2b2b',
        'label_fg': '#cccccc', 'summary_fg': '#aaaaaa',
        'preview_fg': '#888888',
        'tooltip_bg': '#3c3c3c', 'tooltip_fg': '#e0e0e0',
        'cancel_fg': '#ff6b6b', 'cancel_hover_bg': '#c0392b',
        'cancel_hover_fg': '#ffffff',
        'button_bg': '#3c3c3c', 'button_border': '#5a5a5a',
    },
}

_current_theme_name = 'light'


def get_current_theme():
    return THEMES[_current_theme_name]


def apply_theme(root, theme_name):
    global _current_theme_name
    if theme_name not in THEMES:
        return
    _current_theme_name = theme_name
    t = THEMES[theme_name]
    root.configure(bg=t['bg'])

    root.option_add('*Label.Background', t['bg'])
    root.option_add('*Label.Foreground', t['fg'])
    root.option_add('*Checkbutton.Background', t['bg'])
    root.option_add('*Checkbutton.Foreground', t['fg'])
    root.option_add('*Checkbutton.activeBackground', t['bg'])
    root.option_add('*Checkbutton.activeForeground', t['fg'])
    root.option_add('*Checkbutton.selectColor', t['entry_bg'])
    root.option_add('*Radiobutton.Background', t['bg'])
    root.option_add('*Radiobutton.Foreground', t['fg'])
    root.option_add('*Radiobutton.activeBackground', t['bg'])
    root.option_add('*Radiobutton.activeForeground', t['fg'])
    root.option_add('*Radiobutton.selectColor', t['entry_bg'])
    root.option_add('*Button.Background', t['bg'])
    root.option_add('*Button.Foreground', t['fg'])
    root.option_add('*Button.activeBackground', t['select_bg'])
    root.option_add('*Button.activeForeground', t['select_fg'])
    root.option_add('*Entry.Background', t['entry_bg'])
    root.option_add('*Entry.Foreground', t['entry_fg'])
    root.option_add('*Entry.insertBackground', t['fg'])
    root.option_add('*Entry.selectBackground', t['select_bg'])
    root.option_add('*Entry.selectForeground', t['select_fg'])
    root.option_add('*Text.Background', t['entry_bg'])
    root.option_add('*Text.Foreground', t['entry_fg'])
    root.option_add('*Text.insertBackground', t['fg'])
    root.option_add('*Text.selectBackground', t['select_bg'])
    root.option_add('*Text.selectForeground', t['select_fg'])
    root.option_add('*Toplevel.Background', t['bg'])
    root.option_add('*Frame.Background', t['bg'])
    root.option_add('*Canvas.Background', t['bg'])
    root.option_add('*Menu.Background', t['bg'])
    root.option_add('*Menu.Foreground', t['fg'])
    root.option_add('*Menu.activeBackground', t['select_bg'])
    root.option_add('*Menu.activeForeground', t['select_fg'])

    style = ttk.Style()
    style.configure('TFrame', background=t['bg'])
    style.configure('TLabel', background=t['bg'], foreground=t['fg'])
    style.configure('Summary.TLabel', background=t['bg'],
                    foreground=t['summary_fg'], font=('Arial', 11))
    style.configure('TSeparator', background=t['bg'])

    style.configure('TButton', background=t['button_bg'], foreground=t['fg'],
                    bordercolor=t['button_border'],
                    lightcolor=t['button_border'],
                    darkcolor=t['button_border'])
    style.map('TButton',
              foreground=[('disabled', t['summary_fg']),
                          ('pressed', t['select_fg']),
                          ('active', t['select_fg'])],
              background=[('pressed', t['select_bg']),
                          ('active', t['select_bg'])],
              lightcolor=[('pressed', t['select_bg']),
                          ('active', t['select_bg'])],
              darkcolor=[('pressed', t['select_bg']),
                         ('active', t['select_bg'])],
              bordercolor=[('pressed', t['select_bg']),
                           ('active', t['select_bg'])])

    style.configure('Cancel.TButton', background=t['button_bg'],
                    foreground=t['cancel_fg'],
                    bordercolor=t['button_border'],
                    lightcolor=t['button_border'],
                    darkcolor=t['button_border'])
    style.map('Cancel.TButton',
              foreground=[('disabled', t['summary_fg']),
                          ('pressed', t['cancel_hover_fg']),
                          ('active', t['cancel_hover_fg'])],
              background=[('pressed', t['cancel_hover_bg']),
                          ('active', t['cancel_hover_bg'])],
              lightcolor=[('pressed', t['cancel_hover_bg']),
                          ('active', t['cancel_hover_bg'])],
              darkcolor=[('pressed', t['cancel_hover_bg']),
                         ('active', t['cancel_hover_bg'])],
              bordercolor=[('pressed', t['cancel_hover_bg']),
                           ('active', t['cancel_hover_bg'])])

    style.configure('TLabelframe', background=t['bg'])
    style.configure('TLabelframe.Label', background=t['bg'],
                    foreground=t['fg'])

    style.configure('TEntry', fieldbackground=t['entry_bg'],
                    foreground=t['entry_fg'], insertcolor=t['fg'],
                    bordercolor=t['entry_bg'], lightcolor=t['entry_bg'],
                    darkcolor=t['entry_bg'])
    style.map('TEntry',
              fieldbackground=[('readonly', t['entry_bg']),
                               ('disabled', t['bg'])],
              foreground=[('readonly', t['entry_fg']),
                          ('disabled', t['summary_fg'])])

    style.configure('TCheckbutton', background=t['bg'], foreground=t['fg'],
                    indicatorcolor=t['entry_bg'])
    style.map('TCheckbutton',
              background=[('active', t['bg'])],
              foreground=[('disabled', t['summary_fg'])],
              indicatorcolor=[('selected', t['select_bg']),
                              ('!selected', t['entry_bg'])])

    style.configure('TRadiobutton', background=t['bg'], foreground=t['fg'],
                    indicatorcolor=t['entry_bg'])
    style.map('TRadiobutton',
              background=[('active', t['bg'])],
              foreground=[('disabled', t['summary_fg'])],
              indicatorcolor=[('selected', t['select_bg']),
                              ('!selected', t['entry_bg'])])

    style.configure('TCombobox', fieldbackground=t['entry_bg'],
                    foreground=t['entry_fg'], background=t['bg'],
                    arrowcolor=t['fg'])
    style.map('TCombobox',
              fieldbackground=[('readonly', t['entry_bg'])],
              foreground=[('readonly', t['entry_fg'])],
              arrowcolor=[('disabled', t['summary_fg'])])
    root.option_add('*TCombobox*Listbox.background', t['entry_bg'])
    root.option_add('*TCombobox*Listbox.foreground', t['entry_fg'])
    root.option_add('*TCombobox*Listbox.selectBackground', t['select_bg'])
    root.option_add('*TCombobox*Listbox.selectForeground', t['select_fg'])

    style.configure('TPanedwindow', background=t['bg'])
    style.configure('TScrollbar', background=t['bg'],
                    troughcolor=t['entry_bg'], bordercolor=t['bg'],
                    arrowcolor=t['fg'])
    style.map('TScrollbar',
              background=[('active', t['select_bg'])],
              arrowcolor=[('disabled', t['summary_fg'])])

    style.configure('Treeview', background=t['entry_bg'],
                    foreground=t['entry_fg'], fieldbackground=t['entry_bg'])
    style.map('Treeview', background=[('selected', t['select_bg'])],
              foreground=[('selected', t['select_fg'])])
    style.configure('Treeview.Heading', background=t['bg'],
                    foreground=t['fg'])
    style.map('Treeview.Heading',
              background=[('active', t['select_bg'])],
              foreground=[('active', t['select_fg'])])

    def apply_to_widget(w):
        try:
            if isinstance(w, (tk.Frame, tk.Canvas, tk.Toplevel)):
                w.configure(bg=t['bg'])
            elif isinstance(w, tk.Label):
                w.configure(bg=t['bg'], fg=t['fg'])
            elif isinstance(w, tk.Checkbutton):
                w.configure(bg=t['bg'], fg=t['fg'],
                            activebackground=t['bg'],
                            activeforeground=t['fg'],
                            selectcolor=t['entry_bg'])
            elif isinstance(w, tk.Radiobutton):
                w.configure(bg=t['bg'], fg=t['fg'],
                            activebackground=t['bg'],
                            activeforeground=t['fg'],
                            selectcolor=t['entry_bg'])
            elif isinstance(w, tk.Button):
                w.configure(bg=t['bg'], fg=t['fg'],
                            activebackground=t['select_bg'],
                            activeforeground=t['select_fg'])
            elif isinstance(w, (tk.Entry, tk.Text)):
                w.configure(bg=t['entry_bg'], fg=t['entry_fg'],
                            insertbackground=t['fg'],
                            selectbackground=t['select_bg'],
                            selectforeground=t['select_fg'])
            elif isinstance(w, tk.Menu):
                w.configure(bg=t['bg'], fg=t['fg'],
                            activebackground=t['select_bg'],
                            activeforeground=t['select_fg'])
        except tk.TclError:
            pass
        for child in w.winfo_children():
            apply_to_widget(child)

    root.after(0, lambda: apply_to_widget(root))
