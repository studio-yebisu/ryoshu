import io
import json
import re
import threading
import time
import unicodedata
import warnings
from statistics import median
from datetime import date
from pathlib import Path

import httpx
from PIL import Image, ImageOps

from . import config
from .schemas import Draft, Suggestion
from .catalog import account_key, apply_account_policies, default_accounts, resolve_account, retrieve_accounts
from .extraction import amount_on_line, compact, document_flags, extract_total, kanji_amount, money_tokens, total_candidates, vertical_text

Image.MAX_IMAGE_PIXELS = 25_000_000
_converter = None
_ocr_lock = threading.Lock()


def row_ordered_text(lines):
    """Join OCR label/value fragments on the same physical row, without editing characters."""
    valid=[line for line in lines if line["text"].strip() and len(line["box"])==4]
    if not valid:
        return ""
    tolerance=max(3,median(max(1,line["box"][3]-line["box"][1]) for line in valid)*.55)
    rows=[]
    for line in sorted(valid,key=lambda l:((l["box"][1]+l["box"][3])/2,l["box"][0])):
        center=(line["box"][1]+line["box"][3])/2
        if rows and abs(rows[-1]["center"]-center)<=tolerance:
            rows[-1]["lines"].append(line)
        else:
            rows.append({"center":center,"lines":[line]})
    return "\n".join(" ".join(line["text"] for line in sorted(row["lines"],key=lambda l:l["box"][0])) for row in rows)


def validate_file(content: bytes):
    if not content or len(content) > config.MAX_BYTES:
        raise ValueError("1ファイル20MBまでの画像・PDFを選んでください")
    if content.startswith(b"%PDF-"):
        import pypdfium2 as pdfium
        try:
            with pdfium.PdfDocument(content) as pdf:
                if not 1 <= len(pdf) <= config.MAX_PAGES:
                    raise ValueError("PDFは1〜10ページに分けて取り込んでください")
        except ValueError:
            raise
        except Exception as exc:
            raise ValueError("PDFを開けません。破損やパスワード設定を確認してください") from exc
        return ".pdf"
    try:
        with warnings.catch_warnings():
            warnings.simplefilter("error", Image.DecompressionBombWarning)
            with Image.open(io.BytesIO(content)) as image:
                formats = {"JPEG": ".jpg", "PNG": ".png", "WEBP": ".webp"}
                extension = formats.get(image.format)
                if extension is None or getattr(image, "n_frames", 1) != 1:
                    raise ValueError("JPG・PNG・WebP、またはPDFを選んでください。アニメーション画像は対象外です")
                if image.width * image.height > Image.MAX_IMAGE_PIXELS:
                    raise ValueError("画像は2500万画素以下に縮小してください")
                image.verify()
                return extension
    except ValueError:
        raise
    except Exception as exc:
        raise ValueError("画像を開けません。JPG・PNG・WebP・PDFに対応しています") from exc


