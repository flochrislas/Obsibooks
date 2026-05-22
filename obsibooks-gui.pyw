import json
import tkinter
import tkinter.messagebox

try:
    import customtkinter as ctk
    import queue
    import sys
    import threading
    from pathlib import Path
    import tkinter.filedialog as fd

    sys.path.insert(0, str(Path(__file__).parent))
    from obsibooks import run_pipeline
    from pepub import sanitize_filename

except Exception as _import_error:
    _root = tkinter.Tk()
    _root.withdraw()
    tkinter.messagebox.showerror('Import Error', str(_import_error))
    raise SystemExit(1)

ctk.set_appearance_mode('system')
ctk.set_default_color_theme('blue')

RECENT_FILE = Path.home() / '.obsibooks' / 'recent.json'
RECENT_LIMIT = 10


class StreamToQueue:
    """Redirects print() calls into a queue for thread-safe GUI updates."""

    def __init__(self, q):
        self.q = q

    def write(self, text):
        if text:
            self.q.put(text)

    def flush(self):
        pass

    def isatty(self):
        # pepub/pepdf batch reports probe this to decide whether to emit ANSI
        # color codes. The GUI textbox doesn't render them, so say False.
        return False


class App(ctk.CTk):
    def __init__(self):
        super().__init__()
        self.title('Obsibooks — EPUB & PDF to Obsidian')
        self.geometry('720x600')
        self.minsize(560, 480)
        self.log_queue = queue.Queue()
        self.recent = self._load_recent()
        self._build_ui()
        self._poll_log_queue()

    def _build_ui(self):
        self.grid_columnconfigure(0, weight=1)
        self.grid_rowconfigure(5, weight=1)

        # Row 0 — Input
        path_frame = ctk.CTkFrame(self, fg_color='transparent')
        path_frame.grid(row=0, column=0, padx=16, pady=(16, 4), sticky='ew')
        path_frame.grid_columnconfigure(1, weight=1)

        ctk.CTkLabel(path_frame, text='Input:', width=64, anchor='w').grid(
            row=0, column=0, padx=(0, 4))
        self.path_var = ctk.StringVar()
        # ComboBox instead of Entry: the dropdown arrow exposes the recent-paths
        # history (populated on each successful Convert run).
        self.path_combo = ctk.CTkComboBox(
            path_frame, variable=self.path_var,
            values=self.recent['input_paths'] or [''],
        )
        self.path_combo.set('')  # start blank regardless of history
        self.path_combo.grid(row=0, column=1, padx=(0, 8), sticky='ew')
        ctk.CTkButton(path_frame, text='File', width=64,
                      command=self._browse_file).grid(row=0, column=2, padx=(0, 4))
        ctk.CTkButton(path_frame, text='Folder', width=72,
                      command=self._browse_folder).grid(row=0, column=3)

        # Row 1 — Output
        out_frame = ctk.CTkFrame(self, fg_color='transparent')
        out_frame.grid(row=1, column=0, padx=16, pady=4, sticky='ew')
        out_frame.grid_columnconfigure(1, weight=1)

        ctk.CTkLabel(out_frame, text='Output:', width=64, anchor='w').grid(
            row=0, column=0, padx=(0, 4))
        self.output_var = ctk.StringVar()
        self.output_combo = ctk.CTkComboBox(
            out_frame, variable=self.output_var,
            values=self.recent['output_paths'] or [''],
        )
        self.output_combo.set('')  # empty by default → "next to each source"
        self.output_combo.grid(row=0, column=1, padx=(0, 8), sticky='ew')
        ctk.CTkButton(out_frame, text='Folder', width=72,
                      command=self._browse_output).grid(row=0, column=2)

        # Row 2 — Conversion options
        conv_frame = ctk.CTkFrame(self, fg_color='transparent')
        conv_frame.grid(row=2, column=0, padx=16, pady=4, sticky='ew')

        self.overwrite_var = ctk.BooleanVar(value=False)
        self.do_epub_var = ctk.BooleanVar(value=True)
        self.do_pdf_var = ctk.BooleanVar(value=True)
        ctk.CTkCheckBox(conv_frame, text='Overwrite',
                        variable=self.overwrite_var,
                        command=self._refresh_preview).grid(row=0, column=0, padx=(0, 16), sticky='w')
        ctk.CTkCheckBox(conv_frame, text='Convert EPUBs',
                        variable=self.do_epub_var,
                        command=self._refresh_preview).grid(row=0, column=1, padx=(0, 16), sticky='w')
        ctk.CTkCheckBox(conv_frame, text='Convert PDFs',
                        variable=self.do_pdf_var,
                        command=self._refresh_preview).grid(row=0, column=2, padx=(0, 16), sticky='w')

        # Row 3 — Compression options
        comp_frame = ctk.CTkFrame(self)
        comp_frame.grid(row=3, column=0, padx=16, pady=4, sticky='ew')
        comp_frame.grid_columnconfigure(0, weight=1)

        toggles = ctk.CTkFrame(comp_frame, fg_color='transparent')
        toggles.grid(row=0, column=0, padx=8, pady=(8, 0), sticky='ew')

        self.compress_var = ctk.BooleanVar(value=False)
        ctk.CTkCheckBox(toggles, text='Compress images after conversion',
                        variable=self.compress_var,
                        command=self._on_compress_toggle).grid(row=0, column=0, padx=(0, 16), sticky='w')
        self.dry_run_var = ctk.BooleanVar(value=False)
        self.dry_run_box = ctk.CTkCheckBox(toggles, text='Dry run',
                                           variable=self.dry_run_var)
        self.dry_run_box.grid(row=0, column=1, padx=(0, 16), sticky='w')

        opts = ctk.CTkFrame(comp_frame, fg_color='transparent')
        opts.grid(row=1, column=0, padx=8, pady=(4, 8), sticky='ew')
        for c in range(8):
            opts.grid_columnconfigure(c, weight=0)

        ctk.CTkLabel(opts, text='Max kB:').grid(row=0, column=0, padx=(0, 4), sticky='w')
        self.max_kb_var = ctk.StringVar(value='500')
        self.max_kb_entry = ctk.CTkEntry(opts, width=70, textvariable=self.max_kb_var)
        self.max_kb_entry.grid(row=0, column=1, padx=(0, 12))

        ctk.CTkLabel(opts, text='Max width:').grid(row=0, column=2, padx=(0, 4), sticky='w')
        self.max_w_var = ctk.StringVar(value='1024')
        self.max_w_entry = ctk.CTkEntry(opts, width=70, textvariable=self.max_w_var)
        self.max_w_entry.grid(row=0, column=3, padx=(0, 12))

        ctk.CTkLabel(opts, text='Max height:').grid(row=0, column=4, padx=(0, 4), sticky='w')
        self.max_h_var = ctk.StringVar(value='1024')
        self.max_h_entry = ctk.CTkEntry(opts, width=70, textvariable=self.max_h_var)
        self.max_h_entry.grid(row=0, column=5, padx=(0, 12))

        ctk.CTkLabel(opts, text='Quality:').grid(row=0, column=6, padx=(0, 4), sticky='w')
        self.quality_var = ctk.StringVar(value='85')
        self.quality_entry = ctk.CTkEntry(opts, width=60, textvariable=self.quality_var)
        self.quality_entry.grid(row=0, column=7)

        # Row 4 — Convert button
        btn_frame = ctk.CTkFrame(self, fg_color='transparent')
        btn_frame.grid(row=4, column=0, padx=16, pady=(8, 4), sticky='ew')
        btn_frame.grid_columnconfigure(0, weight=1)
        self.convert_btn = ctk.CTkButton(
            btn_frame, text='Convert', width=140, height=36,
            command=self._start
        )
        self.convert_btn.grid(row=0, column=0, sticky='e')

        # Row 5 — Shared preview/log area
        self.log_box = ctk.CTkTextbox(self, state='disabled', wrap='word')
        self.log_box.grid(row=5, column=0, padx=16, pady=(4, 16), sticky='nsew')

        self._converting = False
        self.path_var.trace_add('write', lambda *_: self._refresh_preview())
        self.output_var.trace_add('write', lambda *_: self._refresh_preview())
        self._on_compress_toggle()
        self._refresh_preview()

    # ─── Browse buttons ──────────────────────────────────────────────
    def _browse_file(self):
        path = fd.askopenfilename(
            title='Select EPUB or PDF file',
            filetypes=[('Ebook files', '*.epub *.pdf'),
                       ('EPUB files', '*.epub'),
                       ('PDF files', '*.pdf'),
                       ('All files', '*.*')]
        )
        if path:
            self.path_var.set(path)

    def _browse_folder(self):
        path = fd.askdirectory(title='Select folder containing EPUBs and/or PDFs')
        if path:
            self.path_var.set(path)

    def _browse_output(self):
        path = fd.askdirectory(title='Select output directory')
        if path:
            self.output_var.set(path)

    # ─── Compression options enable/disable ─────────────────────────
    def _on_compress_toggle(self):
        state = 'normal' if self.compress_var.get() else 'disabled'
        for w in (self.max_kb_entry, self.max_w_entry, self.max_h_entry,
                  self.quality_entry, self.dry_run_box):
            w.configure(state=state)
        self._refresh_preview()

    # ─── Preview ─────────────────────────────────────────────────────
    def _refresh_preview(self):
        if self._converting:
            return

        raw = self.path_var.get().strip()
        self.log_box.configure(state='normal')
        self.log_box.delete('1.0', 'end')

        if not raw:
            self.log_box.insert('end', (
                'Welcome to Obsibooks — EPUB & PDF to Obsidian.\n'
                '\n'
                'This tool converts EPUB and PDF books into folders of\n'
                'Markdown files that can be opened as an Obsidian vault\n'
                '(one file per chapter, images copied to assets/, and an\n'
                'index file with a TOC). Optionally, all vault images can\n'
                'be re-compressed to WebP afterwards.\n'
                '\n'
                'How to use:\n'
                '  1. Input (required): "File" picks a single .epub or .pdf,\n'
                '     "Folder" batch-converts every book it contains.\n'
                '  2. Output (optional): where converted books are written.\n'
                '     Empty = next to each source file.\n'
                '  3. Convert options: untick "Convert EPUBs" or "Convert\n'
                '     PDFs" to skip one format. "Overwrite" re-converts books\n'
                '     whose output folder already exists.\n'
                '  4. Compress images (optional): converts oversized images\n'
                '     in the vault to WebP and rewrites markdown refs.\n'
                '  5. Click "Convert".\n'
                '\n'
                'Requires pandoc (for EPUB) and Pillow (for compression).\n'
            ))
            self.log_box.configure(state='disabled')
            return

        target = Path(raw)
        if not target.exists():
            self.log_box.insert('end', f'Path not found: {target}\n')
            self.log_box.configure(state='disabled')
            return

        overwrite = self.overwrite_var.get()
        out_raw = self.output_var.get().strip()
        existing = set()
        if out_raw:
            out_path = Path(out_raw)
            if out_path.is_dir():
                existing = {p.name for p in out_path.iterdir() if p.is_dir()}

        def _will_skip(src_path):
            if overwrite:
                return False
            folder_name = sanitize_filename(src_path.stem)
            if out_raw:
                return folder_name in existing
            return (src_path.parent / folder_name).is_dir()

        do_epub = self.do_epub_var.get()
        do_pdf = self.do_pdf_var.get()

        if target.is_file():
            suffix = target.suffix.lower()
            if suffix == '.epub':
                if not do_epub:
                    self.log_box.insert('end',
                        'This is an EPUB but "Convert EPUBs" is unchecked.\n')
                elif _will_skip(target):
                    self.log_box.insert('end',
                        f'{target.name} — already converted (will be skipped).\n'
                        'Tick "Overwrite" to re-convert it.\n')
                else:
                    self.log_box.insert('end', f'1 EPUB file selected:\n{target.name}\n')
            elif suffix == '.pdf':
                if not do_pdf:
                    self.log_box.insert('end',
                        'This is a PDF but "Convert PDFs" is unchecked.\n')
                elif _will_skip(target):
                    self.log_box.insert('end',
                        f'{target.name} — already converted (will be skipped).\n'
                        'Tick "Overwrite" to re-convert it.\n')
                else:
                    self.log_box.insert('end', f'1 PDF file selected:\n{target.name}\n')
            else:
                self.log_box.insert('end', 'Selected file is not an EPUB or PDF.\n')
        elif target.is_dir():
            epubs = sorted(target.glob('*.epub')) if do_epub else []
            pdfs = sorted(target.glob('*.pdf')) if do_pdf else []
            total = len(epubs) + len(pdfs)
            if total == 0:
                kinds = []
                if do_epub:
                    kinds.append('EPUB')
                if do_pdf:
                    kinds.append('PDF')
                kind_str = ' or '.join(kinds) if kinds else 'EPUB or PDF (both unchecked)'
                self.log_box.insert('end', f'No {kind_str} files found in: {target}\n')
            else:
                to_convert_epubs = [p for p in epubs if not _will_skip(p)]
                to_convert_pdfs = [p for p in pdfs if not _will_skip(p)]
                will_do = len(to_convert_epubs) + len(to_convert_pdfs)
                skipped = total - will_do
                if skipped:
                    header = (
                        f'{will_do} of {total} file(s) will be converted '
                        f'({skipped} already converted, skipped).\n'
                        f'Input: {target}\n'
                    )
                else:
                    header = f'{total} file(s) will be converted from: {target}\n'
                self.log_box.insert('end', header + '\n')
                if to_convert_epubs:
                    self.log_box.insert('end',
                        f'EPUBs ({len(to_convert_epubs)}):\n' +
                        '\n'.join('  ' + p.name for p in to_convert_epubs) + '\n')
                if to_convert_pdfs:
                    if to_convert_epubs:
                        self.log_box.insert('end', '\n')
                    self.log_box.insert('end',
                        f'PDFs ({len(to_convert_pdfs)}):\n' +
                        '\n'.join('  ' + p.name for p in to_convert_pdfs) + '\n')
                if not (to_convert_epubs or to_convert_pdfs):
                    self.log_box.insert('end',
                        'Nothing to do — every book in this folder has already '
                        'been converted.\nTick "Overwrite" to re-convert them.\n')
        else:
            self.log_box.insert('end', f'Unsupported path: {target}\n')

        if self.compress_var.get():
            self.log_box.insert('end',
                '\n→ Images will be compressed after conversion '
                f'(max {self.max_kb_var.get()} kB, '
                f'{self.max_w_var.get()}×{self.max_h_var.get()} px, '
                f'q={self.quality_var.get()}'
                f'{", dry run" if self.dry_run_var.get() else ""}).\n')

        self.log_box.configure(state='disabled')

    # ─── Logging ─────────────────────────────────────────────────────
    def _append_log(self, text):
        self.log_box.configure(state='normal')
        self.log_box.insert('end', text)
        self.log_box.see('end')
        self.log_box.configure(state='disabled')

    def _poll_log_queue(self):
        while True:
            try:
                self._append_log(self.log_queue.get_nowait())
            except queue.Empty:
                break
        self.after(50, self._poll_log_queue)

    # ─── Convert ─────────────────────────────────────────────────────
    def _parse_int(self, var, name, minimum=1, maximum=None):
        try:
            n = int(var.get().strip())
        except ValueError:
            raise ValueError(f'{name} must be an integer')
        if n < minimum or (maximum is not None and n > maximum):
            rng = f'{minimum}' if maximum is None else f'{minimum}-{maximum}'
            raise ValueError(f'{name} must be in range {rng}')
        return n

    def _start(self):
        path = self.path_var.get().strip()
        if not path:
            self._append_log('Please select an EPUB/PDF file or folder first.\n')
            return

        do_epub = self.do_epub_var.get()
        do_pdf = self.do_pdf_var.get()
        if not (do_epub or do_pdf):
            self._append_log('Nothing to do — both "Convert EPUBs" and '
                             '"Convert PDFs" are unchecked.\n')
            return

        compress = self.compress_var.get()
        try:
            max_kb = self._parse_int(self.max_kb_var, 'Max kB') if compress else 500
            max_w = self._parse_int(self.max_w_var, 'Max width') if compress else 1024
            max_h = self._parse_int(self.max_h_var, 'Max height') if compress else 1024
            quality = self._parse_int(self.quality_var, 'Quality', 1, 100) if compress else 85
        except ValueError as e:
            self._append_log(f'Error: {e}.\n')
            return

        output_dir = self.output_var.get().strip() or None

        self._converting = True
        self.convert_btn.configure(state='disabled', text='Converting...')
        self.log_box.configure(state='normal')
        self.log_box.delete('1.0', 'end')
        self.log_box.configure(state='disabled')

        threading.Thread(
            target=self._run,
            kwargs=dict(
                path=path,
                output_dir=output_dir,
                overwrite=self.overwrite_var.get(),
                do_epub=do_epub,
                do_pdf=do_pdf,
                compress=compress,
                max_kb=max_kb,
                max_width=max_w,
                max_height=max_h,
                quality=quality,
                dry_run=self.dry_run_var.get(),
            ),
            daemon=True,
        ).start()

    def _run(self, **kwargs):
        stream = StreamToQueue(self.log_queue)
        old_stdout, old_stderr = sys.stdout, sys.stderr
        sys.stdout = sys.stderr = stream
        input_path = kwargs.get('path', '')
        output_dir = kwargs.get('output_dir') or ''
        succeeded = False
        try:
            run_pipeline(
                Path(kwargs.pop('path')),
                output_dir=Path(kwargs['output_dir']) if kwargs.get('output_dir') else None,
                **{k: v for k, v in kwargs.items() if k != 'output_dir'},
            )
            succeeded = True
        except SystemExit as e:
            # run_pipeline uses SystemExit for user-facing prereq / path errors;
            # surface the message in the log rather than letting it kill the thread silently.
            print(str(e))
        except Exception as e:
            print(f'Error: {e}')
        finally:
            sys.stdout = old_stdout
            sys.stderr = old_stderr
            self.after(0, lambda: self._on_done(succeeded, input_path, output_dir))

    def _on_done(self, succeeded=False, input_path='', output_dir=''):
        self._converting = False
        self.convert_btn.configure(state='normal', text='Convert')
        self._append_log('\n--- Done ---\n')
        if succeeded:
            if input_path:
                self._commit_to_history('input_paths', input_path)
            if output_dir:
                self._commit_to_history('output_paths', output_dir)

    # ─── Recent-paths history ────────────────────────────────────────
    def _load_recent(self):
        """Read ~/.obsibooks/recent.json. Returns empty lists on any failure."""
        empty = {'input_paths': [], 'output_paths': []}
        try:
            with open(RECENT_FILE, 'r', encoding='utf-8') as f:
                data = json.load(f)
            if not isinstance(data, dict):
                return empty
            return {
                'input_paths':  [str(p) for p in data.get('input_paths',  [])][:RECENT_LIMIT],
                'output_paths': [str(p) for p in data.get('output_paths', [])][:RECENT_LIMIT],
            }
        except (FileNotFoundError, json.JSONDecodeError, OSError):
            return empty

    def _save_recent(self):
        """Best-effort persist; history is a nice-to-have, never block on failure."""
        try:
            RECENT_FILE.parent.mkdir(parents=True, exist_ok=True)
            with open(RECENT_FILE, 'w', encoding='utf-8') as f:
                json.dump(self.recent, f, indent=2, ensure_ascii=False)
        except OSError:
            pass

    def _commit_to_history(self, field, path):
        """Move `path` to position 1 of the named history list (dedupe, cap, save).

        Refreshes the matching ComboBox's `values` so the new entry is
        available immediately in the dropdown.
        """
        if not path:
            return
        lst = [p for p in self.recent.get(field, []) if p != path]
        lst.insert(0, path)
        lst = lst[:RECENT_LIMIT]
        self.recent[field] = lst
        self._save_recent()
        combo = self.path_combo if field == 'input_paths' else self.output_combo
        # CTkComboBox needs at least one value or the dropdown hides; pad with ''.
        combo.configure(values=lst or [''])


if __name__ == '__main__':
    try:
        app = App()
        app.mainloop()
    except Exception as e:
        tkinter.messagebox.showerror('Startup Error', str(e))
        raise
