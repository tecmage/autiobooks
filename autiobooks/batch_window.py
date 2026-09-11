import sys
import threading
import time
import traceback
import tkinter as tk
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from tkinter import filedialog, font as tkfont, messagebox, ttk

from .engine import (
    _INTERMEDIATE_EXTS,
    assemble_output,
    convert_chapters_to_wav,
    end_conversion,
    find_chapter_wavs,
    safe_stem,
    set_gpu_acceleration,
    try_begin_conversion,
    unlink_with_retry,
)
from .epub_parser import get_cover_image
from .pdf_parser import get_pdf_cover_bytes
from .voices_lang import deemojify_voice


def _final_ext(fmt):
    return '.m4b' if fmt == 'm4b' else _INTERMEDIATE_EXTS.get(fmt, '.m4b')


# Run state shared across batch-window instances. A worker can outlive its
# window (closing the window cancels at the next chapter boundary, which
# can be minutes away on CPU) — a reopened window must adopt the live
# cancel event and running index, or its row gating and Cancel button
# would be wired to dead per-window objects.
_batch_cancel = threading.Event()
_current_running_idx = [-1]

# Serializes batch_queue mutation against the worker's claim-then-fetch:
# the main thread's _is_pending check and pop/swap were not atomic with the
# worker advancing _current_running_idx and fetching the job, so a Remove
# landing exactly at a job boundary could pop the job the worker had just
# committed to (and silently skip the job that shifted into its slot).
_queue_lock = threading.Lock()

# UI hooks of the CURRENTLY-live batch window. The worker posts updates
# through these instead of closing over its spawning window's widgets, so
# a window reopened mid-run (or during the cancel drain) keeps receiving
# status/progress/completion updates. Entries are replaced wholesale each
# time a window opens; safe_after guards against the window being gone.
_ui = {}

# The single live Batch Queue Toplevel, or None. show_batch_window() is a
# singleton: a second call while one is already open just raises the
# existing window instead of creating a competitor that steals `_ui` out
# from under it and can cancel a run it doesn't own (see on_close).
_window = None


def _ui_post(name, *args):
    """Post a named UI update to whichever batch window is currently live.

    No-op when no window has registered or the registered window has been
    destroyed (its safe_after checks winfo_exists)."""
    sa = _ui.get('safe_after')
    fn = _ui.get(name)
    if sa is None or fn is None:
        return
    sa(0, lambda f=fn, a=args: f(*a))


def cancel_active_batch():
    """Signal a running batch worker to stop at the next chapter boundary.

    Called from the main window's quit path — its own cancel_event is for
    the GUI conversion only and a batch worker never polls it.
    """
    _batch_cancel.set()