def read_receipt(folder: Path, extension: str):
    global _converter
    original = folder / ("original" + extension)
    if extension == ".pdf":
        import pypdfium2 as pdfium
        with pdfium.PdfDocument(original) as pdf:
            for i, page in enumerate(pdf):
                bitmap = page.render(scale=min(2, 2048 / max(page.get_size())))
                try:
                    bitmap.to_pil().convert("RGB").save(folder / f"preview-{i}.jpg", quality=88)
                finally:
                    bitmap.close()
                    page.close()
        ocr_input = original
    else:
        with Image.open(original) as raw:
            image = ImageOps.exif_transpose(raw).convert("RGB")
            ocr_input = folder / "normalized.png"
            image.save(ocr_input)
            image.thumbnail((1800, 2400))
            image.save(folder / "preview-0.jpg", quality=90)
    # Setup performs the public model download. Receipt processing uses the cache only.
    import os
    os.environ["HF_HUB_OFFLINE"] = "1"
    with _ocr_lock:
        if _converter is None:
            import indra_ocr
            _converter = indra_ocr.load(threads=4, mode="auto", furniture=True)
        started = time.perf_counter()
        pages = []
        for index, page in _converter.convert(str(ocr_input), max_pages=config.MAX_PAGES):
            if page.source == "error":
                raise ValueError(f"{index + 1}ページ目の読み取りに失敗しました")
            pages.append({"index": int(index), "text": page.text, "source": page.source,
                          "lines": [{"text": line.text, "confidence": float(line.conf), "box": [float(v) for v in line.box]} for line in page.lines],
                          "timings": {k: float(v) for k, v in page.timings.items()}})
    if not pages or not any(p["text"].strip() for p in pages):
        raise ValueError("文字が見つかりませんでした。明るい場所で正面から撮影した画像を試してください")
    import importlib.metadata
    from .ocr_focus import rectify_pages
    with _ocr_lock:
        pages=rectify_pages(_converter,original,extension,pages,folder)
    native_pages=[]
    if extension=='.pdf':
        import pypdfium2 as pdfium
        with pdfium.PdfDocument(original) as pdf:
            for p in pages:
                if p['source'] not in {'hybrid','text'}:continue
                page=pdf[p['index']]
                textpage=page.get_textpage()
                try:native_pages.append(textpage.get_text_range())
                finally:textpage.close();page.close()
    vertical='\n'.join(vertical_text(p['lines']) for p in pages).strip()
    result = {"engine": "Indra OCR", "version": importlib.metadata.version("indra-ocr"), "seconds": round(time.perf_counter() - started, 3),
              "page_count": len(pages), "raw_text": "\n\n".join(p["text"] for p in pages),
              "text": "\n\n".join(row_ordered_text(p["lines"]) or p["text"] for p in pages), "pages": pages,
              "native_pdf_text":"\n\n".join(native_pages),'vertical_text':vertical}
    result['initial_text']='\n\n'.join(p.get('initial_text','') for p in pages).strip()
    if vertical:result['text']+='\n\n[縦組み文字列]\n'+vertical
    from .ocr_focus import focus_regions
    with _ocr_lock:
        focus=focus_regions(_converter,original,extension,pages,folder)
    result['focus']=focus
    if focus:result['text']+='\n\n[画像の部分再読取]\n'+'\n\n'.join(p['text'] for p in focus)
    from .semantic import issuer_candidates
    from .ocr_focus import focus_headers
    metadata_text=result['text']+'\n'+result.get('native_pdf_text','')+'\n'+result.get('initial_text','')
    result['header_focus']=[]
    if not dates_in(metadata_text) or not issuer_candidates(result):
        with _ocr_lock:headers=focus_headers(_converter,original,extension,pages,folder)
        result['header_focus']=headers
        if headers:result['text']+='\n\n[見出しの部分再読取]\n'+'\n\n'.join(p['text'] for p in headers)
    result['seconds']=round(time.perf_counter()-started,3)
    (folder / "ocr.json").write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
    return result


def normalized(text):
    return re.sub(r"\s+", "", unicodedata.normalize("NFKC", text)).lower()


