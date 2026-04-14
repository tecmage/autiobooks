import sys
import threading
import time
import traceback
import tkinter as tk
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from tkinter import filedialog, messagebox, ttk

from .engine import (
    _INTERMEDIATE_EXTS,
    concat_audio_files,
    convert_chapters_to_wav,
    create_m4b,
    safe_stem,
    set_gpu_acceleration,
)
from .epub_parser import get_cover_image
from .voices_lang import deemojify_voice


def _final_ext(fmt):
    return '.m4b' if fmt == 'm4b' else _INTERMEDIATE_EXTS.get(fmt, '.m4b')


def show_batch_window(
    parent,
    batch_queue,
    initial_dir,
    prevent_sleep,
    prefs,
    get_substitutions,
):
    """Open the batch queue window.

    batch_queue: shared mutable list of BatchJob objects.
    initial_dir: default output directory string.
    prevent_sleep: context manager that inhibits OS sleep during run.
    prefs: dict with tk BooleanVars — 'heteronyms', 'contractions'.
    get_substitutions: callable returning the current word substitutions list.
    """
    if not batch_queue:
        messagebox.showinfo(
            "Batch Queue",
            "The batch queue is empty.\n\n"
            "Load an epub, configure chapters/settings,\n"
            "then click 'Add to Batch'.")
        return

    bw = tk.Toplevel(parent)
    bw.title("Batch Queue")
    bw.geometry("800x500")
    bw.resizable(True, True)

    def safe_after(delay, fn, *args):
        """Schedule fn on the Tk main loop only if the window still exists.

        The batch worker runs in a background thread and posts progress
        updates via bw.after(). If the user closes the window mid-run the
        worker can't interrupt immediately, so its pending callbacks would
        otherwise raise RuntimeError on a destroyed widget. This helper is
        a no-op once the window is gone.
        """
        try:
            if bw.winfo_exists():
                bw.after(delay, fn, *args)
        except tk.TclError:
            pass

    tree_frame = tk.Frame(bw)
    tree_frame.pack(fill=tk.BOTH, expand=True, padx=10, pady=5)

    columns = ('num', 'title', 'voice', 'format', 'bitrate',
               'chapters', 'words', 'status')
    tree = ttk.Treeview(tree_frame, columns=columns,
                        show='headings', height=12)
    tree.heading('num', text='#')
    tree.heading('title', text='Title')
    tree.heading('voice', text='Voice')
    tree.heading('format', text='Format')
    tree.heading('bitrate', text='Bitrate')
    tree.heading('chapters', text='Chapters')
    tree.heading('words', text='Words')
    tree.heading('status', text='Status')
    tree.column('num', width=40, stretch=False)
    tree.column('title', width=200)
    tree.column('voice', width=130)
    tree.column('format', width=60, stretch=False)
    tree.column('bitrate', width=80, stretch=False)
    tree.column('chapters', width=70, stretch=False)
    tree.column('words', width=70, stretch=False)
    tree.column('status', width=80, stretch=False)

    tree_scroll = ttk.Scrollbar(tree_frame, orient='vertical',
                                command=tree.yview)
    tree.configure(yscrollcommand=tree_scroll.set)
    tree.pack(side='left', fill='both', expand=True)
    tree_scroll.pack(side='right', fill='y')

    def _format_bitrate(job):
        fmt = getattr(job, 'output_format', 'm4b')
        if fmt in ('flac', 'wav'):
            return '—'
        if fmt == 'm4b' and job.vbr:
            return 'VBR q2'
        if fmt == 'mp3' and job.vbr:
            return f'{job.bitrate} VBR'
        return job.bitrate

    def refresh_treeview():
        tree.delete(*tree.get_children())
        for i, job in enumerate(batch_queue):
            sel = len(job.selected_chapter_indices)
            total = len([c for c in job.chapters
                         if len(c.extracted_text.split()) > 0])
            fmt = getattr(job, 'output_format', 'm4b').upper()
            tree.insert('', 'end', values=(
                i + 1, job.title, job.voice, fmt,
                _format_bitrate(job),
                f"{sel}/{total}",
                f"{job.total_words:,}",
                job.status))

    refresh_treeview()

    btn_frame = tk.Frame(bw)
    btn_frame.pack(fill=tk.X, padx=10, pady=5)

    def move_up():
        sel = tree.selection()
        if not sel:
            return
        idx = tree.index(sel[0])
        if idx > 0:
            batch_queue[idx], batch_queue[idx - 1] = (
                batch_queue[idx - 1], batch_queue[idx])
            refresh_treeview()
            tree.selection_set(tree.get_children()[idx - 1])

    def move_down():
        sel = tree.selection()
        if not sel:
            return
        idx = tree.index(sel[0])
        if idx < len(batch_queue) - 1:
            batch_queue[idx], batch_queue[idx + 1] = (
                batch_queue[idx + 1], batch_queue[idx])
            refresh_treeview()
            tree.selection_set(tree.get_children()[idx + 1])

    def remove_selected():
        sel = tree.selection()
        if not sel:
            return
        idx = tree.index(sel[0])
        batch_queue.pop(idx)
        refresh_treeview()
        if not batch_queue:
            bw.destroy()

    def clear_all_jobs():
        if messagebox.askyesno("Clear All",
                               "Remove all jobs from the queue?",
                               parent=bw):
            batch_queue.clear()
            bw.destroy()

    move_up_btn = ttk.Button(btn_frame, text='Move Up', command=move_up)
    move_up_btn.pack(side=tk.LEFT, padx=3)
    move_down_btn = ttk.Button(btn_frame, text='Move Down',
                               command=move_down)
    move_down_btn.pack(side=tk.LEFT, padx=3)
    remove_btn = ttk.Button(btn_frame, text='Remove',
                            command=remove_selected)
    remove_btn.pack(side=tk.LEFT, padx=3)
    clear_btn = ttk.Button(btn_frame, text='Clear All',
                           command=clear_all_jobs)
    clear_btn.pack(side=tk.LEFT, padx=3)
    queue_mutation_btns = (move_up_btn, move_down_btn, remove_btn, clear_btn)

    dir_frame = tk.Frame(bw)
    dir_frame.pack(fill=tk.X, padx=10, pady=5)
    tk.Label(dir_frame, text="Output directory:").pack(side=tk.LEFT)
    dir_var = tk.StringVar(value=initial_dir or '')
    dir_entry = ttk.Entry(dir_frame, textvariable=dir_var, width=50)
    dir_entry.pack(side=tk.LEFT, padx=5, fill=tk.X, expand=True)

    def browse_dir():
        d = filedialog.askdirectory(parent=bw,
                                    initialdir=dir_var.get() or None)
        if d:
            dir_var.set(d)

    ttk.Button(dir_frame, text='Browse',
               command=browse_dir).pack(side=tk.LEFT, padx=3)

    prog_frame = tk.Frame(bw)
    prog_frame.pack(fill=tk.X, padx=10, pady=5)
    batch_progress = ttk.Progressbar(prog_frame, orient='horizontal',
                                     mode='determinate')
    batch_progress.pack(fill=tk.X, side=tk.LEFT, expand=True, padx=(0, 5))
    batch_status = tk.Label(prog_frame, text="Ready")
    batch_status.pack(side=tk.LEFT)

    action_frame = tk.Frame(bw)
    action_frame.pack(fill=tk.X, padx=10, pady=(0, 10))

    batch_cancel = threading.Event()

    def start_batch():
        output_dir = dir_var.get().strip()
        if not output_dir:
            messagebox.showwarning("Warning",
                                   "Please select an output directory.",
                                   parent=bw)
            return
        if not Path(output_dir).is_dir():
            messagebox.showwarning("Warning",
                                   "Output directory does not exist.",
                                   parent=bw)
            return

        start_btn.configure(state='disabled')
        cancel_btn.configure(state='normal')
        for b in queue_mutation_btns:
            b.configure(state='disabled')
        batch_cancel.clear()

        user_gpu_pref = None
        gpu_var = prefs.get('gpu_acceleration')
        if gpu_var is not None:
            try:
                user_gpu_pref = bool(gpu_var.get())
            except tk.TclError:
                user_gpu_pref = None

        def run():
            def _restore_buttons():
                if not bw.winfo_exists():
                    return
                try:
                    start_btn.configure(state='normal')
                    cancel_btn.configure(state='disabled')
                    for b in queue_mutation_btns:
                        b.configure(state='normal')
                except tk.TclError:
                    pass

            try:
                _run_body()
            except Exception as e:
                print(f"Batch run crashed: {e}", file=sys.stderr)
                traceback.print_exc()
                safe_after(0, lambda err=str(e):
                           messagebox.showerror(
                               "Batch Error",
                               f"Batch run crashed:\n\n{err}",
                               parent=bw))
            finally:
                if user_gpu_pref is not None:
                    try:
                        set_gpu_acceleration(user_gpu_pref)
                    except Exception as gpu_err:
                        print(f"Failed to restore GPU state: {gpu_err}",
                              file=sys.stderr)
                safe_after(0, _restore_buttons)

        def _run_body():
            total_jobs = len(batch_queue)
            jobs_completed = 0
            jobs_failed = 0
            failed_jobs = []
            used_output_paths = set()

            def _resolve_collision(path):
                """Return a path that doesn't collide with anything already
                claimed this batch. Appends ' (2)', ' (3)', ... before the
                extension when needed. Uses casefold for case-insensitive
                filesystems (Windows/macOS default)."""
                p = Path(path)
                candidate = p
                n = 2
                while str(candidate).casefold() in used_output_paths:
                    candidate = p.with_name(f'{p.stem} ({n}){p.suffix}')
                    n += 1
                used_output_paths.add(str(candidate).casefold())
                return str(candidate)

            def set_bprog(value):
                safe_after(0, lambda v=value:
                           batch_progress.configure(value=v))

            def set_bstat(text):
                safe_after(0, lambda t=text:
                           batch_status.config(text=t))

            def _cleanup_files(paths):
                for p in paths:
                    last_err = None
                    for attempt in range(3):
                        try:
                            Path(p).unlink(missing_ok=True)
                            last_err = None
                            break
                        except OSError as err:
                            last_err = err
                            if attempt < 2:
                                time.sleep(2)
                    if last_err is not None:
                        print(f'failed to delete {p}: {last_err}',
                              file=sys.stderr)

            for job_idx, job in enumerate(batch_queue):
                if batch_cancel.is_set():
                    job.status = "Cancelled"
                    safe_after(0, refresh_treeview)
                    break

                job.status = "Converting"
                safe_after(0, refresh_treeview)

                job_start_pct = (job_idx / total_jobs) * 100
                job_end_pct = ((job_idx + 1) / total_jobs) * 100
                prefix = f"[{job_idx + 1}/{total_jobs}]"

                encode_executor = None
                conversion_success = False
                all_wav = []
                all_enc = []
                try:
                    selected_chapters = [
                        job.chapters[i]
                        for i in job.selected_chapter_indices]
                    voice = deemojify_voice(job.voice)
                    speed_val = job.speed
                    chapter_gap = float(job.chapter_gap)
                    set_gpu_acceleration(job.gpu_acceleration)

                    out_fmt = getattr(job, 'output_format', 'm4b')
                    enc_ext = _INTERMEDIATE_EXTS.get(out_fmt, '.m4a')
                    output_path = _resolve_collision(
                        Path(output_dir)
                        / (Path(job.file_path).stem + _final_ext(out_fmt)))
                    title = job.title
                    creator = job.author
                    chapter_titles = job.chapter_titles
                    chapter_num = job.starting_chapter

                    wav_dir = Path(job.file_path).parent
                    stem = safe_stem(Path(job.file_path).stem, wav_dir)

                    n_selected = len(selected_chapters)
                    all_wav = [
                        str(wav_dir / f'{stem}_chapter_{i}.wav')
                        for i in range(1, n_selected + 1)]
                    all_enc = [
                        str(wav_dir / f'{stem}_chapter_{i}_enc{enc_ext}')
                        for i in range(1, n_selected + 1)]

                    word_counts = [len(ch.extracted_text.split())
                                   for ch in selected_chapters]
                    total_words = sum(word_counts) or 1
                    eta_state = {'words_done': 0,
                                 'start_time': time.time(),
                                 'current_step': 0}
                    steps = n_selected + 1

                    # Prepare chapter texts (title/author prepend on ch 1)
                    chapter_texts = []
                    for i, chapter in enumerate(selected_chapters, start=1):
                        text = chapter.extracted_text
                        if i == 1 and job.read_title_author:
                            text = f"{title} by {creator}.\n{text}"
                        chapter_texts.append(text)

                    def _eta_str():
                        elapsed = time.time() - eta_state['start_time']
                        if eta_state['words_done'] <= 0 or elapsed <= 0:
                            return ''
                        wps = eta_state['words_done'] / elapsed
                        if wps <= 0:
                            return ''
                        remaining = (
                            (total_words - eta_state['words_done']) / wps)
                        if remaining >= 60:
                            return f" (~{int(remaining / 60)} min left)"
                        return f" (~{int(remaining)}s left)"

                    def _job_pct(frac):
                        return job_start_pct + frac * (
                            job_end_pct - job_start_pct)

                    def on_chapter_start(i, total, text, is_resume):
                        if is_resume:
                            set_bstat(
                                f"{prefix} {stem}: skipping ch {i} "
                                f"(already done)")
                        else:
                            set_bstat(
                                f"{prefix} {stem}: ch {i}/{total}"
                                f"{_eta_str()}")

                    def on_segment_cb(i, seg_count, est_segs):
                        cs = eta_state['current_step']
                        ch_s = _job_pct(cs / steps)
                        ch_e = _job_pct((cs + 1) / steps)
                        frac = min(seg_count / est_segs, 0.95)
                        set_bprog(ch_s + frac * (ch_e - ch_s))

                    def on_chapter_done(i, duration):
                        eta_state['words_done'] += word_counts[i - 1]
                        eta_state['current_step'] += 1
                        set_bprog(_job_pct(
                            eta_state['current_step'] / steps))

                    def on_chapter_error(i, exc):
                        print(f"{stem} ch {i} failed: {exc}",
                              file=sys.stderr)
                        eta_state['words_done'] += word_counts[i - 1]
                        eta_state['current_step'] += 1

                    encode_executor = ThreadPoolExecutor(max_workers=1)
                    result = convert_chapters_to_wav(
                        chapter_texts, voice, speed_val, wav_dir,
                        stem, encode_executor,
                        out_format=out_fmt,
                        bitrate=job.bitrate,
                        vbr=job.vbr,
                        chapter_gap=chapter_gap,
                        substitutions=get_substitutions(),
                        heteronyms=prefs['heteronyms'].get(),
                        contractions=prefs['contractions'].get(),
                        resume=True,
                        cancel_check=batch_cancel.is_set,
                        on_chapter_start=on_chapter_start,
                        on_segment=on_segment_cb,
                        on_chapter_done=on_chapter_done,
                        on_chapter_error=on_chapter_error)
                    wav_files = result['wav_files']
                    encode_futures = result['encode_futures']
                    # Replace the pre-computed guess with the real enc paths
                    # the engine actually produced (handles format mismatch
                    # and partial conversions).
                    all_enc = [enc_name
                               for _, enc_name in encode_futures.values()]

                    if result['cancelled']:
                        job.status = "Cancelled"
                        safe_after(0, refresh_treeview)
                        continue

                    if not wav_files:
                        job.status = "Failed"
                        jobs_failed += 1
                        failed_jobs.append(
                            (job.title, "No chapters converted"))
                        safe_after(0, refresh_treeview)
                        continue

                    set_bstat(
                        f"{prefix} {stem}: assembling {out_fmt}...")
                    enc_files = []
                    for wn in wav_files:
                        future, enc_name = encode_futures[wn]
                        future.result()
                        enc_files.append(enc_name)

                    converted_titles = []
                    for ci, ch in enumerate(selected_chapters):
                        wn = str(
                            wav_dir
                            / f'{stem}_chapter_{ci + 1}.wav')
                        if (wn in wav_files
                                and chapter_titles is not None):
                            converted_titles.append(
                                chapter_titles[ci])

                    if job.file_path.lower().endswith('.pdf'):
                        cover_full = None
                    else:
                        cover_full = get_cover_image(job.book, False)

                    def assembly_prog(pct, s=job_start_pct,
                                      e=job_end_pct):
                        set_bprog(s + (pct / 100) * (e - s))

                    if out_fmt == 'm4b':
                        create_m4b(
                            enc_files, output_path, cover_full,
                            title, creator, chapter_num,
                            converted_titles or None,
                            progress_callback=assembly_prog,
                            preencoded=True,
                            bitrate=job.bitrate,
                            vbr=job.vbr)
                    else:
                        concat_audio_files(
                            enc_files, output_path,
                            progress_callback=assembly_prog)

                    job.status = "Done"
                    jobs_completed += 1
                    conversion_success = True

                except Exception as e:
                    job.status = f"Failed"
                    jobs_failed += 1
                    failed_jobs.append((job.title, str(e)))
                    print(f"Batch failed: {job.file_path}: {e}",
                          file=sys.stderr)
                finally:
                    if encode_executor is not None:
                        encode_executor.shutdown(wait=True)
                    # Mirror main-conversion cleanup policy: keep WAVs on
                    # cancel/failure so resume works on the next run;
                    # always remove encoded intermediates.
                    if conversion_success and all_wav:
                        time.sleep(2)
                        _cleanup_files(all_wav)
                    if all_enc:
                        _cleanup_files(all_enc)
                    safe_after(0, refresh_treeview)

            batch_cancel.clear()

            def show_done():
                if not bw.winfo_exists():
                    return
                set_bprog(100)
                start_btn.configure(state='normal')
                cancel_btn.configure(state='disabled')
                msg = (f"Completed: {jobs_completed}\n"
                       f"Failed: {jobs_failed}")
                if failed_jobs:
                    msg += "\n\nFailed:"
                    for name, err in failed_jobs:
                        msg += f"\n  - {name}: {err}"
                if jobs_failed > 0:
                    messagebox.showwarning("Batch Complete", msg,
                                           parent=bw)
                else:
                    messagebox.showinfo("Batch Complete", msg,
                                        parent=bw)
                for j in list(batch_queue):
                    if j.status == "Done":
                        batch_queue.remove(j)
                refresh_treeview()

            safe_after(0, show_done)

        def _batch_with_sleep_prevention():
            with prevent_sleep():
                run()
        threading.Thread(target=_batch_with_sleep_prevention,
                         daemon=True).start()

    def cancel_batch():
        batch_cancel.set()
        batch_status.config(text="Cancelling...")

    def on_close():
        """Close-window handler. Signals cancel to any running worker so
        its pending after() callbacks become no-ops via safe_after(), then
        destroys the window."""
        batch_cancel.set()
        bw.destroy()

    bw.protocol("WM_DELETE_WINDOW", on_close)

    start_btn = ttk.Button(action_frame, text='Start Batch',
                           command=start_batch)
    start_btn.pack(side=tk.RIGHT, padx=5)
    cancel_btn = ttk.Button(action_frame, text='Cancel',
                            command=cancel_batch, state='disabled')
    cancel_btn.pack(side=tk.RIGHT, padx=5)
