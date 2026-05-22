"""obsibooks — unified CLI to convert a folder of EPUBs and PDFs to Obsidian
markdown, optionally followed by image compression of the resulting vault.

Wraps pepub.convert_epub, pepdf.convert_pdf and compress_images.compress_images
without modifying them. The same run_pipeline() entry point is used by both
the CLI here and the customtkinter GUI (obsibooks-gui.pyw).
"""

import argparse
import io
import sys
from pathlib import Path

__version__ = "0.1.0"

try:
    from pepub import convert_epub, _print_batch_report as _epub_batch_report
    _PEPUB_IMPORT_ERROR = None
except ImportError as _e:
    convert_epub = None
    _epub_batch_report = None
    _PEPUB_IMPORT_ERROR = _e

from pepdf import convert_pdf, _print_batch_report as _pdf_batch_report
from compress_images import compress_images as _compress_vault


class _Tee:
    """Mirror stderr writes to the real stream and an in-memory buffer."""

    def __init__(self, real):
        self.real = real
        self.buf = io.StringIO()

    def write(self, text):
        self.real.write(text)
        self.buf.write(text)

    def flush(self):
        self.real.flush()


def _check_epub_prereqs():
    if _PEPUB_IMPORT_ERROR is not None:
        raise SystemExit(
            f'Error: EPUB conversion is unavailable ({_PEPUB_IMPORT_ERROR}).\n'
            'Install with: pip install ebooklib beautifulsoup4 lxml pypandoc pyyaml'
        )
    import pypandoc
    try:
        pypandoc.get_pandoc_version()
    except OSError:
        raise SystemExit(
            'Error: pandoc is not installed or not found in PATH.\n'
            'Install it from https://pandoc.org/installing.html'
        )


def _check_pdf_prereqs():
    try:
        import fitz  # noqa: F401
        import pymupdf4llm  # noqa: F401
    except ImportError:
        raise SystemExit(
            'Error: pymupdf and pymupdf4llm are required for PDF conversion.\n'
            'Install with: pip install pymupdf pymupdf4llm'
        )


def _check_compress_prereqs():
    try:
        from PIL import Image  # noqa: F401
    except ImportError:
        raise SystemExit(
            'Error: Pillow is required for --compress.\n'
            'Install with: pip install Pillow'
        )


def _run_batch(paths, converter, *, overwrite, output_base_dir, label):
    """Iterate `paths`, call `converter` on each, capture stderr per file.

    Returns the list of (name, status, warning_lines, error_msg) tuples that
    pepub/pepdf's _print_batch_report expects.
    """
    total = len(paths)
    results = []
    for i, p in enumerate(paths, 1):
        print(f'[{label} {i}/{total}] {p.name}', flush=True)
        tee = _Tee(sys.stderr)
        sys.stderr = tee
        status = 'ok'
        error_msg = ''
        try:
            outcome = converter(p, overwrite=overwrite, output_base_dir=output_base_dir)
            if outcome == 'skipped':
                status = 'skipped'
        except Exception as e:
            status = 'error'
            error_msg = str(e)
            print(f'  Error: {e}', file=tee.real)
        finally:
            sys.stderr = tee.real
        captured = tee.buf.getvalue()
        warning_lines = [l for l in captured.splitlines() if l.startswith('Warning:')]
        results.append((p.name, status, warning_lines, error_msg))
    return results


