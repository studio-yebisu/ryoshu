"""Text evidence retained from OCR, the checked PDF layer, and pre-correction OCR."""


def evidence_text(ocr):
    if not ocr:
        return ""
    initial = ocr.get("initial_text") or "\n".join(p.get("initial_text", "") for p in ocr.get("pages", []))
    return "\n\n".join(str(part) for part in (ocr.get("text", ""), ocr.get("native_pdf_text", ""), initial) if part)
