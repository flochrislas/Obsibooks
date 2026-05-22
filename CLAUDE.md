# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Project shape

Obsibooks is a unified CLI + GUI that converts EPUB and PDF ebooks to Obsidian-compatible markdown, then optionally compresses the resulting vault images to WebP. It consolidates three previously-separate scripts (`pepub.py`, `pepdf.py`, `compress_images.py`) behind a single entry point and a single GUI window.

## Running

**CLI:**
```bash
python obsibooks.py path/to/book.epub                       # single EPUB
python obsibooks.py path/to/book.pdf -d path/to/vault       # single PDF, explicit output
python obsibooks.py path/to/folder/ -d vault --overwrite    # batch both formats
python obsibooks.py path/to/folder/ -d vault --epub-only    # only EPUBs
python obsibooks.py path/to/folder/ -d vault --compress \
    --max-kb 300 --max-width 1280 --quality 80 --dry-run    # convert + compress preview
```

**GUI:**
```bash
pythonw obsibooks-gui.pyw    # or double-click obsibooks-gui.bat
```

## External requirements

- `pandoc` on PATH — required whenever an EPUB will be processed (pepub uses pypandoc).
- pip: `ebooklib`, `beautifulsoup4`, `lxml`, `pypandoc`, `pyyaml`, `pymupdf`, `pymupdf4llm`, `customtkinter`, `Pillow`.

## Layout

- `obsibooks.py` — public CLI + the single `run_pipeline()` entry point that both the CLI and the GUI call.
- `obsibooks-gui.pyw` — customtkinter GUI. `obsibooks-gui.bat` launches it with `pythonw`.
- `pepub.py` — EPUB→markdown conversion. Imported as a module; its own `python pepub.py …` CLI still works standalone.
- `pepdf.py` — PDF→markdown conversion. Same arrangement.
- `compress_images.py` — vault image compression (WebP). Same arrangement.

## Architecture

### Single orchestrator

`obsibooks.run_pipeline(input_path, *, output_dir, overwrite, do_epub, do_pdf, compress, max_kb, max_width, max_height, quality, dry_run)` is the only function the GUI and the CLI call. Anything new the GUI needs to do should go through `run_pipeline`, not its own batch loop — that's how CLI and GUI stay behaviorally identical.

`_run_batch` iterates a list of files, captures per-file stderr via a `_Tee`, and hands the result list to `pepub._print_batch_report` or `pepdf._print_batch_report` (imported under aliases `_epub_batch_report` / `_pdf_batch_report` to dodge the name clash).

**Vault resolution for compression:** `vault = output_dir if output_dir else (input_path if input_path.is_dir() else input_path.parent)`. Compression always operates wherever the just-converted books landed.

**Pre-flight checks** fire lazily, only for the path actually being taken: pandoc check when an EPUB is about to be processed (`_check_epub_prereqs`), fitz/pymupdf4llm when a PDF is (`_check_pdf_prereqs`), PIL when `--compress` is set (`_check_compress_prereqs`). This lets `--pdf-only` runs succeed on machines without pandoc/pypandoc.

**pepub import is wrapped** in try/except at obsibooks module load: pepub imports `ebooklib` and `pypandoc` unconditionally at module scope, so a missing install would otherwise break obsibooks even for PDF-only use. The import error is preserved and re-raised inside `_check_epub_prereqs`.

### EPUB and PDF conversion

`pepub.convert_epub(path, overwrite, output_base_dir)` and `pepdf.convert_pdf(path, overwrite, output_base_dir)` are the public entry points. Both return `'skipped'` when the output folder already exists and `overwrite=False`, otherwise None. Implementation details (TOC-driven slicing for pepub, pymupdf4llm extraction for pepdf) live in those modules' docstrings — don't duplicate them here.

Output folder name comes from the source file's stem (via `pepub.sanitize_filename`), not from `<dc:title>` metadata — rename the source file to control the output folder name. The GUI preview reuses `sanitize_filename` for its "already converted, will be skipped" check.

### Image compression (`compress_images.py`)

Scans `vault/*/assets/` for images exceeding `--max-kb` *or* `--max-width`/`--max-height`. Converts to WebP, rewrites markdown refs in the matching book folder, deletes originals on success.

- **`.png`** — tries lossless WebP first. If the result is still over `max_bytes` (= `--max-kb` × 1024), falls back to lossy at `--quality`. The size cap takes priority over preserving lossless quality.
- **`.jpg` / `.jpeg` / `.bmp` / `.tiff` / `.tif` / `.webp`** — always lossy WebP at `--quality` (default 85).
- **`.gif`** — intentionally excluded (animation would be lost).
- **Skip-if-no-saving guard:** if the encoded WebP isn't resized *and* it ended up ≥ the original size, the WebP is deleted and the original kept. Resized images are always kept, regardless of byte count — the goal there is dimensions.
- **`.webp` → `.webp` rename dance:** writes to `stem._tmp.webp`, renames on success, to avoid overwriting the source mid-read.

The PNG fallback is the only format-specific path. Even after lossy at `--quality` the file may still exceed `max_bytes` — there is no iterative quality reduction. Lower `--quality` on the CLI if that matters.

## GUI specifics

- Single column layout: Input row, Output row, conversion checkboxes (Overwrite / Convert EPUBs / Convert PDFs), compression frame (Compress toggle + dry-run + max-kb / max-width / max-height / quality entries), Convert button, shared preview/log textbox.
- Compression entries are greyed when "Compress images" is off (`_on_compress_toggle`).
- The textbox cycles through three states: **idle** (welcome text), **preview** (lists files that *will* be converted given current toggles, with already-converted ones filtered when Overwrite is off), **log** (live stdout/stderr during a run). A `_converting` flag suppresses preview refreshes mid-run.
- Worker thread redirects `sys.stdout` and `sys.stderr` through a `StreamToQueue`. `StreamToQueue.isatty()` returns `False` so the batch-report code doesn't try to emit ANSI color codes into the textbox.
- The `_run` worker catches `SystemExit` raised by `run_pipeline`'s pre-flight checks and prints the message into the log instead of letting the thread die silently.

## Key design decisions

- **Underlying scripts imported as modules, not rewritten.** `pepub.py`, `pepdf.py`, and `compress_images.py` keep their standalone CLIs working. Useful when iterating on one converter in isolation, and means `obsibooks.py` is a thin orchestrator rather than a duplicate of their logic.
- **Compress-only is not supported through `obsibooks`.** Folder mode aborts before compression if there are no `.epub`/`.pdf` files. For a compress-only run, use `python compress_images.py <vault>` directly. If a future use case justifies it, the change is small (skip the abort and proceed straight to compression when `compress=True` and both globs are empty).
- **`--epub-only` and `--pdf-only` are mutually exclusive** (argparse group). Unset means "do both".
- **Compression flags without `--compress` are warned-and-ignored**, not treated as implicit `--compress`. Avoids the "I typed it but nothing happened" footgun.

## Public entry points to call from new code

| Function | Purpose |
|---|---|
| `obsibooks.run_pipeline(...)` | High-level: convert + optionally compress, file or folder |
| `pepub.convert_epub(path, overwrite, output_base_dir)` | Single EPUB |
| `pepdf.convert_pdf(path, overwrite, output_base_dir)` | Single PDF |
| `compress_images.compress_images(vault, max_kb, quality, max_w, max_h, dry_run)` | Vault-wide compression |
| `pepub.sanitize_filename(stem)` | Folder-name slugifier (used by GUI preview) |
