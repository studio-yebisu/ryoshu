"""Unicode-safe image input for Indra OCR.

Indra 1.1.2 Converter.convert() reads non-PDF files with
cv2.imreadmulti/imread(str(path)), which cannot open paths outside the ANSI
code page on Windows (e.g. a data folder with Japanese characters). Here the
bytes are read by Python from the same protected work file and decoded with
the same OpenCV flags and fallback, then passed to the converter's own
per-page OCR call. PDFs, missing files and argument errors go through
Converter.convert unchanged.
"""
from pathlib import Path

import cv2
import numpy as np


def decode_gray(data):
    """Grayscale frames exactly as indra_ocr.pipeline.read_images, from bytes."""
    if not data:
        # cv2.imdecode rejects an empty buffer; read_images yields no frames for an empty file.
        return []
    buffer = np.frombuffer(data, dtype=np.uint8)
    frames = []
    if hasattr(cv2, "imdecodemulti"):
        ok, decoded = cv2.imdecodemulti(buffer, cv2.IMREAD_GRAYSCALE)
        frames = list(decoded) if ok and decoded else []
    if not frames:
        image = cv2.imdecode(buffer, cv2.IMREAD_GRAYSCALE)
        frames = [image] if image is not None else []
    return [np.ascontiguousarray(frame) for frame in frames]


def convert(converter, path, max_pages=0):
    """Yield (index, Page) like converter.convert(str(path), max_pages=...)."""
    from indra_ocr.pipeline import InputError, is_pdf

    path = Path(path)
    if max_pages < 0 or not path.is_file() or is_pdf(path):
        yield from converter.convert(str(path), max_pages=max_pages)
        return
    frames = decode_gray(path.read_bytes())
    if not frames:
        raise InputError(f"{path}: not a PDF or a readable image")
    for index, gray in enumerate(frames[: max_pages or None]):
        # Same per-page call and error-page wrapping as Converter.convert.
        yield index, converter._safe(lambda: converter.ocr(gray))
