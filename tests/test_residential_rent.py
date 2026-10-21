import json
from pathlib import Path

import pytest

from receipt_ledger.catalog import apply_account_policies
from receipt_ledger.schemas import Draft
from receipt_ledger.store import Store


@pytest.mark.parametrize('text', [
    '居住用賃貸住宅の家賃 一部支払分\n家賃 70,000 20% 14,000\n駐車場使用料 4,000 0% 0\n今回分合計14,000円',
    '住居用賃料 75,000円 領収書',
    '自宅家賃 今月分の領収証 80,000円',
])
def test_residential_rent_never_keeps_a_forced_travel_candidate(text):
    result = apply_account_policies({'account': '旅費交通費', 'total': 14000}, text)
    assert result['account'] == ''
    assert result['account_code'] is None
    assert not result['blocking_reasons']
    assert any(p['code'] == 'P_RESIDENTIAL_RENT' for p in result['policy_matches'])
    assert '事業で使う割合と根拠' in result['review_questions']


@pytest.mark.parametrize('text,account', [
    ('事務所賃料 70,000円 駐車場使用料0円', '地代家賃'),
    ('出張先ホテル 宿泊料金 12,000円', '旅費交通費'),
    ('住居用 火災保険 保険料 20,000円', '損害保険料'),
])
def test_non_residential_rent_is_not_held_by_the_new_policy(text, account):
    result = apply_account_policies({'account': account, 'total': 12000}, text)
    assert not any(p['code'] == 'P_RESIDENTIAL_RENT' for p in result['policy_matches'])


def test_old_catalog_gets_the_review_policy_and_preserves_manual_catalog_fields(tmp_path, monkeypatch):
    import receipt_ledger.store as module
    old = json.loads((Path(__file__).parents[1] / 'data/catalog-history/2026-10-21.6.json').read_text('utf-8'))
    with monkeypatch.context() as patch:
        patch.setattr(module, 'seed_catalog', lambda: old)
        store = Store(tmp_path)
        with store.connect() as db:
            db.execute("UPDATE account_master SET description='利用者の説明' WHERE name='地代家賃'")
    upgraded = Store(tmp_path)
    assert len(upgraded.policies()) == 15
    assert next(a for a in upgraded.accounts() if a['name'] == '地代家賃')['description'] == '利用者の説明'
    assert any(p['code'] == 'P_RESIDENTIAL_RENT' for p in upgraded.policies())


def test_owner_can_confirm_real_business_use_after_the_candidate_was_held(tmp_path):
    store = Store(tmp_path)
    row, _ = store.add('架空の住居家賃.pdf', '.pdf', b'fictional-residential-rent')
    text = '居住用賃貸住宅の家賃 領収書 70,000円'
    candidate = apply_account_policies({'account': '旅費交通費', 'total': 70000}, text)
    store.complete(row['id'], {'text': text}, candidate)
    row = store.get(row['id'])
    draft = Draft(date='2024-06-27', merchant='架空の貸主', total=70000, account='地代家賃',
                  purpose='仕事専用スペースの面積から按分', payment='bank', business_percent=20)
    confirmed = store.save(row['id'], row['version'], draft, confirm=True)
    assert confirmed['status'] == 'confirmed'
    assert {line['name'] for line in confirmed['posting']['lines']} == {'地代家賃', '事業主貸', '普通預金'}
    assert sum(line['amount'] for line in confirmed['posting']['lines'] if line['side'] == 'credit') == 70000
