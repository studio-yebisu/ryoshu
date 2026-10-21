"""Regenerate display images from immutable originals, without OCR or the model."""
from pathlib import Path
import uuid

from PIL import Image, ImageOps

from . import config
from .pdf_runtime import PDF_LOCK


def save_preview(image, target, quality):
    target = Path(target)
    if target.is_file():
        return
    temporary = target.with_name(target.name + "." + uuid.uuid4().hex + ".tmp")
    try:
        image.save(temporary, format="JPEG", quality=quality)
        temporary.replace(target)
    finally:
        temporary.unlink(missing_ok=True)


def ensure_preview(store, row, index):
    if not 0 <= index < config.MAX_PAGES:
        raise ValueError("プレビューのページ番号が正しくありません")
    folder = store.root / "receipts" / row["id"]
    target = folder / f"preview-{index}.jpg"
    if target.is_file():
        return target
    with PDF_LOCK:
        if target.is_file():
            return target
        original = folder / ("original" + row["extension"])
        if row["extension"] == ".pdf":
            import pypdfium2 as pdfium
            with pdfium.PdfDocument(original) as document:
                if index >= len(document):
                    raise ValueError("このページはありません")
                page = document[index]
                bitmap = page.render(scale=min(2, 2048 / max(page.get_size())))
                try:
                    save_preview(bitmap.to_pil().convert("RGB"), target, 88)
                finally:
                    bitmap.close()
                    page.close()
        else:
            if index:
                raise ValueError("画像は1ページです")
            with Image.open(original) as raw:
                image = ImageOps.exif_transpose(raw).convert("RGB")
                image.thumbnail((1800, 2400))
                save_preview(image, target, 90)
        return target
