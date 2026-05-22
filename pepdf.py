import argparse, io, re, sys, unicodedata
from pathlib import Path
import yaml

try:
    import fitz  # PyMuPDF
    import pymupdf4llm
except ImportError:
    fitz = None
    pymupdf4llm = None


def sanitize_filename(name):
    name = unicodedata.normalize('NFC', name)
    name = re.sub(r'[<>:"/\\|?*\x00-\x1f]', '', name)
    name = re.sub(r'\s+', ' ', name).strip()
    name = name[:200]
    name = name.rstrip('. ')
    return name or 'Untitled'


def extract_metadata(doc, pdf_path):
    m = doc.metadata
    title = (m.get('title') or '').strip() or Path(pdf_path).stem
    author = (m.get('author') or '').strip() or None
    year = None
    raw_date = (m.get('creationDate') or '').strip()
    if raw_date:
        match = re.search(r'\b(1[0-9]{3}|20[0-9]{2})\b', raw_date)
        year = int(match.group(1)) if match else None
    return {
        'title': title,
        'author': author,
        'publisher': None,
        'year': year,
    }


def build_chapter_ranges(doc):
    """Return [(title, start_page_0based, end_page_0based_inclusive), ...].

    Uses only level-1 TOC entries. If no TOC, returns an empty list (caller
    will fall back to single-file conversion).
    """
    toc = doc.get_toc()
    top = [(title, page - 1) for level, title, page in toc if level == 1]
    if not top:
        return []

    n_pages = doc.page_count
    ranges = []
    for i, (title, start) in enumerate(top):
        end = top[i + 1][1] - 1 if i + 1 < len(top) else n_pages - 1
        # Clamp to valid range
        start = max(0, min(start, n_pages - 1))
        end = max(start, min(end, n_pages - 1))
        ranges.append((title, start, end))
    return ranges


def _rewrite_image_refs(md, assets_dir):
    """Rewrite absolute image paths emitted by pymupdf4llm to relative assets/ refs."""
    assets_str = str(assets_dir).replace('\\', '/')

    def _replace(m):
        alt = m.group(1)
        path = m.group(2)
        # Normalise backslashes
        path_norm = path.replace('\\', '/')
        # Strip leading assets_dir prefix if present
        if path_norm.startswith(assets_str + '/'):
            filename = path_norm[len(assets_str) + 1:]
        else:
            filename = Path(path).name
        return f'![{alt}](assets/{filename})'

    md = re.sub(r'!\[([^\]]*)\]\(([^)]+)\)', _replace, md)
    return md


def _postprocess_markdown(md, chapter_title, assets_dir=None):
    # Rewrite image refs before any other transforms
    if assets_dir is not None:
        md = _rewrite_image_refs(md, assets_dir)

    # Remove bare page-number lines (a lone integer, possibly padded)
    md = re.sub(r'^\s*\d{1,4}\s*$', '', md, flags=re.MULTILINE)

    # Fix broken hyphenation across page breaks: word-\nword → wordword
    md = re.sub(r'(\w)-\n(\w)', r'\1\2', md)

    # Deduplicate chapter title: remove any h1/h2 that matches it
    title_norm = re.sub(r'\W+', ' ', chapter_title).strip().lower()
    title_norm_noprefix = re.sub(r'\W+', ' ', re.sub(r'^\d+[\.\-\u2013\u2014]\s*', '', chapter_title)).strip().lower()

    lines = md.split('\n')
    filtered = []
    for line in lines:
        heading_m = re.match(r'^#{1,2}\s+(.+)', line)
        if heading_m:
            text_norm = re.sub(r'\W+', ' ', heading_m.group(1)).strip().lower()
            if text_norm in (title_norm, title_norm_noprefix):
                continue
        filtered.append(line)
    md = '\n'.join(filtered)

    # Demote any remaining h1 to h2, then prepend chapter title as sole h1
    md = re.sub(r'^# (.+)', r'## \1', md, flags=re.MULTILINE)
    md = f'# {chapter_title}\n\n{md.lstrip()}'

    # Collapse 3+ blank lines to 2
    md = re.sub(r'\n{3,}', '\n\n', md)

    # Strip trailing whitespace per line
    md = '\n'.join(line.rstrip() for line in md.splitlines())

    return md