def dates_in(text):
    text = unicodedata.normalize("NFKC", text)
    text=re.sub(r'(令和|平成)([〇零一壱二弐三参四五六七八九十拾]+)年([〇零一二三四五六七八九十]+)月([〇零一二三四五六七八九十]+)日',
                lambda m:f'{m[1]}{kanji_amount(m[2]+"円")}年{kanji_amount(m[3]+"円")}月{kanji_amount(m[4]+"円")}日',text)
    result = set()
    for y, m, d in re.findall(r"((?:19|20)\d{2})\s*[年/.-]\s*(\d{1,2})\s*[月/.-]\s*(\d{1,2})", text):
        try:
            result.add(date(int(y), int(m), int(d)).isoformat())
        except ValueError:
            pass
    for era, y, m, d in re.findall(r"(令和|平成|R|H)[.]?\s*(元|\d{1,2})\s*[年/.-]\s*(\d{1,2})\s*[月/.-]\s*(\d{1,2})", text, re.I):
        try:
            year = (2018 if era in ["令和", "R", "r"] else 1988) + (1 if y == "元" else int(y))
            result.add(date(year, int(m), int(d)).isoformat())
        except ValueError:
            pass
    months={'january':1,'february':2,'march':3,'april':4,'may':5,'june':6,'july':7,'august':8,'september':9,'october':10,'november':11,'december':12}
    for month,d,y in re.findall(r'\b([A-Za-z]+)\s+(\d{1,2}),?\s+((?:19|20)\d{2})\b',text):
        month_num=months.get(month.lower()) or next((v for k,v in months.items() if month.lower()==k[:3]),None)
        if month_num:
            try:result.add(date(int(y),month_num,int(d)).isoformat())
            except ValueError:pass
    for d,month,y in re.findall(r'\b(\d{1,2})[ -]+([A-Za-z]+)[ -]+((?:19|20)\d{2})\b',text):
        month_num=months.get(month.lower()) or next((v for k,v in months.items() if month.lower()==k[:3]),None)
        if month_num:
            try:result.add(date(int(y),month_num,int(d)).isoformat())
            except ValueError:pass
    return result


def money_in(text):
    return [value for value,_,_ in money_tokens(text)]


def strong_totals(text):
    return [(c['value'],c['evidence']) for c in total_candidates(text)]


def fallback(text):
    dates = dates_in(text)
    totals = strong_totals(text)
    unique = {x[0] for x in totals}
    return {**Draft().model_dump(), "date": next(iter(dates)) if len(dates) == 1 else "",
            "total": next(iter(unique)) if len(unique) == 1 else None,
            "reason": "意味判断モデルが実行できていません。OCR本文を見ながら入力できます。",
            "confidence": "low", "model": None, "warnings": ["AI分類未実行"], "evidence": {}}


def local_models():
    with httpx.Client(trust_env=False, timeout=3) as client:
        response = client.get(config.OLLAMA_URL + "/api/tags")
        response.raise_for_status()
        return response.json().get("models", [])


def classify(ocr: dict, business_context="", rules=None, accounts=None, policies=None):
    from .semantic import classify as local_classify
    return local_classify(ocr,business_context,rules,accounts,policies)


