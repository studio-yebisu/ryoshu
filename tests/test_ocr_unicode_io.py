"""OCR image input under Japanese/space work paths, compared with Indra's own reader (fake OCR, real codecs)."""
import io
import os
from types import SimpleNamespace

import cv2
import numpy as np
import pytest
from PIL import Image, ImageDraw

from indra_ocr.document import Converter
from indra_ocr.pipeline import InputError, read_images
from receipt_ledger import ocr_focus, ocr_io

ORIGINAL_IMREAD, ORIGINAL_IMREADMULTI = cv2.imread, cv2.imreadmulti


class FakeOCR:
    """Records the grayscale arrays handed to the OCR call; no model."""

    def __init__(self, fail=False):
        self.calls, self.fail = [], fail

    def __call__(self, gray, turn=True):
        self.calls.append((gray.copy(), turn))
        if self.fail:
            raise RuntimeError("boom")
        return SimpleNamespace(text="合計 1,650円", lines=[], source="ocr", timings={})


def fake_converter(fail=False):
    converter = Converter.__new__(Converter)  # real convert()/_safe(), no weights
    converter.ocr, converter.dpi = FakeOCR(fail), 72
    return converter


def sample(seed=7, size=(173, 121)):
    rng = np.random.default_rng(seed)
    image = Image.fromarray(rng.integers(0, 255, (size[1], size[0], 3), dtype=np.uint8))
    ImageDraw.Draw(image).rectangle((10, 10, 90, 60), fill=(30, 160, 220))
    return image


def encoded():
    out = {}
    for name, kwargs in {"png": {}, "jpg": {"quality": 87}, "bmp": {}}.items():
        buffer = io.BytesIO(); sample().save(buffer, "JPEG" if name == "jpg" else name.upper(), **kwargs); out[name] = buffer.getvalue()
    buffer = io.BytesIO()
    frames = [sample(seed).convert("L") for seed in (1, 2, 3)]
    frames[0].save(buffer, "TIFF", save_all=True, append_images=frames[1:]); out["tif"] = buffer.getvalue()
    return out


@pytest.fixture
def dirs(tmp_path):
    ascii_dir, unicode_dir = tmp_path / "ascii space dir", tmp_path / "QA 領収書 テスト 空白"
    ascii_dir.mkdir(); unicode_dir.mkdir()
    return ascii_dir, unicode_dir


def write_both(dirs, name, data):
    paths = [d / name for d in dirs]
    for path in paths:
        path.write_bytes(data)
    assert paths[0].read_bytes() == paths[1].read_bytes()
    return paths


@pytest.mark.skipif(os.name != "nt", reason="the root cause is Windows ANSI-path handling in OpenCV")
def test_root_cause_indra_reader_cannot_open_japanese_path(dirs):
    _, unicode_path = write_both(dirs, "rectified-p0.png", encoded()["png"])
    with pytest.raises(InputError, match="not a PDF or a readable image"):
        list(fake_converter().convert(str(unicode_path), max_pages=1))


@pytest.mark.parametrize("fmt", ["png", "jpg", "bmp", "tif"])
def test_decoded_grayscale_is_identical_to_indra_reader(dirs, fmt):
    ascii_path, unicode_path = write_both(dirs, f"page.{fmt}", encoded()[fmt])
    reference = read_images(ascii_path)
    decoded = ocr_io.decode_gray(unicode_path.read_bytes())
    assert len(decoded) == len(reference) == (3 if fmt == "tif" else 1)
    for got, want in zip(decoded, reference):
        assert got.dtype == want.dtype == np.uint8 and got.shape == want.shape and np.array_equal(got, want)
        assert got.flags["C_CONTIGUOUS"]


@pytest.mark.parametrize("max_pages", [0, 1, 2])
def test_adapter_passes_exactly_what_converter_convert_passes(dirs, max_pages):
    ascii_path, unicode_path = write_both(dirs, "multi.tif", encoded()["tif"])
    reference, adapted = fake_converter(), fake_converter()
    want = list(reference.convert(str(ascii_path), max_pages=max_pages))
    got = list(ocr_io.convert(adapted, unicode_path, max_pages=max_pages))
    assert [i for i, _ in got] == [i for i, _ in want]
    assert len(adapted.ocr.calls) == len(reference.ocr.calls) == (max_pages or 3)
    for (g, gt), (w, wt) in zip(adapted.ocr.calls, reference.ocr.calls):
        assert np.array_equal(g, w) and gt == wt is True


