"""Printed tax must describe the selected payment, not a repeated detail row."""
import pytest

from receipt_ledger.extraction import TAX_RATE, printed_tax
from receipt_ledger.pipeline import ground
from receipt_ledger.semantic import semantic_document_text
from test_ledger import candidate


@pytest.mark.parametrize('text,total,expected',[
    ('合計1,760円\n内消費税160円',1760,160),
    ('合計1,760円\n消費税（160円）',1760,160),
    ('回線A 回線B\n消費税（10%）653円 消費税（10%）653円\n回線合計7,185円 回線合計7,185円\n割引-891円\n合計13,479円',13479,None),
    ('回線A\n消費税（10%）653円\n回線B\n消費税（10%）653円\n合計14,370円',14370,None),
    ('8%内消費税132円\n10%内消費税181円\n合計3,600円',3600,None),
    ('消費税合計313円\n8%内消費税132円\n10%内消費税181円\n合計3,600円',3600,313),
    ('消費税の合計313円\n8%内消費税132円\n10%内消費税181円\n合計3,600円',3600,313),
    ('消費税総額313円\n税額合計314円\n合計3,600円',3600,None),
    ('回線A 消費税合計653円\n回線B 消費税合計653円\n合計14,370円',14370,None),
    ('合計1,650円\n消費税10%150円\n控え 合計1,650円\n消費税10%150円',1650,150),
    ('税抜合計1,220,000 消費税（10%）122,000 税込合計1,342,000',1342000,122000),
    ('うち消費税（手数料税込660円の内数）60円\n手数料領収660円',660,60),
    ('消費税10%3,000円\n報酬総額33,000円\n源泉徴収3,063円\n実受領金額29,937円',29937,3000),
    ('消費税10%3,000円\n報酬総額33,000円\n源泉徴収税額3,063円\n実受領金額29,937円',29937,3000),
    ('消費税10%10,000円\n報酬総額110,000円\n源泉所得税10,210円\n実振込額99,790円',99790,10000),
    ('10%対象27,500円（内消費税2,500円）／対象外（宿泊税）400円\n合計27,900円',27900,2500),
    ('今回領収分55,000円（税抜50,000円・消費税5,000円10%）\nご契約金額88,000円（税抜80,000円・消費税8,000円）',55000,5000),
    ('ご契約金額88,000円（税抜80,000円・消費税8,000円）\n合計55,000円',55000,None),
])
def test_printed_tax_keeps_its_document_or_current_payment_scope(text,total,expected):
    mixed=set(TAX_RATE.findall(text))=={'8','10'}
    assert printed_tax(text,mixed,total)==expected


def test_grounding_rejects_a_line_tax_even_when_the_number_exists_in_ocr():
    text='文具店\n2026年10月20日\n回線A 回線B\n消費税10%653円 消費税10%653円\n合計13,479円'
    checked=ground(candidate(total=13479,total_evidence='合計13,479円',tax_amount=653),text)
    assert checked['tax_amount'] is None
    assert any('記載消費税' in warning for warning in checked['warnings'])


def test_percentage_shares_and_withholding_rates_are_not_consumption_tax_rates():
    text='構成比14.8% 税区分10%\n源泉所得税10.21%\n構成比18%'
    assert set(TAX_RATE.findall(text))=={'10'}
    checked=ground(candidate(total=1342000,total_evidence='合計1,342,000円',tax_amount=122000),
                   '文具店\n2026年10月20日\n構成比14.8%\n税抜合計1,220,000円 消費税10%122,000円 税込合計1,342,000円')
    assert checked['tax_rate']=='10' and checked['tax_amount']==122000


def test_grounding_does_not_count_auxiliary_ocr_layers_as_more_invoice_lines():
    text='文具店\n2026年10月20日\n消費税10%3,000円\n報酬総額33,000円\n源泉徴収3,063円\n実受領金額29,937円'
    combined=text+'\n[PDF文字層（Indra照合済み）]\n'+text+'\n[補正前の読取]\n'+text
    checked=ground(candidate(total=29937,total_evidence='実受領金額29,937円',tax_amount=3000),combined,tax_text=text)
    assert checked['tax_amount']==3000


def test_ambiguous_purchase_policy_still_prevents_auto_classification():
    checked=ground(candidate(account='会議費',total=880,total_evidence='合計880円',tax_amount=80),
                   '文具店\n2026年10月20日\nコーヒー800円\n消費税10%80円\n合計880円\n見本・サンプル')
    assert checked['account']==''
    assert any(p['code']=='P_FOOD_CONTEXT' for p in checked['policy_matches'])


def test_standalone_document_marks_are_separate_from_real_purchase_descriptions():
    original='テスト商事株式会社\nソフトウェアの負荷テスト委託料\n見本市参加費\nサンプル品3個\n合計22,000円\n架空テスト / 再発行不可\n（見本）\nSAMPLE / DUMMY'
    body,ignored=semantic_document_text(original)
    assert body=='テスト商事株式会社\nソフトウェアの負荷テスト委託料\n見本市参加費\nサンプル品3個\n合計22,000円\n（見本）'
    assert ignored==['架空テスト / 再発行不可','SAMPLE / DUMMY']
    assert '架空テスト / 再発行不可' in original


def test_bare_sample_item_and_its_separately_recognized_price_are_retained():
    original='文具店\n品名\nサンプル\n1,000円\n見本・サンプル\n数量1 単価500円\n合計1,650円'
    assert semantic_document_text(original)==(original,[])
