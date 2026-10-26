"""Reviewed, tax-inclusive journal exports. No VAT eligibility is inferred by OCR."""
import csv
import io
import unicodedata
from dataclasses import dataclass

from .accounting import amounts, confirmed, csv_export, safe_cell

TAX_CHOICES = {
    "": "未確認（会計ソフト向け出力前に選択）",
    "outside": "対象外・免税事業者の税込記帳",
    "non_taxable": "非課税仕入（内容を確認済み）",
    "purchase10": "課税仕入10%・適格等の要件を確認済み",
    "purchase8": "課税仕入8%軽減・適格等の要件を確認済み",
    "transition10": "課税仕入10%・区分記載の経過措置を確認済み",
    "transition8": "課税仕入8%軽減・区分記載の経過措置を確認済み",
}
PROFILES = {
    "generic": {"label": "汎用CSV", "encoding": "UTF-8 BOM", "route": "経費帳・仕訳・確認待ちの一覧", "source": ""},
    "freee": {"label": "freee", "encoding": "UTF-8 BOM", "route": "振替伝票 → インポート → 他社会計ソフトインポート（名称列・税込金額）", "source": "https://support.freee.co.jp/hc/ja/articles/204847430"},
    "mf": {"label": "MFクラウド", "encoding": "UTF-8 BOM", "route": "会計帳簿 → 仕訳帳 → インポート → 仕訳帳", "source": "https://biz.moneyforward.com/support/account/guide/import-books/ib01.html"},
    "yayoi": {"label": "弥生", "encoding": "Shift-JIS (CP932)", "route": "弥生会計・やよいの青色申告の仕訳インポート（25項目・税込経理）", "source": "https://support.yayoi-kk.co.jp/subcontents.html?page_id=18545"},
}
FREEE_HEADER = ["[表題行]", "日付", "伝票番号", "決算整理仕訳", "借方勘定科目", "借方補助科目", "借方取引先", "借方金額", "借方税区分", "借方税額", "貸方勘定科目", "貸方補助科目", "貸方取引先", "貸方金額", "貸方税区分", "貸方税額", "摘要"]
MF_HEADER = ["取引No", "取引日", "借方勘定科目", "借方補助科目", "借方部門", "借方取引先", "借方税区分", "借方インボイス", "借方金額(円)", "借方税額", "貸方勘定科目", "貸方補助科目", "貸方部門", "貸方取引先", "貸方税区分", "貸方インボイス", "貸方金額(円)", "貸方税額", "摘要", "仕訳メモ", "タグ", "MF仕訳タイプ", "決算整理仕訳", "作成日時", "作成者", "最終更新日時", "最終更新者"]
YAYOI_HEADER = ["識別フラグ", "伝票No.", "決算", "取引日付", "借方勘定科目", "借方補助科目", "借方部門", "借方税区分", "借方金額", "借方税金額", "貸方勘定科目", "貸方補助科目", "貸方部門", "貸方税区分", "貸方金額", "貸方税金額", "摘要", "番号", "期日", "タイプ", "生成元", "仕訳メモ", "付箋1", "付箋2", "調整"]


@dataclass
class Export:
    columns: list
    rows: list
    issues: list
    warnings: list
    encoding: str = "utf-8-sig"
    header: bool = True
    receipt_count: int = 0
    debit_total: int = 0
    credit_total: int = 0

    def content(self):
        if self.issues:
            raise ValueError("\n".join(f"{v['file']}: {v['message']}" for v in self.issues[:20]))
        output = io.StringIO(newline="")
        writer = csv.writer(output, lineterminator="\r\n")
        if self.header:
            writer.writerow(self.columns)
        writer.writerows([[safe_cell(c) for c in r] for r in self.rows])
        return output.getvalue().encode(self.encoding, errors="strict")

    def preview(self):
        return {"columns": self.columns, "rows": self.rows[:5], "row_count": len(self.rows), "receipt_count": self.receipt_count,
                "debit_total": self.debit_total, "credit_total": self.credit_total, "issues": self.issues, "warnings": self.warnings,
                "encoding": self.encoding, "includes_header": self.header, "ready": not self.issues}


def transition_percent(day):
    # Source: each vendor's 2026 invoice documentation, recorded in docs/CSV-EXPORT.md.
    if "2023-10-01" <= day < "2026-10-01": return 80
    if "2026-10-01" <= day < "2028-10-01": return 70
    if "2028-10-01" <= day < "2030-10-01": return 50
    if "2030-10-01" <= day < "2031-10-01": return 30
    raise ValueError("取引日が経過措置の対応期間外です。原本と税区分を個別に確認してください")