def test_failed_page_becomes_the_same_error_page(dirs):
    ascii_path, unicode_path = write_both(dirs, "page.png", encoded()["png"])
    want = list(fake_converter(fail=True).convert(str(ascii_path)))[0][1]
    got = list(ocr_io.convert(fake_converter(fail=True), unicode_path))[0][1]
    assert (got.source, got.note, got.timings) == (want.source, want.note, want.timings) == ("error", "RuntimeError: boom", {"total_ms": 0.0})


def test_unreadable_image_raises_the_same_input_error(dirs):
    ascii_path, unicode_path = write_both(dirs, "broken.png", b"\x89PNG not really")
    with pytest.raises(InputError) as want:
        list(fake_converter().convert(str(ascii_path)))
    with pytest.raises(InputError) as got:
        list(ocr_io.convert(fake_converter(), unicode_path))
    assert str(got.value) == str(want.value).replace(str(ascii_path), str(unicode_path))


def test_empty_internal_image_raises_the_same_input_error_without_ocr(dirs):
    ascii_path, unicode_path = write_both(dirs, "rectified-p0.png", b"")
    reference, adapted = fake_converter(), fake_converter()
    with pytest.raises(InputError) as want:
        list(reference.convert(str(ascii_path), max_pages=1))
    with pytest.raises(InputError) as got:
        list(ocr_io.convert(adapted, unicode_path, max_pages=1))
    assert str(got.value) == str(want.value).replace(str(ascii_path), str(unicode_path))
    assert ocr_io.decode_gray(b"") == [] and reference.ocr.calls == adapted.ocr.calls == []


@pytest.mark.parametrize("case", ["pdf", "missing", "negative"])
def test_pdf_missing_and_invalid_arguments_delegate_to_converter_convert(dirs, case):
    _, unicode_dir = dirs
    path = unicode_dir / "original.pdf"
    if case == "pdf":
        path.write_bytes(b"%PDF-1.4\n%fictional\n")
    converter, seen = fake_converter(), []
    converter.convert = lambda p, max_pages=0: seen.append((p, max_pages)) or iter([(0, "delegated")])
    max_pages = -1 if case == "negative" else 10
    assert list(ocr_io.convert(converter, path, max_pages=max_pages)) == [(0, "delegated")]
    assert seen == [(str(path), max_pages)] and converter.ocr.calls == []


def skewed_receipt_pdf(path):
    page = Image.new("RGB", (600, 800), (60, 60, 60))
    draw = ImageDraw.Draw(page)
    draw.polygon([(60, 40), (560, 70), (540, 760), (40, 730)], fill=(255, 255, 255))
    draw.rectangle((120, 480, 420, 560), fill=(20, 20, 20))
    page.save(path, "PDF", resolution=72)


def test_focus_rectify_and_header_images_stay_in_the_unicode_workdir_and_reach_ocr(tmp_path):
    folder = tmp_path / "data" / ".ryoshu-work" / "ocr-領収書 空白"
    folder.mkdir(parents=True)
    original = folder / "original.pdf"
    skewed_receipt_pdf(original)
    pages = [{"index": 0, "source": "ocr", "text": "合計", "lines": [
        {"text": "合計", "confidence": 1.0, "box": [130.0, 490.0, 230.0, 530.0]},
        {"text": "1,650", "confidence": 1.0, "box": [260.0, 480.0, 410.0, 560.0]}]}]
    cwd, converter = os.getcwd(), fake_converter()
    before = {p for p in tmp_path.rglob("*")}

    rectified = ocr_focus.rectify_pages(converter, original, ".pdf", pages, folder)
    focus = ocr_focus.focus_regions(converter, original, ".pdf", pages, folder)
    headers = ocr_focus.focus_headers(converter, original, ".pdf", pages, folder)

    written = sorted(p.name for p in set(tmp_path.rglob("*")) - before)
    assert all((folder / name).parent == folder for name in written)
    assert "rectified-p0.png" in written and rectified[0]["ocr_image"] == "rectified-p0.png"
    assert any(name.startswith("focus-p0-") for name in written) and focus
    assert {"header-p0-0.png", "header-p0-1.png"} <= set(written) and len(headers) == 2
    images = [folder / name for name in written]
    expected = [ocr_io.decode_gray(p.read_bytes())[0] for p in images]
    assert len(converter.ocr.calls) == len(images)
    assert all(any(np.array_equal(call, want) for call, _ in converter.ocr.calls) for want in expected)
    assert os.getcwd() == cwd and cv2.imread is ORIGINAL_IMREAD and cv2.imreadmulti is ORIGINAL_IMREADMULTI