def run_pipeline(
    input_path,
    *,
    output_dir=None,
    overwrite=False,
    do_epub=True,
    do_pdf=True,
    compress=False,
    max_kb=500,
    max_width=1024,
    max_height=1024,
    quality=85,
    dry_run=False,
):
    """Single entry point used by both the CLI and the GUI.

    - input_path: an EPUB/PDF file, or a folder containing either.
    - output_dir: vault root for converted books. None means each book is
      written next to its source.
    - do_epub / do_pdf: format filters for folder mode (ignored when
      input_path is a single file).
    - compress + max_kb / max_width / max_height / quality / dry_run:
      passed through to compress_images.compress_images after conversion.
    """
    input_path = Path(input_path)
    if output_dir is not None:
        output_dir = Path(output_dir)
        if output_dir.exists() and not output_dir.is_dir():
            raise SystemExit(f'Error: output path is not a directory: {output_dir}')
        output_dir.mkdir(parents=True, exist_ok=True)

    if not input_path.exists():
        raise SystemExit(f'Error: path not found: {input_path}')

    if input_path.is_file():
        suffix = input_path.suffix.lower()
        if suffix == '.epub':
            _check_epub_prereqs()
            convert_epub(input_path, overwrite=overwrite, output_base_dir=output_dir)
        elif suffix == '.pdf':
            _check_pdf_prereqs()
            convert_pdf(input_path, overwrite=overwrite, output_base_dir=output_dir)
        else:
            raise SystemExit(f'Error: unsupported file type: {input_path.suffix}')
    else:
        epubs = sorted(input_path.glob('*.epub')) if do_epub else []
        pdfs = sorted(input_path.glob('*.pdf')) if do_pdf else []
        if not epubs and not pdfs:
            kinds = []
            if do_epub:
                kinds.append('EPUB')
            if do_pdf:
                kinds.append('PDF')
            raise SystemExit(f'No {" or ".join(kinds)} files found in: {input_path}')

        if epubs:
            _check_epub_prereqs()
            epub_results = _run_batch(
                epubs, convert_epub,
                overwrite=overwrite, output_base_dir=output_dir, label='epub',
            )
            _epub_batch_report(epub_results)

        if pdfs:
            _check_pdf_prereqs()
            pdf_results = _run_batch(
                pdfs, convert_pdf,
                overwrite=overwrite, output_base_dir=output_dir, label='pdf',
            )
            _pdf_batch_report(pdf_results)

    if compress:
        _check_compress_prereqs()
        vault = output_dir if output_dir else (
            input_path if input_path.is_dir() else input_path.parent
        )
        print(f'\nCompressing images under: {vault}')
        _compress_vault(vault, max_kb, quality, max_width, max_height, dry_run)


def main():
    for _stream in (sys.stdout, sys.stderr):
        if hasattr(_stream, 'reconfigure'):
            _stream.reconfigure(encoding='utf-8')

    parser = argparse.ArgumentParser(
        prog='obsibooks',
        description=(
            'Convert EPUB and PDF ebooks to Obsidian markdown, then optionally '
            'compress vault images.'
        ),
    )
    parser.add_argument('--version', action='version',
                        version=f'obsibooks {__version__}')
    parser.add_argument('path',
                        help='Path to an EPUB/PDF file or a folder containing either')
    parser.add_argument('-o', '--overwrite', action='store_true',
                        help='Overwrite already-converted books (default: skip them)')
    parser.add_argument('-d', '--output-dir',
                        help='Directory where converted books are written '
                             '(default: same folder as each input file)')

    fmt = parser.add_mutually_exclusive_group()
    fmt.add_argument('--epub-only', action='store_true',
                     help='In folder mode, convert only EPUB files')
    fmt.add_argument('--pdf-only', action='store_true',
                     help='In folder mode, convert only PDF files')

    parser.add_argument('--compress', action='store_true',
                        help='After conversion, compress images in the vault (WebP)')
    parser.add_argument('--max-kb', type=int, default=500,
                        help='Compression: file size threshold in kB (default: 500)')
    parser.add_argument('--max-width', type=int, default=1024,
                        help='Compression: maximum image width in px (default: 1024)')
    parser.add_argument('--max-height', type=int, default=1024,
                        help='Compression: maximum image height in px (default: 1024)')
    parser.add_argument('--quality', type=int, default=85, metavar='1-100',
                        help='Compression: WebP lossy quality (default: 85)')
    parser.add_argument('--dry-run', action='store_true',
                        help='Compression: list images that would be processed without changes')

    args = parser.parse_args()

    if not 1 <= args.quality <= 100:
        parser.error('--quality must be between 1 and 100')

    compress_flags_used = (
        args.max_kb != 500 or args.max_width != 1024 or args.max_height != 1024
        or args.quality != 85 or args.dry_run
    )
    if compress_flags_used and not args.compress:
        print('Note: compression options were given but --compress is not set; '
              'they will be ignored.', file=sys.stderr)

    run_pipeline(
        Path(args.path),
        output_dir=Path(args.output_dir) if args.output_dir else None,
        overwrite=args.overwrite,
        do_epub=not args.pdf_only,
        do_pdf=not args.epub_only,
        compress=args.compress,
        max_kb=args.max_kb,
        max_width=args.max_width,
        max_height=args.max_height,
        quality=args.quality,
        dry_run=args.dry_run,
    )


if __name__ == '__main__':
    main()