def tax_labels(treatment, day):
    if treatment == "outside": return {"freee": "対象外", "mf": "対象外", "yayoi": "対象外", "invoice": ""}
    if treatment == "non_taxable": return {"freee": "非課仕入", "mf": "非課税仕入", "yayoi": "非課仕入", "invoice": ""}
    if treatment not in TAX_CHOICES or not treatment:
        raise ValueError("会計ソフト向けの消費税区分が未選択です。明細を開いて区分を確認してください")
    rate = "10" if treatment.endswith("10") else "8"
    light = "（軽）" if rate == "8" else ""
    transitional = treatment.startswith("transition")
    pct = transition_percent(day) if transitional else 100
    return {"freee": f"課対仕入（控{pct}）{rate}%{light}" if transitional else f"課対仕入{rate}%{light}",
            "mf": f"課税仕入 {rate}%" + ("（軽）" if rate == "8" else ""),
            "yayoi": f"課対仕入込{' 軽減' if rate == '8' else ''}{rate}%" + (f"区分{pct}%" if transitional else "適格"),
            "invoice": f"{pct}％控除" if transitional else "適格"}


def review_export(records, month=""):
    columns = ["状態", "取引日候補", "支払先候補", "合計金額候補", "勘定科目候補", "判定", "確認すること", "エラー", "証憑ID", "原本ファイル名", "原本SHA256"]
    result = Export(columns, [], [], ["確認待ち・対象外を含む候補の一覧です。会計ソフトの仕訳としては取り込まないでください。"])
    names = {"queued":"順番待ち", "processing":"読み取り中", "review":"確認待ち", "error":"要再確認", "confirmed":"登録済み", "excluded":"対象外"}
    for r in sorted(records, key=lambda r: (r["draft"].get("date", ""), r["id"])):
        d=r["draft"]; s=r.get("suggestion") or {}
        if month and d.get("date") and not d["date"].startswith(month): continue
        notes = list(dict.fromkeys(s.get("blocking_reasons", []) + s.get("review_questions", []) + s.get("warnings", [])))
        result.rows.append([names.get(r["status"], r["status"]), d.get("date", ""), d.get("merchant", ""), d.get("total"), d.get("account", ""), s.get("decision", ""), " / ".join(notes), r.get("error", ""), r["id"], r["original_name"], r["sha256"]])
    result.receipt_count = len(result.rows)
    return result


