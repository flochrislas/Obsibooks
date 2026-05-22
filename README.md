# obsibooks

Convert a library of EPUB and PDF books into an Obsidian vault — one folder per book, one markdown file per chapter, an index with YAML frontmatter, and optional in-place image compression to WebP.

```
My Vault/
├── My First Book/
│   ├── 00 - My First Book.md      ← index with YAML frontmatter + [[wiki-links]]
│   ├── 01 - Introduction.md
│   ├── 02 - Chapter One.md
│   ├── ...
│   └── assets/
│       └── cover.webp
└── My Second Book/
    ├── 00 - My Second Book.md
    ├── ...
    └── assets/
```

Each index file has editable metadata fields ready for Obsidian:

```yaml
---
title: My First Book
author: Author Name
publisher: Publisher
year: 2021
read: false
rating: null
tags:
  - book
---
```

## Install

Pick one of three paths.

### 1. From PyPI (recommended)

```bash
pip install obsibooks            # CLI only
pip install "obsibooks[gui]"     # also install the customtkinter GUI
```

This puts an `obsibooks` command on your PATH.

### 2. Prebuilt binary (no Python install)

Download the single-file executable for your OS from the latest [GitHub release](https://github.com/flochrislas/obsibooks/releases) — assets are named `obsibooks-windows.exe`, `obsibooks-macos`, `obsibooks-linux`. Drop it anywhere on PATH and run it.

### 3. From source

```bash
git clone https://github.com/flochrislas/obsibooks.git
cd obsibooks
pip install -e .
```

### External requirement: Pandoc

EPUB conversion calls Pandoc, regardless of how obsibooks itself is installed:

```bash
# Windows
winget install --id JohnMacFarlane.Pandoc

# macOS
brew install pandoc
```

Or download from [pandoc.org](https://pandoc.org/installing.html). PDF-only runs do not need Pandoc.

## Usage

### Command line

```bash
# Single file (format auto-detected from extension)
python obsibooks.py path/to/book.epub
python obsibooks.py path/to/book.pdf

# Whole folder — converts every .epub and .pdf inside
python obsibooks.py path/to/library/

# Write output to a specific vault folder (default: next to each source file)
python obsibooks.py path/to/library/ -d path/to/vault/

# Re-convert books whose output folder already exists
python obsibooks.py path/to/library/ -d path/to/vault/ --overwrite

# Restrict to one format
python obsibooks.py path/to/library/ --epub-only
python obsibooks.py path/to/library/ --pdf-only

# Convert + compress vault images in one go
python obsibooks.py path/to/library/ -d path/to/vault/ --compress

# Preview what compression would do, without touching files
python obsibooks.py path/to/library/ -d path/to/vault/ --compress --dry-run

# Tighter compression budget
python obsibooks.py path/to/library/ -d vault/ --compress \
    --max-kb 300 --max-width 1280 --max-height 1280 --quality 80
```

Each book's output folder is named after the **source filename** (not the book's metadata title), so renaming an `.epub` or `.pdf` before conversion controls the output folder name. Re-running skips books whose folder already exists, so partial batches can resume safely; pass `--overwrite` to force re-conversion.

#### Flags

| Flag | Default | Description |
|---|---|---|
| `path` | — | EPUB/PDF file, or folder containing either |
| `-d, --output-dir DIR` | next to each source | Vault root for converted books |
| `-o, --overwrite` | off | Re-convert already-converted books |
| `--epub-only` | both run | Folder mode: skip PDFs |
| `--pdf-only` | both run | Folder mode: skip EPUBs |
| `--compress` | off | Compress vault images after conversion |
| `--max-kb N` | 500 | Compression: file-size threshold (kB) |
| `--max-width N` | 1024 | Compression: pixel width limit |
| `--max-height N` | 1024 | Compression: pixel height limit |
| `--quality N` | 85 | Compression: WebP lossy quality (1–100) |
| `--dry-run` | off | Compression: list-only, no changes |

`--epub-only` and `--pdf-only` are mutually exclusive. Compression flags are ignored unless `--compress` is set.

### GUI

```bash
pythonw obsibooks-gui.pyw
```

Or double-click `obsibooks-gui.bat` on Windows.

The window has:

- **Input** — a single `.epub`/`.pdf` (via *File*) or a folder of them (via *Folder*).
- **Output** — vault directory; leave empty to write alongside each source file.
- **Overwrite / Convert EPUBs / Convert PDFs** — toggles. Uncheck a format to skip it in folder mode.
- **Compress images** — when ticked, the four entries underneath become editable: max kB, max width, max height, quality. *Dry run* lists images that would be processed without touching anything.
- **Convert** — runs the pipeline in a worker thread; the main area streams the conversion log.

When you pick an input, the main area first shows a preview listing the books that *will* be converted given the current toggles (already-converted books are hidden when Overwrite is off). The same area then streams the live log once you click Convert.

## Image compression

When `--compress` is set, every image under `vault/*/assets/` that exceeds the size threshold *or* the pixel limits is converted to WebP, the markdown references in the book's notes are rewritten, and the original is deleted.

| Source format | Treatment |
|---|---|
| `.png` | Lossless WebP first. If the result is still over `--max-kb`, falls back to lossy at `--quality`. The size cap takes priority over preserving lossless quality. |
| `.jpg` / `.jpeg` / `.bmp` / `.tiff` / `.tif` / `.webp` | Lossy WebP at `--quality`. |
| `.gif` | Skipped — would lose animation. |

If an image wasn't resized and the WebP encoding ended up no smaller than the original, the WebP is discarded and the source kept as-is. Resized images are always kept regardless of byte count — the goal there is dimensions.

## Project layout

- `obsibooks.py` — CLI and the single `run_pipeline()` orchestrator function.
- `obsibooks-gui.pyw` — customtkinter GUI. Driven by the same `run_pipeline()`.
- `obsibooks-gui.bat` — Windows launcher.
- `pepub.py` — EPUB → markdown. Also runnable on its own (`python pepub.py …`).
- `pepdf.py` — PDF → markdown. Also runnable on its own.
- `compress_images.py` — vault image compression. Also runnable on its own when you want compression without conversion.

The three underlying scripts are imported as modules and reused without modification, so any of them can still be used in isolation if that's all you need.

## License

See `LICENSE`.