def show_batch_window(
    parent,
    batch_queue,
    initial_dir,
    prevent_sleep,
    prefs,
    get_substitutions,
    get_phoneme_overrides=None,
    get_auto_acronyms=None,
    is_preview_active=None,
):
    """Open the batch queue window.

    batch_queue: shared mutable list of BatchJob objects.
    initial_dir: default output directory string.
    prevent_sleep: context manager that inhibits OS sleep during run.
    prefs: dict with tk BooleanVars — 'heteronyms', 'contractions'.
    get_substitutions: callable returning the current word substitutions list.
    get_phoneme_overrides: callable returning the current phoneme override list.
    get_auto_acronyms: callable returning the current auto-acronym bool.
    """
    global _window
    if _window is not None:
        try:
            if _window.winfo_exists():
                # Already open (possibly mid-run) — raise it instead of
                # opening a second Toplevel. A second window used to steal
                # `_ui` wholesale (window A's progress/status/completion
                # updates then went nowhere) and could cancel a run window
                # A started the moment it was closed.
                _window.deiconify()
                _window.lift()
                _window.focus_force()
                return
        except tk.TclError:
            pass
        _window = None

    if not batch_queue:
        messagebox.showinfo(
            "Batch Queue",
            "The batch queue is empty.\n\n"
            "Load an epub, configure chapters/settings,\n"
            "then click 'Add to Batch'.")
        return

    bw = tk.Toplevel(parent)
    _window = bw
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

    last_queue_snapshot = [()]
    # -1 when no batch is running; otherwise the index in batch_queue of the
    # job currently being processed. Used to gate move/remove on pending
    # rows only and to visually mark the running row. Module-level so a
    # window reopened during a cancel drain sees the live run.
    current_running_idx = _current_running_idx

    _default_font = tkfont.nametofont('TkDefaultFont')
    _running_font = tkfont.Font(family=_default_font.cget('family'),
                                size=_default_font.cget('size'),
                                weight='bold')
    tree.tag_configure('running', font=_running_font)

    def refresh_treeview():
        tree.delete(*tree.get_children())
        running = current_running_idx[0]
        for i, job in enumerate(batch_queue):
            sel = len(job.selected_chapter_indices)
            total = len([c for c in job.chapters
                         if len(c.extracted_text.split()) > 0])
            fmt = getattr(job, 'output_format', 'm4b').upper()
            num_label = f"▶ {i + 1}" if i == running else str(i + 1)
            tags = ('running',) if i == running else ()
            tree.insert('', 'end', tags=tags, values=(
                num_label, job.title, job.voice, fmt,
                _format_bitrate(job),
                f"{sel}/{total}",
                f"{job.total_words:,}",
                job.status))
        last_queue_snapshot[0] = tuple(
            (id(j), j.status) for j in batch_queue)

    refresh_treeview()

    # Poll for external mutations (e.g. main window's "Add to Batch" while
    # this window is open). The queue is shared mutable state with no
    # notify channel; the snapshot includes per-job status so a window
    # reopened mid-run re-renders as the worker advances, not only when
    # queue membership changes.
    def poll_queue_changes():
        if not bw.winfo_exists():
            return
        snap = tuple((id(j), j.status) for j in batch_queue)
        if snap != last_queue_snapshot[0]:
            refresh_treeview()
        bw.after(500, poll_queue_changes)

    bw.after(500, poll_queue_changes)

    btn_frame = tk.Frame(bw)
    btn_frame.pack(fill=tk.X, padx=10, pady=5)

    def _is_pending(idx):
        """True if idx points to a job that hasn't started yet — i.e. past
        the currently-running job, or there's no run in progress."""
        return idx > current_running_idx[0]

    def move_up():
        sel = tree.selection()
        if not sel:
            return
        idx = tree.index(sel[0])
        # Need both source and target (idx-1) to be pending. When running,
        # that means idx must be at least 2 past the running index. The
        # lock makes the pending-check + swap atomic against the worker's
        # claim-then-fetch at a job boundary.
        with _queue_lock:
            if idx <= 0 or not _is_pending(idx) or not _is_pending(idx - 1):
                return
            batch_queue[idx], batch_queue[idx - 1] = (
                batch_queue[idx - 1], batch_queue[idx])
        refresh_treeview()
        tree.selection_set(tree.get_children()[idx - 1])

    def move_down():
        sel = tree.selection()
        if not sel:
            return
        idx = tree.index(sel[0])
        with _queue_lock:
            if idx >= len(batch_queue) - 1 or not _is_pending(idx):
                return
            batch_queue[idx], batch_queue[idx + 1] = (
                batch_queue[idx + 1], batch_queue[idx])
        refresh_treeview()
        tree.selection_set(tree.get_children()[idx + 1])

    def remove_selected():
        sel = tree.selection()
        if not sel:
            return
        idx = tree.index(sel[0])
        with _queue_lock:
            if not _is_pending(idx) or idx >= len(batch_queue):
                return
            batch_queue.pop(idx)
        refresh_treeview()
        if not batch_queue and current_running_idx[0] < 0:
            bw.destroy()

    def clear_all_jobs():
        if current_running_idx[0] >= 0:
            # Mid-run: only pending jobs (past the running one) are removable.
            pending_count = len(batch_queue) - (current_running_idx[0] + 1)
            if pending_count <= 0:
                return
            if messagebox.askyesno(
                    "Clear Pending",
                    f"Remove {pending_count} pending job(s) from the queue?\n"
                    "The currently-running job will continue.",
                    parent=bw):
                with _queue_lock:
                    del batch_queue[current_running_idx[0] + 1:]
                refresh_treeview()
            return
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

    batch_cancel = _batch_cancel

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

        # One conversion at a time process-wide: the main-window convert flow
        # and a still-finishing batch worker (this window may have been
        # closed and reopened mid-run) share global GPU state and WAV paths.
        if is_preview_active and is_preview_active():
            # The preview thread is inside the shared TTS pipeline; the
            # batch worker's set_gpu_acceleration() would flip the
            # process-global torch device under it.
            messagebox.showwarning(
                "Warning",
                "Wait for the chapter preview to finish generating first.",
                parent=bw)
            return

        # Jobs left at "Done" by a run whose window was closed before the
        # completion summary could prune them: converting them again would
        # re-TTS from scratch (WAVs are cleaned on success) and overwrite
        # the previous output with regenerated content. Prune them now.
        # "Done (N ch failed)" jobs are kept — re-running those is a retry.
        with _queue_lock:
            leftover_done = [j for j in batch_queue if j.status == "Done"]
            for j in leftover_done:
                batch_queue.remove(j)
        if leftover_done:
            refresh_treeview()
        if not batch_queue:
            messagebox.showinfo(
                "Batch Queue",
                "All queued jobs were already converted.", parent=bw)
            return

        if not try_begin_conversion():
            messagebox.showwarning(
                "Warning",
                "Another conversion is already running (main window or a "
                "previous batch still finishing). Wait for it to complete.",
                parent=bw)
            return

        # Everything between the slot claim above and the worker spawn at
        # the bottom runs under a try/except that releases the slot — an
        # exception here (a Tk read, a snapshot copy) would otherwise leak
        # the conversion lock for the rest of the session.
        try:
            start_btn.configure(state='disabled')
            cancel_btn.configure(state='normal')
            batch_cancel.clear()

            user_gpu_pref = None
            gpu_var = prefs.get('gpu_acceleration')
            if gpu_var is not None:
                try:
                    user_gpu_pref = bool(gpu_var.get())
                except tk.TclError:
                    user_gpu_pref = None

            # Snapshot Tk-held prefs and the substitution/override lists on
            # the main thread — the batch worker must not read Tk variables,
            # and a mid-run prefs change shouldn't alter later jobs.
            heteronyms_pref = bool(prefs['heteronyms'].get())
            contractions_pref = bool(prefs['contractions'].get())
            auto_acronyms_pref = bool(get_auto_acronyms()) if get_auto_acronyms else False
            subs_snapshot = [dict(s) for s in (get_substitutions() or [])]
            overrides_snapshot = ([dict(o) for o in (get_phoneme_overrides() or [])]
                                  if get_phoneme_overrides else None)
        except Exception:
            end_conversion()
            try:
                start_btn.configure(state='normal')
                cancel_btn.configure(state='disabled')
            except tk.TclError:
                pass
            raise

        def run():
            # All worker→UI traffic goes through _ui_post so a batch window
            # reopened mid-run (the module-level _ui holder then points at
            # the NEW window) keeps receiving updates — closures over this
            # window's widgets would go dark the moment it was closed.
            try:
                _run_body()
            except Exception as e:
                print(f"Batch run crashed: {e}", file=sys.stderr)
                traceback.print_exc()
                _ui_post('show_error', f"Batch run crashed:\n\n{e}")
            finally:
                current_running_idx[0] = -1
                if user_gpu_pref is not None:
                    try:
                        set_gpu_acceleration(user_gpu_pref)
                    except Exception as gpu_err:
                        print(f"Failed to restore GPU state: {gpu_err}",
                              file=sys.stderr)
                end_conversion()
                _ui_post('set_buttons', False)
                _ui_post('refresh')

        def _run_body():
            jobs_completed = 0
            jobs_failed = 0
            failed_jobs = []
            # Jobs that completed but with individual chapter failures —
            # the output exists yet is MISSING those chapters; stderr alone
            # is invisible in a windowed build, so these must reach the UI.
            chapter_failure_jobs = []
            used_output_paths = set()

            def _resolve_collision(path):
                """Return a path that doesn't collide with anything already
                claimed this batch. Appends ' (2)', ' (3)', ... before the
                extension when needed. Uses casefold for case-insensitive
                filesystems (Windows/macOS default).

                Deliberately does NOT check the filesystem: re-converting a
                book to the same directory overwrites the previous output,
                matching CLI -o semantics (user decision, audit §4.5)."""
                p = Path(path)
                candidate = p
                n = 2
                while str(candidate).casefold() in used_output_paths:
                    candidate = p.with_name(f'{p.stem} ({n}){p.suffix}')
                    n += 1
                used_output_paths.add(str(candidate).casefold())
                return str(candidate)

            def set_bprog(value):
                _ui_post('set_progress', value)

            def set_bstat(text):
                _ui_post('set_status', text)

            def _cleanup_files(paths):
                for p in paths:
                    err = unlink_with_retry(p)
                    if err is not None:
                        print(f'failed to delete {p}: {err}',
                              file=sys.stderr)

            job_idx = -1
            while True:
                # Claim the next slot BEFORE fetching the job: the main
                # thread's _is_pending gating treats idx > current_running_idx
                # as removable, so advancing the index first closes the window
                # where Remove could pop a job the worker is committed to.
                # len() is re-read each iteration so jobs appended from the
                # main window mid-run are picked up and the [n/total] prefix
                # and percent math stay within bounds.
                with _queue_lock:
                    job_idx += 1
                    current_running_idx[0] = job_idx
                    if job_idx >= len(batch_queue):
                        break
                    job = batch_queue[job_idx]
                if job.status == "Done":
                    # Converted by an earlier run whose window closed before
                    # the summary pruned it — don't re-TTS and overwrite.
                    continue
                if batch_cancel.is_set():
                    job.status = "Cancelled"
                    _ui_post('refresh')
                    break

                job.status = "Converting"
                _ui_post('refresh')

                total_jobs = len(batch_queue)
                job_start_pct = (job_idx / total_jobs) * 100
                job_end_pct = ((job_idx + 1) / total_jobs) * 100
                prefix = f"[{job_idx + 1}/{total_jobs}]"

                encode_executor = None
                conversion_success = False
                all_enc = []
                chapter_errors = []
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
                    all_enc = [
                        str(wav_dir / f'{stem}_chapter_{i}_enc{enc_ext}')
                        for i in range(1, n_selected + 1)]

                    word_counts = [len(ch.extracted_text.split())
                                   for ch in selected_chapters]
                    total_words = sum(word_counts) or 1
                    eta_state = {'words_done': 0,
                                 'start_time': time.time(),
                                 'current_step': 0,
                                 'words_remaining': total_words}
                    steps = n_selected + 1
                    resumed_indices = set()

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
                        remaining = eta_state['words_remaining'] / wps
                        if remaining >= 60:
                            return f" (~{int(remaining / 60)} min left)"
                        return f" (~{int(remaining)}s left)"

                    def _job_pct(frac):
                        return job_start_pct + frac * (
                            job_end_pct - job_start_pct)

                    def on_chapter_start(i, total, text, is_resume):
                        if is_resume:
                            resumed_indices.add(i)
                            # Resumed-from-disk / duplicate-reuse chapters
                            # cost ~0 wall clock — drop them from the
                            # remaining-work total immediately so the
                            # words/sec rate isn't computed against
                            # instantaneous "free" progress.
                            eta_state['words_remaining'] -= word_counts[i - 1]
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
                        # Resumed/duplicate chapters already had their
                        # words dropped from words_remaining in
                        # on_chapter_start; crediting them here too would
                        # double count and re-poison the words/sec rate.
                        if i not in resumed_indices:
                            eta_state['words_done'] += word_counts[i - 1]
                            eta_state['words_remaining'] -= word_counts[i - 1]
                        eta_state['current_step'] += 1
                        set_bprog(_job_pct(
                            eta_state['current_step'] / steps))

                    def on_chapter_error(i, exc):
                        print(f"{stem} ch {i} failed: {exc}",
                              file=sys.stderr)
                        # Surfaced in the job status and completion summary
                        # — stderr is invisible in the windowed build, and a
                        # job with missing chapters must not read as a
                        # clean "Done".
                        chapter_errors.append((i, str(exc)))
                        eta_state['words_done'] += word_counts[i - 1]
                        eta_state['words_remaining'] -= word_counts[i - 1]
                        eta_state['current_step'] += 1
                        set_bprog(_job_pct(
                            eta_state['current_step'] / steps))

                    encode_executor = ThreadPoolExecutor(max_workers=1)
                    result = convert_chapters_to_wav(
                        chapter_texts, voice, speed_val, wav_dir,
                        stem, encode_executor,
                        out_format=out_fmt,
                        bitrate=job.bitrate,
                        vbr=job.vbr,
                        chapter_gap=chapter_gap,
                        substitutions=subs_snapshot,
                        phoneme_overrides=overrides_snapshot,
                        auto_acronyms=auto_acronyms_pref,
                        heteronyms=heteronyms_pref,
                        contractions=contractions_pref,
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
                        _ui_post('refresh')
                        continue

                    if not wav_files:
                        job.status = "Failed"
                        jobs_failed += 1
                        failed_jobs.append(
                            (job.title, "No chapters converted"))
                        _ui_post('refresh')
                        continue

                    set_bstat(
                        f"{prefix} {stem}: assembling {out_fmt}...")

                    if job.file_path.lower().endswith('.pdf'):
                        cover_full = get_pdf_cover_bytes(job.file_path)
                    else:
                        cover_full = get_cover_image(job.book, False)

                    def assembly_prog(pct):
                        # Map onto the reserved trailing 1/steps slot
                        # (steps = n_selected + 1) instead of the job's
                        # full [job_start_pct, job_end_pct] span — the
                        # latter made the bar snap backward from wherever
                        # the last chapter left off (up to
                        # _job_pct(n_selected/steps)) down to
                        # job_start_pct the instant assembly's first
                        # -progress line (pct=0) arrived.
                        set_bprog(_job_pct((steps - 1 + pct / 100) / steps))

                    assemble_output(result, chapter_texts, chapter_titles,
                                    stem, wav_dir, out_fmt, output_path,
                                    cover_full, title, creator,
                                    starting_chapter=chapter_num,
                                    bitrate=job.bitrate, vbr=job.vbr,
                                    progress_callback=assembly_prog)

                    if chapter_errors:
                        job.status = (
                            f"Done ({len(chapter_errors)} ch failed)")
                        chapter_failure_jobs.append(
                            (job.title, list(chapter_errors)))
                    else:
                        job.status = "Done"
                    jobs_completed += 1
                    conversion_success = True

                except Exception as e:
                    job.status = f"Failed: {e}"
                    jobs_failed += 1
                    failed_jobs.append((job.title, str(e)))
                    print(f"Batch failed: {job.file_path}: {e}",
                          file=sys.stderr)
                finally:
                    if encode_executor is not None:
                        encode_executor.shutdown(wait=True)
                    # Mirror main-conversion cleanup policy: keep WAVs on
                    # cancel/failure so resume works on the next run;
                    # always remove encoded intermediates. The sweep is
                    # stem-wide (any render_key) so orphans from a run
                    # cancelled under different settings are reclaimed too.
                    if conversion_success:
                        _cleanup_files(find_chapter_wavs(stem, wav_dir))
                    if all_enc:
                        _cleanup_files(all_enc)
                    _ui_post('refresh')

            batch_cancel.clear()
            current_running_idx[0] = -1

            msg = (f"Completed: {jobs_completed}\n"
                   f"Failed: {jobs_failed}")
            if chapter_failure_jobs:
                msg += "\n\nCompleted with MISSING chapters:"
                for name, errs in chapter_failure_jobs:
                    chs = ', '.join(str(i) for i, _ in errs)
                    msg += f"\n  - {name}: chapter(s) {chs}"
                    for i, err in errs[:3]:
                        msg += f"\n      ch {i}: {err}"
            if failed_jobs:
                msg += "\n\nFailed:"
                for name, err in failed_jobs:
                    msg += f"\n  - {name}: {err}"
            warn = jobs_failed > 0 or bool(chapter_failure_jobs)
            # Routed through the live-window holder: if the user closed and
            # reopened the window mid-run, the NEW window shows the summary
            # and prunes Done jobs. With no window open the summary is
            # skipped; leftover Done jobs are pruned at the next Start.
            _ui_post('run_finished', msg, warn)

        def _batch_with_sleep_prevention():
            with prevent_sleep():
                run()
        try:
            threading.Thread(target=_batch_with_sleep_prevention,
                             daemon=True).start()
        except Exception:
            # Slot claimed but no worker exists to release it in its
            # finally — release here or every later conversion is blocked.
            end_conversion()
            try:
                start_btn.configure(state='normal')
                cancel_btn.configure(state='disabled')
            except tk.TclError:
                pass
            raise

    def cancel_batch():
        batch_cancel.set()
        batch_status.config(text="Cancelling...")

    def on_close():
        """Close-window handler. Signals cancel to a running worker so its
        pending after() callbacks become no-ops via safe_after() — but only
        when THIS window's run is actually live. current_running_idx[0] is
        -1 whenever no run is in flight (never started, or finished/
        cancelled already reset it in the worker's finally), so a window
        that closes after its run ended — or, defensively, a second window
        that never should have opened at all now that show_batch_window is
        a singleton — can't cancel a run it doesn't own."""
        global _window
        if current_running_idx[0] >= 0:
            batch_cancel.set()
        if _window is bw:
            _window = None
        bw.destroy()

    bw.protocol("WM_DELETE_WINDOW", on_close)

    start_btn = ttk.Button(action_frame, text='Start Batch',
                           command=start_batch)
    start_btn.pack(side=tk.RIGHT, padx=5)
    cancel_btn = ttk.Button(action_frame, text='Cancel',
                            command=cancel_batch, state='disabled')
    cancel_btn.pack(side=tk.RIGHT, padx=5)

    def _set_buttons(running):
        start_btn.configure(state='disabled' if running else 'normal')
        cancel_btn.configure(state='normal' if running else 'disabled')

    def _run_finished(msg, warn):
        batch_progress.configure(value=100)
        _set_buttons(False)
        if warn:
            messagebox.showwarning("Batch Complete", msg, parent=bw)
        else:
            messagebox.showinfo("Batch Complete", msg, parent=bw)
        with _queue_lock:
            for j in [j for j in batch_queue if j.status == "Done"]:
                batch_queue.remove(j)
        refresh_treeview()

    def _show_error(msg):
        messagebox.showerror("Batch Error", msg, parent=bw)

    # Register this window as the live UI target for the (possibly
    # already-running) batch worker. Replaces any previous window's hooks —
    # this is what lets a window reopened mid-run receive status/progress/
    # completion updates instead of freezing at "Converting".
    _ui.update(
        safe_after=safe_after,
        refresh=refresh_treeview,
        set_progress=lambda v: batch_progress.configure(value=v),
        set_status=lambda t: batch_status.config(text=t),
        set_buttons=_set_buttons,
        run_finished=_run_finished,
        show_error=_show_error,
    )

    # Adopt a run already in flight (window was closed and reopened):
    # reflect the running state in the buttons and status line.
    if current_running_idx[0] >= 0:
        _set_buttons(True)
        batch_status.config(text="Batch running...")