def convert_chapter(doc, pages, chapter_title, assets_dir):
    """Convert a page range to Markdown using pymupdf4llm."""
    assets_dir.mkdir(parents=True, exist_ok=True)
    md = pymupdf4llm.to_markdown(
        doc,
        pages=pages,
        write_images=True,
        image_path=str(assets_dir),
        image_format='png',
        show_progress=False,
    )
    return _postprocess_markdown(md, chapter_title, assets_dir)


def generate_toc_file(metadata, chapters, output_dir):
    frontmatter = {
        'title': metadata['title'],
        'author': metadata['author'],
        'publisher': metadata['publisher'],
        'year': metadata['year'],
        'read': False,
        'rating': None,
        'tags': ['book'],
    }

    yaml_str = yaml.dump(frontmatter, sort_keys=False, allow_unicode=True,
                         default_flow_style=False)

    toc_lines = ['## Table of Contents', '']
    for ch in chapters:
        stem = Path(ch['filename']).stem
        toc_lines.append(f'- [[{stem}]]')

    body = '\n'.join(toc_lines)
    content = f'---\n{yaml_str}---\n\n{body}\n'

    safe_title = sanitize_filename(metadata['title'])
    toc_name = f'00 - {safe_title}.md'
    if len(toc_name) > 120:
        toc_name = toc_name[:117].rstrip(' -') + '.md'
    (output_dir / toc_name).write_text(content, encoding='utf-8')


def convert_pdf(pdf_path, overwrite=False, output_base_dir=None):
    path = Path(pdf_path)
    if not path.exists():
        print(f'Error: file not found: {pdf_path}', file=sys.stderr)
        sys.exit(1)
    if path.suffix.lower() != '.pdf':
        print(f'Error: not a PDF file: {pdf_path}', file=sys.stderr)
        sys.exit(1)

    doc = fitz.open(str(path))
    metadata = extract_metadata(doc, path)

    # The output folder is named after the PDF filename (without extension),
    # so the user can rename the .pdf before conversion to control the output
    # folder name — and rerunning will skip already-converted books that share
    # that name.
    folder_name = sanitize_filename(path.stem)
    base_dir = Path(output_base_dir) if output_base_dir else path.parent
    output_dir = base_dir / folder_name

    if output_dir.exists() and not overwrite:
        print(f'  Skipping (already converted): {output_dir.name}')
        doc.close()
        return 'skipped'

    output_dir.mkdir(parents=True, exist_ok=True)
    assets_dir = output_dir / 'assets'

    chapter_ranges = build_chapter_ranges(doc)
    chapters = []

    if chapter_ranges:
        total = len(chapter_ranges)
        pad = len(str(total))
        for index, (title, start, end) in enumerate(chapter_ranges, 1):
            pages = list(range(start, end + 1))
            try:
                md = convert_chapter(doc, pages, title, assets_dir)
                safe_ch_title = sanitize_filename(title)
                filename = f'{str(index).zfill(pad)} - {safe_ch_title}.md'
                if len(filename) > 120:
                    filename = filename[:117].rstrip(' -') + '.md'
                (output_dir / filename).write_text(md, encoding='utf-8')
                chapters.append({'title': title, 'filename': filename})
            except Exception as exc:
                print(f'Warning: skipping "{title}": {exc}', file=sys.stderr)
    else:
        # No TOC — convert whole document as one file
        title = metadata['title']
        pages = list(range(doc.page_count))
        try:
            md = convert_chapter(doc, pages, title, assets_dir)
            filename = f'01 - {sanitize_filename(title)}.md'
            if len(filename) > 120:
                filename = filename[:117].rstrip(' -') + '.md'
            (output_dir / filename).write_text(md, encoding='utf-8')
            chapters.append({'title': title, 'filename': filename})
        except Exception as exc:
            print(f'Warning: could not convert document: {exc}', file=sys.stderr)

    doc.close()
    generate_toc_file(metadata, chapters, output_dir)
    print(f'Done. Output: {output_dir}')


