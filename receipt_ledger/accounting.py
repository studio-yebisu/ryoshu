import csv
import io
from collections import defaultdict
from decimal import Decimal, ROUND_HALF_UP

from .config import PAYMENTS, TAX_LABELS
from .schemas import Draft
from .catalog import default_accounts


def is_asset(row):
    if row.get("account_kind"):
        return row["account_kind"] == "asset"
    return any(a["name"]==row["draft"]["account"] and a["kind"]=="asset" for a in default_accounts())


def posted_pairs(row):
    posting=row.get("posting")
    if not posting:
        return journal(row["draft"])
    credits=[line for line in posting["lines"] if line["side"]=="credit"]
    if len(credits)!=1:
        raise ValueError("経費仕訳の貸方を確認してください")
    return [{"debit":line["name"],"credit":credits[0]["name"],"amount":line["amount"]} for line in posting["lines"] if line["side"]=="debit"]


def amounts(draft: dict):
    total = draft.get("total") or 0
    percent = draft.get("business_percent")
    business = int((Decimal(total) * Decimal(percent or 0) / 100).quantize(Decimal(1), rounding=ROUND_HALF_UP))
    return business, total - business


def journal(draft: dict):
    """Tax-inclusive journal; no input VAT deduction or depreciation is calculated."""
    Draft.model_validate(draft)
    business, private = amounts(draft)
    payment = draft["payment"]
    if not payment or not draft["account"]:
        return []
    credit = PAYMENTS[payment]["credit"]
    rows = [{"debit": draft["account"], "credit": credit, "amount": business}]
    if payment != "owner" and private:
        rows.append({"debit": "事業主貸", "credit": credit, "amount": private})
    return [r for r in rows if r["amount"] > 0]


def confirmed(records, month=""):
    return sorted([r for r in records if r["status"] == "confirmed" and r["draft"]["date"].startswith(month)], key=lambda r: (r["draft"]["date"], r["id"]))


def summary(records, month=""):
    rows = confirmed(records, month)
    categories = defaultdict(lambda: {"amount": 0, "count": 0})
    expenses = assets = total = 0
    for row in rows:
        draft = row["draft"]
        amount, _ = amounts(draft)
        total += amount
        if is_asset(row):
            assets += amount
        else:
            expenses += amount
        categories[draft["account"]]["amount"] += amount
        categories[draft["account"]]["count"] += 1
    return {"count": len(rows), "total": total, "expense_total": expenses, "asset_total": assets,
            "categories": [{"name": k, **v} for k, v in sorted(categories.items(), key=lambda kv: -kv[1]["amount"])],
            "pending": sum(r["status"] in ["review", "error", "queued", "processing"] for r in records)}


def safe_cell(value):
    if not isinstance(value, str):
        return value
    # Prevent spreadsheet formula interpretation of user/OCR-controlled text.
    if value.lstrip().startswith(("=", "+", "-", "@")) or value.startswith(("\t", "\r", "\n")):
        return "'" + value
    return value


def csv_export(records, month="", kind="expenses"):
    output = io.StringIO(newline="")
    writer = csv.writer(output)
    if kind == "journal":
        writer.writerow(["取引日", "借方科目", "借方金額", "貸方科目", "貸方金額", "支払先", "摘要", "証憑ID", "税処理"])
    else:
        writer.writerow(["取引日", "支払先", "勘定科目", "摘要", "業務での用途", "領収書合計", "事業割合(%)", "事業分金額", "私用分金額", "支払方法", "記載税率", "記載消費税", "記載登録番号", "証憑ID", "原本ファイル名", "原本SHA256"])
    for row in confirmed(records, month):
        d = row["draft"]
        if kind == "journal":
            for entry in posted_pairs(row):
                writer.writerow([safe_cell(v) for v in [d["date"], entry["debit"], entry["amount"], entry["credit"], entry["amount"], d["merchant"], d["description"] + " / " + d["purpose"], row["id"], "税込・消費税仕訳なし"]])
        else:
            business, private = amounts(d)
            writer.writerow([safe_cell(v) for v in [d["date"], d["merchant"], d["account"], d["description"], d["purpose"], d["total"], d["business_percent"], business, private, PAYMENTS[d["payment"]]["label"], TAX_LABELS[d["tax_rate"]], d["tax_amount"], d["invoice_number"], row["id"], row["original_name"], row["sha256"]]])
    return output.getvalue().encode("utf-8-sig")
