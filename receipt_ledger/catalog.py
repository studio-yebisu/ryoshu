"""Versioned seed data. The live catalogue is held in each user's SQLite DB."""
import json
import re
import unicodedata
from functools import lru_cache
from pathlib import Path


@lru_cache(maxsize=1)
def seed_catalog():
    return json.loads((Path(__file__).resolve().parents[1] / "data" / "account-catalog.json").read_text(encoding="utf-8"))


def default_accounts():
    return [a for a in seed_catalog()["accounts"]
            if a.get("selectable") and a.get("active", True) and a.get("kind") in {"expense", "asset"}]


def account_categories():
    return [dict(category, kinds=list(category["kinds"])) for category in seed_catalog()["account_categories"]]


def category_for(kind, category_id=None):
    fallback = "other" if kind == "expense" else "assets" if kind == "asset" else "settlement"
    selected = fallback if category_id in (None, "") else category_id
    category = next((c for c in account_categories() if c["id"] == selected), None)
    if category is None or kind not in category["kinds"]:
        raise ValueError("科目の種類に対応するカテゴリーを選択してください")
    return selected


def account_key(value):
    return re.sub(r"\s+", "", unicodedata.normalize("NFKC", value or "")).casefold()


def receipt_key(value):
    # Common visual OCR confusion, limited to the purchase word rather than names.
    return account_key(value).replace('モ二タ','モニタ')


def resolve_account(value, accounts):
    key = account_key(value)
    if not key:
        return None
    # A previously valid custom name can equal a newly added seed code.
    # Names take precedence, followed by codes, then historical aliases.
    # Draft.account stores formal names, so expanding the seed cannot retarget it.
    for account in accounts:
        if key == account_key(account["name"]):
            return account
    for account in accounts:
        if key == account_key(account["code"]):
            return account
    for account in accounts:
        if key in {account_key(v) for v in account.get("aliases", [])}:
            return account
    return None


def retrieve_accounts(text, accounts):
    """Retrieve candidates from editable item terms, without using any test answers."""
    lines=text.splitlines()
    # Do not use the shop name as the main purchase classifier when a header is identifiable.
    anchors=[i for i,line in enumerate(lines[:12]) if re.search(r"取引日|No[.：:]|領収日",line,re.I)]
    body="\n".join(lines[max(anchors)+1:]) if anchors else text
    haystack=receipt_key(body)
    from .extraction import document_flags
    negative_haystack=haystack
    if not document_flags(text)['refund']:
        negative_haystack=negative_haystack.replace('返品','').replace('返金','')
    hits=[]
    for account in accounts:
        hints=account.get("hints",{})
        if any(account_key(k) in negative_haystack for k in hints.get("unless_any",[])):
            continue
        terms=[k for k in hints.get("any",[]) if receipt_key(k) in haystack]
        if terms:
            hits.append({"account":account,"terms":terms})
    if not hits:
        return accounts,[]
    return [h["account"] for h in hits],[{"code":h["account"]["code"],"terms":h["terms"]} for h in hits]


def policy_matches(policy, text, total):
    match = policy["match"]
    if match.get('document_flag'):
        from .extraction import document_flags
        if not document_flags(text).get(match['document_flag']):return False
    if match.get('scope')=='purchases':
        lines=text.splitlines()
        text='\n'.join(l for i,l in enumerate(lines) if not re.search(r'御中|様|ご契約者|宛名|購入者|申請者|納品先|車名・登録',l) and not (i+1<len(lines) and re.match(r'^\s*(?:御中|様)(?:\s|$|[（(])',lines[i+1])))
    haystack = receipt_key(text)
    for phrase in match.get('ignore_phrases',[]):haystack=haystack.replace(receipt_key(phrase),'')
    contains = lambda words: any(receipt_key(w) in haystack for w in words)
    if match.get("any") and not contains(match["any"]):
        return False
    if match.get("unless_any") and contains(match["unless_any"]):
        return False
    if match.get("all_groups") and not all(contains(group) for group in match["all_groups"]):
        return False
    if "min_total" in match and (total is None or total < match["min_total"]):
        return False
    return True


def apply_account_policies(candidate, text, accounts=None, policies=None):
    accounts = default_accounts() if accounts is None else accounts
    policies = seed_catalog()["policies"] if policies is None else policies
    result = dict(candidate)
    notes = list(result.get("warnings", []))
    canonical = resolve_account(result.get("account", ""), accounts)
    result["account"] = canonical["name"] if canonical else ""
    matched, blocked, questions = [], [], []
    hold = False
    for policy in sorted(policies, key=lambda p: p["priority"]):
        if not policy_matches(policy, text, result.get("total")):
            continue
        matched.append({k: policy[k] for k in ["code", "title", "action", "message", "sources"]})
        notes.append(policy["message"])
        questions.extend(policy.get("questions", []))
        if policy["action"] == "block":
            blocked.append(policy["message"])
            hold = True
        if policy["action"] == "hold":
            hold = True
        if policy["action"] == "asset_review" and canonical and canonical["kind"] != "asset":
            hold = True
    if hold:
        result["account"] = ""
        result["confidence"] = "low"
        hold_policies = [p for p in matched if p["action"] in {"hold", "block", "asset_review"}]
        if hold_policies:
            result["reason"] = hold_policies[0]["message"]
    final_account = resolve_account(result["account"], accounts)
    if final_account:
        questions.extend(final_account.get("review_questions", []))
    result["account_code"] = final_account["code"] if final_account else None
    result["account_kind"] = final_account["kind"] if final_account else None
    result["policy_matches"] = matched
    result["blocking_reasons"] = list(dict.fromkeys(blocked))
    result["review_questions"] = list(dict.fromkeys(questions))
    result["warnings"] = list(dict.fromkeys(notes))
    return result
