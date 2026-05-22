#!/usr/bin/env python3
"""
compress_images.py

Scan all assets/ folders in the vault, find images that are too large (file size
or resolution), convert them to WebP, update Markdown references in the book
notes, and delete the originals.

An image is processed when either condition is true:
  - File size  > --max-kb   (default 500 kB)
  - Width or height > --max-width / --max-height  (default 1024 px each)
    → resized so the longest side equals the limit, keeping aspect ratio

Conversion strategy:
  - JPEG/JPG  → WebP lossy  (quality 85 by default; good for photos/covers)
  - PNG       → WebP lossless first; falls back to lossy if lossless is bigger
  - Already-WebP files are re-compressed / resized in place
  - If only converting for file size and WebP would be larger, the image is kept

Requires: pip install Pillow

Usage:
    python compress_images.py <vault_path>
    python compress_images.py <vault_path> --max-kb 500 --max-width 1024 --max-height 1024
    python compress_images.py <vault_path> --max-kb 300 --quality 85 --dry-run
"""

import re
import sys
import argparse
from pathlib import Path

try:
    from PIL import Image
except ImportError:
    print("Error: Pillow is not installed. Run:  pip install Pillow")
    sys.exit(1)

IMAGE_EXTENSIONS = {'.jpg', '.jpeg', '.png', '.bmp', '.tiff', '.tif', '.webp'}
# GIF excluded intentionally: animation would be lost


ImageInfo = tuple[Path, bool, bool, int, int]  # path, too_large, too_wide_tall, w, h


def find_images_to_process(vault_path: Path, max_bytes: int, max_w: int, max_h: int) -> list[ImageInfo]:
    results = []
    for assets_dir in vault_path.glob('*/assets'):
        if not assets_dir.is_dir():
            continue
        for img_path in sorted(assets_dir.iterdir()):
            if img_path.suffix.lower() not in IMAGE_EXTENSIONS:
                continue
            too_large = img_path.stat().st_size > max_bytes
            try:
                with Image.open(img_path) as img:
                    w, h = img.size
            except Exception:
                continue
            too_wide_tall = w > max_w or h > max_h
            if too_large or too_wide_tall:
                results.append((img_path, too_large, too_wide_tall, w, h))
    return results


def try_convert(
    img_path: Path, quality: int, max_w: int, max_h: int, max_bytes: int
) -> tuple[Path, int, bool, tuple[int, int]] | None:
    """
    Convert to WebP (resizing if needed).
    Returns (webp_path, new_size, was_resized, final_dims), or None if conversion
    would not reduce file size (only possible when no resize was needed).

    PNG: tries lossless WebP first, but falls back to lossy at --quality
    whenever the lossless result is still above max_bytes — the size cap
    takes priority over preserving lossless quality.
    """
    original_size = img_path.stat().st_size
    ext = img_path.suffix.lower()

    # .webp → .webp would collide; use a temp name then rename
    same_ext = ext == '.webp'
    webp_path = img_path.with_name(img_path.stem + ('._tmp.webp' if same_ext else '.webp'))

    with Image.open(img_path) as img:
        w, h = img.size
        resized = w > max_w or h > max_h
        if resized:
            img.thumbnail((max_w, max_h), Image.LANCZOS)

        final_dims = img.size

        if ext == '.png':
            img.save(webp_path, 'WEBP', lossless=True, method=6)
            if webp_path.stat().st_size > max_bytes:
                img.save(webp_path, 'WEBP', quality=quality, method=6)
        else:
            img.save(webp_path, 'WEBP', quality=quality, method=6)

    new_size = webp_path.stat().st_size
    if not resized and new_size >= original_size:
        webp_path.unlink()
        return None

    final_path = img_path.with_suffix('.webp') if same_ext else webp_path
    if same_ext:
        webp_path.replace(final_path)

    return final_path, new_size, resized, final_dims


def update_md_references(book_dir: Path, old_name: str, new_name: str) -> list[str]:
    """Replace all references to old_name with new_name in .md files; return list of changed note names."""
    changed = []
    pattern = re.compile(re.escape(old_name))
    for md_path in sorted(book_dir.rglob('*.md')):
        text = md_path.read_text(encoding='utf-8')
        new_text = pattern.sub(new_name, text)
        if new_text != text:
            md_path.write_text(new_text, encoding='utf-8')
            changed.append(md_path.name)
    return changed


