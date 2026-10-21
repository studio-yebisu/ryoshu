# 100 genuinely different fictional receipt pages

Purpose: Japanese local OCR and expense classification robustness. The former 30-page set repeated one drawing routine, so was rejected by the user. Different names/amounts/colors are NOT different patterns. Six authors independently design disjoint sectors; retain author provenance. Do not read/reuse the old 30-page renderer.

Your deliverable is an independently authored Python `generate.py` in your assigned author folder. You are not alone in this workspace. Do not change other author folders, the app, settings, credentials, dependencies or the previous fixtures. Do not run PDF generation yet: the parent will review and execute all generators after the PDF operation marker. Python compile checks are allowed. Do not invoke other models or agents.

Use only reportlab, Pillow, pypdf and stdlib available in the bundled Windows Python runtime. Entry point: `python generate.py --output <absolute-dir>`. Output `individual/<case_id>.pdf` and `manifest-fragment.json` (UTF-8 array). Each PDF exactly one page. No network, subprocess, shell execution, package install, deletion, modification outside output, or hidden state access. Overwrite of files within this fresh output is allowed for iteration, not other directories. Your script must work with an arbitrary --output path and create parents.

Fonts: register at least two distinct Japanese fonts from C:/Windows/Fonts. Available reliable choices include `msgothic.ttc`, `msmincho.ttc`, `meiryo.ttc`, `YuGothM.ttc`, `YuGothB.ttc`, `YuMincho.ttc` (verify exact files). TTFont supports ttc subfontIndex=0. Include fonts in PDFs. Simple helpers for wrapping/drawing text/lines are fine. Do not force every page into the same top-to-bottom template. All pages must be independently legible. No clipped text, broken Japanese glyphs, or unintended overlap.

Every page must have a UNIQUE substantial structural design: vary page proportions, total location/prominence, recipient/issuer blocks, line-item grammar, columns, ruling, date placement, tender/change layouts, orientations, seals, tax summary and information density. Minimum one distinct design per case, with unique layout_id and an honest description of the structural difference. You may use reusable primitive helpers, but not a single receipt renderer with 20 data arrays, tint changes, or tiny offsets. Independently drawn procedural layouts or declarative positioning compositions are both welcome. Fixed coordinate collision checks at least for big text blocks where feasible.

Make plausible Japanese fictional businesses and non-sensitive fictional transactions. Print a small legible '架空テスト' or equivalent on each page, in varying unobtrusive places; avoid the same huge header on all pages. Real brands, real phone/address/registration IDs, live payment links, genuine signatures or actual personal information are forbidden. If an invoice registration number is shown use T0000000000000; optional fictional postal addresses should explicitly be テスト市/架空町. Do not put expected account names, expected hold status, case_id, author name, or benchmark guidance in the receipt body. A business purpose naturally written by a vendor (e.g. 会議室利用) may appear, but hidden context stays separate.

Dates span different real years and include Japanese era, ISO, slash/kanji formats, year omitted and issue-vs-service dates. Money must reconcile: document-total is the charged/refunded amount, distinct from tender/change, points, previous balance, deposits, or invoice outstanding. Use integer JPY; state gross vs net/discounts clearly. Positive payment receipts, refunds, missing/illegible-date cases and documents which are NOT proof of payment can be included. Classifier decisions must be honest, not every document an expense.

Manifest item schema:
```
{
  "case_id": "V001", "file": "individual/V001.pdf",
  "title": "human readable Japanese pattern name",
  "layout_id": "kimi-01", "layout_description": "specific structural description",
  "document_kind": "thermal_receipt|voucher|invoice_receipt|statement|refund|invoice_unpaid|other",
  "merchant": "fictional printed issuer", "date": "YYYY-MM-DD or empty",
  "date_printed": "exact printed transaction date or empty",
  "total": 1234,
  "acceptable_accounts": ["消耗品費"],
  "expected_decision": "propose|hold|block|asset_review",
  "human_context": "not printed; actual business/private situation and information missing",
  "review_questions": ["..."],
  "features": ["58mm thermal", "change amount", "two VAT rates"],
  "items": [{"name": "...", "gross": 1234, "rate": "10|8|exempt|unknown"}]
}
```
For unpaid invoices expected_decision=block and explain it is not proof of payment. For missing year/date use date empty, do not infer current year. Refund total must be negative and expected_decision=block. Mixed business/private baskets and unknown meals should generally hold with empty acceptable_accounts. For valuable equipment use asset_review, never assume a tax exemption. Accounting choices are candidate alternatives, not legal determinations. Keep tax arithmetic consistent if printed. Do not invent any legal thresholds.

No scan transformations yet: output clean embedded-font PDFs. The parent will derive varied scan, skew, fax, camera perspective, faded and rotation variants while preserving the authored document structures and provenance. Output only assigned IDs; include all required cases and no placeholder duplicates. Parent will combine to 100 pages, rasterize all for visual QA, validate arithmetic/manifest/hash/page count, and keep truth data outside model inputs.

Generation environment: Windows Python with ReportLab, Pillow, pypdf and Poppler. The original machine-specific runtime path is omitted from this public brief.