def ground(candidate, text, accounts=None, policies=None):
    accounts=default_accounts() if accounts is None else accounts
    candidate = dict(candidate)
    notes = list(candidate.get("warnings", []))[:8]
    evidence = {}
    for field in ["date", "merchant", "total"]:
        raw = candidate.pop(field + "_evidence", "")
        supported = bool(raw and normalized(raw) in normalized(text))
        if field == "date":
            source_dates=dates_in(raw) if supported else set()
            if len(source_dates)==1 and candidate[field] not in source_dates:
                candidate[field]=next(iter(source_dates))
                notes.append("日付をOCRの根拠行から正規化しました。原本と照合してください")
            supported = supported and candidate[field] in source_dates
        elif field == "merchant":
            supported = supported and bool(candidate[field]) and normalized(candidate[field]) in normalized(raw)
        else:
            supported = supported and (candidate[field] in money_in(raw) or kanji_amount(raw)==candidate[field])
            # An amount from tender/change/subtotal is not evidence for total.
            if re.search(r"お預[りか]|お釣|小計|釣銭", raw) and not re.search(r"(?<!小)合計|支払.*金額|領収金額|\btotal\b", raw, re.I):
                supported = False
            if not supported and candidate[field] is not None:
                # Deterministic cash consistency check is recorded, not hidden.
                value,_,_,checks=extract_total(text)
                supported=any(c['value']==candidate[field] and (c.get('cash_check') or c.get('tax_check')) for c in checks)
        if candidate[field] not in [None, ""] and not supported:
            notes.append({"date": "取引日", "merchant": "支払先", "total": "合計金額"}[field] + "の根拠をOCR本文で確認できないため、候補を空欄にしました")
            candidate[field] = None if field == "total" else ""
        if supported:
            evidence[field] = raw
    resolved,_,_,source_candidates=extract_total(text)
    totals=[(c['value'],c['evidence']) for c in source_candidates]
    if candidate["total"] is not None and resolved is not None and candidate['total']!=resolved:
        candidate["total"] = None
        notes.append("AIの合計とOCRの合計行が一致しません。原本から金額を入力してください")
    if len({v for v, _ in totals}) > 1:
        notes.append("異なる合計金額が複数あります。領収書の単位を確認してください")
    canonical=resolve_account(candidate["account"],accounts)
    mentioned_codes=set(re.findall(r"(?<![A-Z0-9])(?:E|A)\d{3}(?!\d)",candidate.get("reason","")))
    if canonical and mentioned_codes and canonical["code"] not in mentioned_codes:
        notes.append("AIが選択した科目コードと説明が一致しないため、科目を保留しました")
        canonical=None
    candidate["account"] = canonical["name"] if canonical else ""
    number = unicodedata.normalize("NFKC", candidate["invoice_number"])
    candidate["invoice_number"] = number if re.fullmatch(r"T\d{13}", number) and normalized(number) in normalized(text) else ""
    tax = candidate["tax_amount"]
    tax_lines = [line for line in text.splitlines() if re.search(r"消費税|内税|税額", line)]
    if tax is not None and (not any(tax in money_in(line) for line in tax_lines) or (candidate["total"] is not None and tax > candidate["total"])):
        candidate["tax_amount"] = None
        notes.append("記載消費税を確かめられないため空欄にしました")
    rates = set(re.findall(r"(?<!\d)(8|10)\s*[%％]", unicodedata.normalize("NFKC", text)))
    if candidate["tax_rate"] in ["8", "10"] and candidate["tax_rate"] not in rates:
        candidate["tax_rate"] = "unknown"
    if candidate["tax_rate"] == "mixed" and rates != {"8", "10"}:
        candidate["tax_rate"] = "unknown"
    if candidate["tax_rate"] == "exempt" and not re.search(r"非課税|不課税|免税", text):
        candidate["tax_rate"] = "unknown"
    if rates == {"8", "10"}:
        candidate["tax_rate"] = "mixed"
    if canonical and canonical["kind"]=="asset":
        notes.append("資産候補です。取得価額・耐用年数・減価償却は別途確認してください")
    candidate["evidence"] = evidence
    candidate["warnings"] = list(dict.fromkeys(notes))
    if any(not candidate.get(k) for k in ["date", "merchant", "total", "account"]):
        candidate["confidence"] = "low"
    # Final schema validation also bounds strings before persistence.
    safe = {k: candidate[k] for k in Draft.model_fields if k in candidate}
    try:
        Draft.model_validate(safe)
    except ValueError as exc:
        raise ValueError("AI候補の形式が正しくありません。OCR本文から手入力できます") from exc
    return apply_account_policies(candidate,text,accounts,policies)


def process(store, receipt_id):
    ocr = None
    try:
        current = store.get(receipt_id)
        folder = store.folder(receipt_id)
        store.stage(receipt_id, "ocr")
        ocr = read_receipt(folder, current["extension"])
        store.stage(receipt_id, "model")
        suggestion = classify(ocr, store.business_context(), store.rules(),store.accounts(),store.policies())
        (folder / "suggestion.json").write_text(json.dumps(suggestion, ensure_ascii=False, indent=2), encoding="utf-8")
        store.complete(receipt_id, ocr, suggestion)
    except Exception as exc:
        if isinstance(exc, httpx.ConnectError):
            error = "Ollamaに接続できません。起動後に再読取するか、OCR本文から入力できます。"
        elif isinstance(exc, httpx.TimeoutException):
            error = "意味判断モデルが時間内に完了しませんでした。再読取するか、OCR本文から入力できます。"
        else:
            error = str(exc)[:500]
        suggestion=apply_account_policies(fallback(ocr["text"]),ocr["text"],store.accounts(),store.policies()) if ocr else None
        store.complete(receipt_id, ocr, suggestion, error)