def compress_images(
    vault_path: Path, max_kb: int, quality: int, max_w: int, max_h: int, dry_run: bool
) -> None:
    max_bytes = max_kb * 1024
    images = find_images_to_process(vault_path, max_bytes, max_w, max_h)

    if not images:
        print(f"No images found exceeding {max_kb} kB or {max_w}×{max_h} px. Nothing to do.")
        return

    print(f"Found {len(images)} image(s) to process.\n")

    total_before = 0
    total_after = 0
    converted = 0
    skipped = 0

    for img_path, too_large, too_wide_tall, w, h in images:
        book_dir = img_path.parent.parent
        original_size = img_path.stat().st_size
        rel = img_path.relative_to(vault_path)

        reasons = []
        if too_large:
            reasons.append(f"{original_size / 1024:.0f} kB")
        if too_wide_tall:
            reasons.append(f"{w}×{h} px")
        print(f"  {rel}  ({', '.join(reasons)})")

        if dry_run:
            webp_name = img_path.with_suffix('.webp').name
            actions = []
            if too_wide_tall:
                # Compute what the thumbnail size would be
                thumb_w, thumb_h = w, h
                if thumb_w > max_w or thumb_h > max_h:
                    ratio = min(max_w / thumb_w, max_h / thumb_h)
                    thumb_w, thumb_h = int(thumb_w * ratio), int(thumb_h * ratio)
                actions.append(f"resize to {thumb_w}×{thumb_h} px")
            actions.append(f"convert to {webp_name}")
            print(f"    → [DRY RUN] would {', '.join(actions)}")
            skipped += 1
            continue

        result = try_convert(img_path, quality, max_w, max_h, max_bytes)

        if result is None:
            print(f"    → skipped (WebP would not be smaller)")
            skipped += 1
            continue

        webp_path, new_size, was_resized, final_dims = result
        saved = original_size - new_size
        total_before += original_size
        total_after += new_size
        converted += 1

        changed_notes = update_md_references(book_dir, img_path.name, webp_path.name)
        if img_path != webp_path:
            img_path.unlink()

        details = []
        if was_resized:
            details.append(f"{final_dims[0]}×{final_dims[1]} px")
        details.append(f"{new_size / 1024:.0f} kB")
        if saved > 0:
            details.append(f"saved {saved / 1024:.0f} kB")
        if changed_notes:
            details.append(f"updated {len(changed_notes)} note(s)")
        print(f"    → {webp_path.name}  ({', '.join(details)})")

    print()
    if dry_run:
        print(f"Dry run complete. {len(images)} image(s) would be processed.")
    else:
        total_saved = total_before - total_after
        mb = total_saved / (1024 * 1024)
        print(f"Done. Converted {converted} image(s), skipped {skipped}. "
              f"Total saved: {total_saved / 1024:.0f} kB ({mb:.2f} MB).")


def main():
    parser = argparse.ArgumentParser(
        description="Convert oversized vault images to WebP and update Obsidian note references.",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Examples:
  python compress_images.py "G:/Nodes Network/Obsidian/VaultBooks_EN_read"
  python compress_images.py "." --max-kb 300 --dry-run
  python compress_images.py "." --max-kb 500 --max-width 1920 --max-height 1080
  python compress_images.py "." --quality 90 --dry-run
        """,
    )
    parser.add_argument("vault", help="Path to the vault root directory")
    parser.add_argument(
        "--max-kb", "-m",
        type=int,
        default=500,
        help="File size threshold in kB (default: 500)",
    )
    parser.add_argument(
        "--max-width",
        type=int,
        default=1024,
        help="Maximum image width in pixels; wider images are resized (default: 1024)",
    )
    parser.add_argument(
        "--max-height",
        type=int,
        default=1024,
        help="Maximum image height in pixels; taller images are resized (default: 1024)",
    )
    parser.add_argument(
        "--quality", "-q",
        type=int,
        default=85,
        metavar="1-100",
        help="WebP lossy quality for JPEG/re-compressed sources (default: 85)",
    )
    parser.add_argument(
        "--dry-run", "-n",
        action="store_true",
        help="List images that would be processed without changing any files",
    )
    args = parser.parse_args()

    if not 1 <= args.quality <= 100:
        parser.error("--quality must be between 1 and 100")

    vault_path = Path(args.vault).resolve()
    if not vault_path.is_dir():
        print(f"Error: '{args.vault}' is not a directory.")
        sys.exit(1)

    compress_images(vault_path, args.max_kb, args.quality, args.max_width, args.max_height, args.dry_run)


if __name__ == "__main__":
    main()