def build_export(records, profile="generic", month="", kind="journal", tax_mode="reviewed", account_map=None):
    if profile not in PROFILES: raise ValueError("出力形式が正しくありません")
    if tax_mode not in {"reviewed", "exempt"}: raise ValueError("消費税の出力設定が正しくありません")
    if profile == "generic":
        if kind == "review": return review_export(records, month)
        if kind not in {"journal", "expenses"}: raise ValueError("汎用CSVの種類が正しくありません")
        rows = list(csv.reader(io.StringIO(csv_export(records, month, kind).decode("utf-8-sig"))))
        # csv_export already neutralizes cells. Do not add a second prefix here.
        result = Export(rows[0], rows[1:], [], [], receipt_count=len(confirmed(records, month)))
        return result
    if kind != "journal": raise ValueError("会計ソフト向けCSVには確認済みの仕訳だけを出力できます")
    account_map = account_map or {}
    columns = {"freee": FREEE_HEADER, "mf": MF_HEADER, "yayoi": YAYOI_HEADER}[profile]
    result = Export(columns, [], [], [], "cp932" if profile == "yayoi" else "utf-8-sig", profile != "yayoi")
    if tax_mode == "exempt": result.warnings.append("免税事業者の税込記帳として、全行の消費税区分を対象外で出力します。")
    result.warnings.append("同じCSVを再取り込みすると重複することがあります。取込先で件数・借貸合計・科目の対応を確認してください。")
    if profile == "freee": result.warnings.append("振替伝票として取り込みます。名称列と税込金額を選択し、口座・科目の対応を確認してください。取引先列には支払先名を出力します。freeeでは未登録の取引先が新しいタグとして登録されます。")
    if profile == "yayoi": result.warnings.append("25項目・ヘッダーなし。摘要は半角64桁、仕訳メモは半角180桁までです。長い内容は証憑IDを残して短縮します。")
    def issue(r, message): result.issues.append({"id":r["id"], "file":r["original_name"], "message":message})
    for number, r in enumerate(confirmed(records, month), 1):
        d=r["draft"]; posting=r.get("posting")
        if not posting or posting.get("status", "posted") != "posted":
            issue(r, "保存済みの仕訳が見つかりません。確認画面で再登録してください"); continue
        debit=[l for l in posting["lines"] if l["side"]=="debit"]
        credit=[l for l in posting["lines"] if l["side"]=="credit"]
        ds=sum(l["amount"] for l in debit); cs=sum(l["amount"] for l in credit)
        if not debit or not credit or ds!=cs or any(type(l["amount"]) is not int or l["amount"]<=0 for l in debit+credit):
            issue(r, "保存済み仕訳の貸借・金額を確認してください"); continue
        try: tax=tax_labels("outside" if tax_mode=="exempt" else d.get("export_tax", ""), d["date"])
        except ValueError as exc: issue(r,str(exc));continue
        if d.get("tax_rate")=="mixed" and d.get("export_tax") not in {"outside", "non_taxable"} and tax_mode!="exempt":
            issue(r,"8%と10%が混在しています。汎用CSVと原本を使い、会計ソフト側で内訳を分けてください");continue
        mapped={}
        failed=False
        for line in debit+credit:
            name=account_map.get(line["name"], line["name"])
            if not isinstance(name,str) or not name.strip(): issue(r,"科目の出力名を入力してください");failed=True;continue
            if name.lstrip().startswith(("=","+","-","@")) or any(c in name for c in "\r\n\t"):
                issue(r,"科目名にCSVへ出力できない文字があります。科目の対応名を設定してください");failed=True;continue
            limit=24 if profile=="yayoi" else 30
            length=len(name.encode("cp932",errors="replace")) if profile=="yayoi" else len(name)
            if length>limit: issue(r,f"科目名「{name}」が取込先の長さ制限を超えます。出力名を短く設定してください");failed=True
            mapped[line["name"]]=name
        if failed: continue
        memo=f"証憑:{r['id']} {d.get('description','')} / {d.get('purpose','')}"
        desc=f"{d['merchant']} {d.get('description','')} / {d.get('purpose','')}"
        if profile=="yayoi":
            original_description=(desc,memo)
            desc=unicodedata.normalize('NFKC',desc).replace('¥','￥').replace('—','―').replace('–','-')
            memo=unicodedata.normalize('NFKC',memo).replace('¥','￥').replace('—','―').replace('–','-')
            if original_description!=(desc,memo):
                warning='弥生の文字コードに合わせ、摘要・メモの単位や丸数字等を標準表記へ変換しました。元の帳簿は保持します。'
                if warning not in result.warnings:result.warnings.append(warning)
            try:
                for name in mapped.values(): name.encode('cp932',errors='strict')
                (desc+memo).encode('cp932',errors='strict')
            except UnicodeEncodeError:
                issue(r,"Shift-JISで表せない文字があります。汎用・freee・MF形式を使うか、表示名を修正してください");continue
        count=max(len(debit),len(credit))
        for i in range(count):
            dl=debit[i] if i<len(debit) else None;cl=credit[i] if i<len(credit) else None
            dn=mapped[dl['name']] if dl else '';cn=mapped[cl['name']] if cl else ''
            da=dl['amount'] if dl else '';ca=cl['amount'] if cl else ''
            dt=tax[profile] if dl and dl['name']!='事業主貸' else '対象外'
            ct='対象外';day=d['date'].replace('-','/')
            if profile=='freee':
                row=['[明細行]',day,number,'',dn,'',d['merchant'] if dl else '',da,dt if dl else '',0 if dt=='対象外' and dl else '',cn,'','',ca,ct if cl else '',0 if cl else '',clip_text(desc,1024)]
            elif profile=='mf':
                row=[number,day,dn,'','',d['merchant'] if dl else '',dt if dl else '',tax['invoice'] if dl and dl['name']!='事業主貸' else '',da,0 if dl else '',cn,'','','',ct if cl else '','',ca,0 if cl else '',clip_text(desc,200),clip_text(memo,200),'','インポート','','','','','']
            else:
                flag='2000' if count==1 else '2110' if i==0 else '2101' if i==count-1 else '2100'
                row=[flag,'','',day,dn,'','',dt,da or 0,0,cn,'','',ct,ca or 0,0,clip_bytes(desc,64),'','','0','',clip_bytes(memo,180),0,0,'no']
            if len(desc)>(1024 if profile=='freee' else 200) or profile=='yayoi' and len(desc.encode('cp932'))>64:
                warning='長い摘要を取込先の制限に合わせて短縮しました。完全な内容は汎用CSV・原本を参照できます。'
                if warning not in result.warnings: result.warnings.append(warning)
            result.rows.append(row)
        result.receipt_count+=1;result.debit_total+=ds;result.credit_total+=cs
    return result


def clip_text(text, limit):
    return text if len(text)<=limit else text[:limit-1]+"…"


def clip_bytes(text, limit):
    # CP932 character boundary, never a partial multibyte sequence.
    raw=text.encode("cp932")
    return text if len(raw)<=limit else raw[:limit-3].decode("cp932",errors="ignore")+"..."
