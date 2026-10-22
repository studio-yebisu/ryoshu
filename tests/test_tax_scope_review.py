"""Receipt scope counterexamples found independently of the 100 fixtures."""
import pytest

from receipt_ledger.extraction import extract_total, printed_tax


@pytest.mark.parametrize('label', ['消費税等合計', '税額合計', '内税合計'])
def test_a_tax_summary_is_never_a_gross_payment_candidate(label):
    total, _, _, candidates = extract_total(f'合計3,600円\n{label}313円')
    assert total == 3600
    assert all(candidate['value'] != 313 for candidate in candidates)


@pytest.mark.parametrize('text,total,expected', [
    ('制作費総額880,000円（うち消費税80,000円）\n今回お支払額（1回目）440,000円', 440000, None),
    ('契約総額880,000円 消費税合計80,000円\n今回領収額440,000円', 440000, None),
    ('宿泊料11,000円（内消費税1,000円）\n夕食5,500円\n合計16,500円', 16500, None),
    ('回線A（本社）\n基本料金1,000円\n消費税100円\n回線B（営業）\n基本料金2,000円\n税額は別紙参照\n合計3,300円', 3300, None),
    ('ご契約金額88,000円（消費税8,000円）\n今回領収額55,000円\n消費税5,000円', 55000, 5000),
    ('軽油40.00L 7,684円\n軽油引取税額1,284円\n合計7,684円', 7684, None),
    ('軽油40.00L 7,684円\n軽油引取税額1,284円\n内消費税698円\n合計7,684円', 7684, 698),
    ('ゴルフ場利用税額800円\n合計8,800円', 8800, None),
    ('課税額1,000円\n税額100円\n合計1,100円', 1100, 100),
    ('非課税額1,100円\n消費税対象額1,000円\n内消費税100円\n合計1,100円', 1100, 100),
    ('消費税660円\n合計660円', 660, None),
    ('税抜合計1,220,000円 消費税122,000円 税込合計1,342,000円', 1342000, 122000),
    ('10%対象27,500円（内消費税2,500円）\n宿泊税400円\n合計27,900円', 27900, 2500),
    ('報酬総額110,000円\n消費税10,000円\n源泉所得税10,210円\n実振込額99,790円', 99790, 10000),
])
def test_document_and_component_tax_scopes_are_not_interchangeable(text, total, expected):
    assert printed_tax(text, total=total) == expected
