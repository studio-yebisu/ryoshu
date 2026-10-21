"""Read-only fixture inspection. Writes only the requested JSON report."""
import argparse
from collections import Counter
from datetime import date
import hashlib
import json
from pathlib import Path
import re
from pypdf import PdfReader


def inspect(root):
    errors, warnings, observations = [], [], []
    dimensions, authors, modes = Counter(), Counter(), Counter()
    layouts, hashes = [], []
    def confined(relative):
        if not isinstance(relative, str):
            raise ValueError('file path is not a string')
        candidate = (root / relative).resolve()
        if not candidate.is_relative_to(root):
            raise ValueError('path escapes input folder: ' + relative)
        if not candidate.is_file():
            raise ValueError('missing file: ' + relative)
        return candidate
    try:
        manifest = json.loads(confined('manifest.json').read_text(encoding='utf-8'))
        cases = manifest['cases']
        if not isinstance(cases, list):
            raise ValueError('cases must be an array')
    except Exception as exc:
        return dict(ok=False, errors=[f'manifest: {exc}'], warnings=[], observations=[])
    ids = [c.get('case_id') for c in cases]
    expected = {f'V{i:03d}' for i in range(1,101)}
    if len(cases) != 100 or set(ids) != expected or len(set(ids)) != len(ids):
        errors.append(dict(check='case_ids', count=len(cases), missing=sorted(expected-set(ids)), unexpected=[i for i in ids if i not in expected], duplicates=[i for i,n in Counter(ids).items() if n>1]))
    for c in cases:
        cid = c.get('case_id', '?')
        authors[str(c.get('author', 'missing'))] += 1
        acquisition = c.get('acquisition', {})
        mode = acquisition.get('mode', 'missing') if isinstance(acquisition, dict) else 'invalid'
        modes[str(mode)] += 1
        layout = c.get('layout_id')
        if not isinstance(layout, str) or not layout:
            errors.append(f'{cid}: missing layout_id')
        else:
            layouts.append(layout)
        dt = c.get('date')
        if dt:
            try:
                if not re.fullmatch(r'\d{4}-\d{2}-\d{2}', dt):
                    raise ValueError('expected YYYY-MM-DD')
                date.fromisoformat(dt)
            except (ValueError, TypeError) as exc:
                errors.append(f'{cid}: invalid date {dt!r}: {exc}')
        total, items = c.get('total'), c.get('items', [])
        # Only explicit plain sums qualify. Deposits, withholding, currency,
        # prior balances and unmarked transactions remain manual inspection.
        explicit_sum = c.get('arithmetic_mode') in ('sum_items', 'no_adjustments') or c.get('no_adjustments') is True
        adjustment_keys = ('adjustments','deposit','deposits','previous_balance','reference_jpy','withholding','currency')
        has_adjustments = any(c.get(k) not in (None, [], {}, 0, '', 'JPY') for k in adjustment_keys)
        gross = [it.get('gross') for it in items] if isinstance(items, list) and all(isinstance(it,dict) for it in items) else []
        numeric = lambda x: isinstance(x, (int,float)) and not isinstance(x,bool)
        if explicit_sum and not has_adjustments and gross and numeric(total) and all(numeric(n) for n in gross):
            if sum(gross) != total:
                warnings.append(f'{cid}: explicit unadjusted item sum {sum(gross)} differs from total {total}')
        else:
            observations.append(f'{cid}: automatic arithmetic comparison skipped (not explicitly unadjusted or incomplete amounts)')
        try:
            path = confined(c.get('file'))
            digest = hashlib.sha256(path.read_bytes()).hexdigest()
            hashes.append(digest)
            if c.get('sha256') != digest:
                errors.append(f'{cid}: sha256 missing or mismatched')
            reader = PdfReader(str(path))
            if len(reader.pages) != 1:
                errors.append(f'{cid}: expected one page, found {len(reader.pages)}')
            for page in reader.pages:
                width, height = float(page.mediabox.width), float(page.mediabox.height)
                dimensions[f'{width:.2f}x{height:.2f}'] += 1
                if width <= 0 or height <= 0:
                    errors.append(f'{cid}: invalid page dimensions')
                text = page.extract_text() or ''
                if text.strip():
                    # Field-name leakage is forbidden; natural vendor wording
                    # such as 会議室利用 or 消耗品 is deliberately not flagged.
                    for token in ('acceptable_accounts','expected_decision','human_context','review_questions','expected account','勘定科目','期待勘定科目','正解勘定科目','推奨勘定科目','expected_decision='):
                        if token.lower() in text.lower():
                            errors.append(f'{cid}: benchmark label visible in text layer: {token}')
                    clean = not any(t in str(mode).lower() for t in ('scan','camera','fax','faded','skew','rotation','raster'))
                    if clean and not re.search(r'架空|fictional|fiction\s*test|test\s*document', text, re.I):
                        warnings.append(f'{cid}: clean PDF has no recognizable fiction signal in extracted text')
                    # Text positions are only a coarse diagnostic, not glyph
                    # bounding boxes. Rotation and transformed text may differ.
                    outside = []
                    def visit(txt, cm, tm, font, size):
                        if not txt.strip(): return
                        x = tm[4]*cm[0] + tm[5]*cm[2] + cm[4]
                        y = tm[4]*cm[1] + tm[5]*cm[3] + cm[5]
                        if x < -5 or y < -5 or x > width+5 or y > height+5:
                            outside.append(txt.strip()[:60])
                    try:
                        page.extract_text(visitor_text=visit)
                        if outside: warnings.append(dict(case_id=cid, check='text_origins_outside_media', samples=outside[:8]))
                    except Exception as exc:
                        warnings.append(f'{cid}: optional text-coordinate inspection unavailable: {exc}')
                else:
                    observations.append(f'{cid}: no usable text layer; visible label and fiction-signal checks skipped')
        except Exception as exc:
            errors.append(f'{cid}: PDF inspection failed: {exc}')
    for label, values in [('layout_id',layouts),('sha256',hashes)]:
        dup = {key:n for key,n in Counter(values).items() if n>1}
        if dup: errors.append(dict(check='duplicate_'+label, duplicates=dup))
    try:
        combined = PdfReader(str(confined('receipts-100-review.pdf')))
        combined_count = len(combined.pages)
        if combined_count != 100: errors.append(f'combined PDF: expected 100 pages, found {combined_count}')
    except Exception as exc:
        combined_count = None
        errors.append(f'combined PDF: {exc}')
    try:
        import zipfile
        with zipfile.ZipFile(confined('receipts-100.zip')) as archive:
            members = archive.namelist()
            unsafe = [n for n in members if n.startswith(('/', '\\')) or '..' in n.replace('\\','/').split('/') or re.match(r'^[A-Za-z]:',n)]
            if unsafe: errors.append(dict(check='unsafe_zip_paths', members=unsafe))
            bad = archive.testzip()
            if bad: errors.append(f'ZIP CRC failure: {bad}')
    except Exception as exc:
        errors.append(f'ZIP: {exc}')
    return dict(ok=not errors, errors=errors, warnings=warnings, observations=observations, summary=dict(cases=len(cases), unique_layouts=len(set(layouts)), unique_hashes=len(set(hashes)), authors=dict(authors), acquisition_modes=dict(modes), dimensions=dict(dimensions), dimension_variants=len(dimensions), combined_pages=combined_count), limitations=['Text coordinate checks measure text origins, not complete glyph bounds.', 'Image-only PDFs require parent visual QA.', 'Arithmetic compares only explicitly unadjusted item sums; discrepancies are warnings.'])


def main():
    parser = argparse.ArgumentParser(); parser.add_argument('--input',required=True); parser.add_argument('--report',required=True)
    args = parser.parse_args(); root = Path(args.input).resolve(); report = Path(args.report).resolve()
    # Never permit the report to overwrite an inspected fixture or manifest.
    if report == root/'manifest.json' or report.suffix.lower() in ('.pdf','.zip'):
        parser.error('report must be a separate JSON report path')
    result = inspect(root)
    report.parent.mkdir(parents=True,exist_ok=True)
    report.write_text(json.dumps(result,ensure_ascii=False,indent=2),encoding='utf-8')
    print(json.dumps(dict(ok=result['ok'], errors=len(result['errors']), warnings=len(result['warnings']), report=str(report)),ensure_ascii=False))
    raise SystemExit(0 if result['ok'] else 1)


if __name__ == '__main__': main()