def _print_batch_report(results):
    """Print a summary table after batch conversion.

    results: list of (pdf_name, status, warning_lines, error_msg)
      status: 'ok' | 'skipped' | 'error'
    """
    use_color = sys.stdout.isatty()
    RED    = '\033[31m' if use_color else ''
    YELLOW = '\033[33m' if use_color else ''
    GREEN  = '\033[32m' if use_color else ''
    BOLD   = '\033[1m'  if use_color else ''
    RESET  = '\033[0m'  if use_color else ''

    n_total   = len(results)
    n_ok      = sum(1 for _, s, w, _ in results if s == 'ok' and not w)
    n_warned  = sum(1 for _, s, w, _ in results if s == 'ok' and w)
    n_skipped = sum(1 for _, s, _, _ in results if s == 'skipped')
    n_errors  = sum(1 for _, s, _, _ in results if s == 'error')

    bar = '-' * 42
    print(f'\n{BOLD}{bar}{RESET}')
    print(f'{BOLD} Batch report — {n_total} file{"s" if n_total != 1 else ""}{RESET}')
    print(f'{BOLD}{bar}{RESET}')
    print(f' {GREEN}✓{RESET}  {n_ok + n_warned:<4} converted')
    if n_warned:
        print(f' {YELLOW}⚠{RESET}  {n_warned:<4} of those had warnings')
    print(f'    {n_skipped:<4} skipped (already converted)')
    if n_errors:
        print(f' {RED}✗{RESET}  {n_errors:<4} errors')
    print(f'{BOLD}{bar}{RESET}')

    warned_results = [(n, w) for n, s, w, _ in results if s == 'ok' and w]
    if warned_results:
        print(f'\n{YELLOW}Warnings:{RESET}')
        for name, warning_lines in warned_results:
            print(f'  {name}  ({len(warning_lines)})')

    error_results = [(n, e) for n, s, _, e in results if s == 'error']
    if error_results:
        print(f'\n{RED}Errors:{RESET}')
        for name, error_msg in error_results:
            print(f'  {RED}{name}{RESET} — {error_msg}')


def main():
    for _stream in (sys.stdout, sys.stderr):
        if hasattr(_stream, 'reconfigure'):
            _stream.reconfigure(encoding='utf-8')

    parser = argparse.ArgumentParser(
        description='Convert a PDF (or a folder of PDFs) to Obsidian-compatible markdown files.'
    )
    parser.add_argument('path', help='Path to a PDF file or a folder containing PDF files')
    parser.add_argument('-o', '--overwrite', action='store_true',
                        help='Overwrite already-converted books (default: skip them)')
    parser.add_argument('-d', '--output-dir',
                        help='Directory where converted books are written '
                             '(default: same folder as each PDF)')
    args = parser.parse_args()

    output_base_dir = None
    if args.output_dir:
        output_base_dir = Path(args.output_dir)
        if output_base_dir.exists() and not output_base_dir.is_dir():
            print(f'Error: output path is not a directory: {output_base_dir}', file=sys.stderr)
            sys.exit(1)
        output_base_dir.mkdir(parents=True, exist_ok=True)

    if fitz is None or pymupdf4llm is None:
        print(
            'Error: pymupdf and pymupdf4llm are required.\n'
            'Install them with: pip install pymupdf pymupdf4llm',
            file=sys.stderr
        )
        sys.exit(1)

    target = Path(args.path)

    if target.is_file():
        convert_pdf(target, overwrite=args.overwrite, output_base_dir=output_base_dir)
    elif target.is_dir():
        pdfs = sorted(target.glob('*.pdf'))
        if not pdfs:
            print(f'No PDF files found in: {target}', file=sys.stderr)
            sys.exit(1)
        total = len(pdfs)
        results = []

        class _Tee:
            """Write to both the real stderr and a capture buffer."""
            def __init__(self, real):
                self.real = real
                self.buf = io.StringIO()
            def write(self, text):
                self.real.write(text)
                self.buf.write(text)
            def flush(self):
                self.real.flush()

        for i, pdf_path in enumerate(pdfs, 1):
            print(f'[{i}/{total}] {pdf_path.name}', flush=True)
            tee = _Tee(sys.stderr)
            sys.stderr = tee
            status = 'ok'
            error_msg = ''
            try:
                outcome = convert_pdf(pdf_path, overwrite=args.overwrite,
                                      output_base_dir=output_base_dir)
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
            results.append((pdf_path.name, status, warning_lines, error_msg))

        _print_batch_report(results)
    else:
        print(f'Error: path not found: {target}', file=sys.stderr)
        sys.exit(1)


if __name__ == '__main__':
    main()
