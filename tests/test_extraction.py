import pytest
from receipt_ledger.extraction import extract_total,document_flags,kanji_amount,compact
from receipt_ledger.pipeline import dates_in
from receipt_ledger.semantic import dated_evidence
from receipt_ledger.semantic import issuer_candidates
from receipt_ledger.semantic import printed_tax
from receipt_ledger.catalog import apply_account_policies


@pytest.mark.parametrize('text,expected',[
    ('合計(5点) ¥7,480\n内消費税680',7480),
    ('合計（税込） 18,260 内消費税 ¥1,660\nご請求金額\n18,260 円',18260),
    ('¥72,820-\nご購入金額(税込)\nお支払 現金75,000 お釣り2,180',72820),
    ('今回受領（残金）\n13,000円\n宿泊総額28,000円\n予約金受領済15,000円',13000),
    ('合計ご請求額\n12,100円\n回線合計5,500円\n回線合計6,600円',12100),
    ('受領額\n32,100円\n航空運賃30,000円\n施設使用料2,100円',32100),
    ('合計 金参萬弐阡五百円也',32500),
])
def test_evidence_total_label_and_adjacent_value(text,expected):
    value,evidence,_,_=extract_total(text)
    assert value==expected and compact(evidence) in compact(text)


def test_return_policy_is_not_a_refund_and_actual_refund_is_stopped():
    ordinary='御領収書\n合計3,300円\nご返品は未使用・タグ付き、7日以内に限り承ります。'
    assert not document_flags(ordinary)['refund']
    assert not apply_account_policies({'account':'消耗品費','total':3300},ordinary)['blocking_reasons']
    refund='返金処理完了通知\n返金額 -3,300円'
    assert document_flags(refund)['refund']
    assert apply_account_policies({'account':'消耗品費','total':3300},refund)['blocking_reasons']


def test_legitimate_leading_four_is_not_removed_from_a_currency_amount():
    text='領収金額 ¥440,000\n税抜価格400,000円\n消費税40,000円'
    assert extract_total(text)[0]==440000


def test_geometry_recovers_amount_without_taking_meters_or_tax():
    lines=[{'text':'領収金額','box':[20,100,130,120]}, {'text':'3,850','box':[20,130,150,165]},
           {'text':'口径13mm','box':[230,105,310,120]}, {'text':'消費税350','box':[230,140,330,160]}]
    amount,evidence,_,_=extract_total('領収金額 口径13mm\n3,850 消費税350',pages=[{'lines':lines}])
    assert amount==3850 and evidence=='3,850'


def test_native_pdf_amount_and_scan_amount_disagreement_is_visible():
    value,_,notes,_=extract_total('合計 ¥43,850','合計 ¥3,850')
    assert value==3850 and notes


def test_japanese_era_kanji_and_dotted_abbreviation_dates():
    assert dates_in('令和五年九月二十九日')=={'2023-09-29'}
    assert dates_in('R.6- 3- 8')=={'2024-03-08'}


def test_payment_date_wins_over_future_usage_date_on_same_line():
    text='発行・決済 2024/07/10 搭乗予定 2024/08/02'
    assert dated_evidence(text,dates_in)[0]=='2024-07-10'
    assert dated_evidence('滞在 2025年9月11日〜9月12日 精算日 2025年9月12日',dates_in)[0]=='2025-09-12'


def test_issuer_options_keep_names_separate_from_receipt_caption_and_test_marker():
    ocr={'pages':[],'text':'八百屋 あづまや お買い上げ控え\n2024年10月3日\nみかん 袋', 'raw_text':''}
    assert '八百屋 あづまや' in issuer_candidates(ocr)
    ocr['text']='電器専門店 サンプル電機 架空テスト\n御領収書'
    assert '電器専門店 サンプル電機' in issuer_candidates(ocr)


def test_recipient_marker_does_not_become_issuer_option():
    ocr={'pages':[],'text':'株式会社サンプル商事 御中 言葉舎 水野\n請求書\n翻訳料', 'raw_text':''}
    values=issuer_candidates(ocr)
    assert '言葉舎 水野' in values and '株式会社サンプル商事' not in values


def test_tax_evidence_is_not_added_twice_for_stubs_or_ocr_retries():
    assert printed_tax('消費税10% 150円\n消費税10% 150円')==150
    assert printed_tax('8%内消費税132円\n10%内消費税181円',mixed=True) is None
    assert printed_tax('消費税合計313円\n8%内消費税132円\n10%内消費税181円',mixed=True)==313


def test_currency_code_without_space_does_not_become_a_yen_amount():
    assert document_flags('Total USD30\nJPY4,500は参考換算です')['foreign']
    assert document_flags('USD 129.00')['foreign']
    assert not document_flags('USDクラウド株式会社\n合計3,300円')['foreign']


def test_event_purpose_and_honorarium_require_review():
    for text in ['文化祭 入場料領収証 1,800円','座談会 出演謝礼 20,000円']:
        checked=apply_account_policies({'account':'消耗品費','total':20000},text)
        assert checked['account']=='' and not checked['blocking_reasons']


def test_repeated_amount_and_tax_consistency_disambiguate_previous_balance():
    text='今回のご利用明細\n小計(税抜)2,500円\n消費税(10%)250円\n42,750\nご参考:前回残高\nY6,600\n[部分再読取]\n$2,750'
    assert extract_total(text)[0]==2750
    assert extract_total('￥43,850\n￥5,500')[0] is None


def test_supplier_header_is_distinct_from_recipient_and_captions():
    from receipt_ledger.semantic import is_recipient_name
    text='北町モータース\nパン工房さくら\n様\n取付工賃 6,000円'
    names=issuer_candidates({'text':text,'pages':[]})
    assert '北町モータース' in names and 'パン工房さくら' not in names
    assert not is_recipient_name('南市水道局','南市水道局 様より\n山田 様')
    assert is_recipient_name('パン工房さくら',text)
    assert '南市 会計課' in issuer_candidates({'text':'南市 会計課','pages':[]})
    assert issuer_candidates({'text':'領 収 証\n求 R\n年 月 日','pages':[]})==[]


def test_shop_branch_and_address_shared_row_are_preserved():
    assert 'ひばり文具店 南町本店' in issuer_candidates({'text':'ひばり文具店\n南町本店','pages':[]})
    assert '南市家具 リビング館' in issuer_candidates({'text':'南市山町3丁目22 南市家具 リビング館','pages':[]})
    assert '電器専門店 あかり' in issuer_candidates({'text':'電器専門店 あかり お客様サービスカウンター','pages':[]})


def test_food_policy_uses_purchases_not_customer_name_or_storage_container():
    for text in ['南町モータース\nパン工房みどり 様\nブレーキパッド 8,500円\n取付工賃 6,000円','卸売領収書\n食品保存容器 4個組 22,200円']:
        checked=apply_account_policies({'account':'消耗品費','total':20000},text)
        assert not any(p['code']=='P_FOOD_CONTEXT' for p in checked['policy_matches'])
    assert apply_account_policies({'account':'消耗品費','total':800},'野菜 ごぼう 800円')['account']==''


def test_partial_payment_certificate_is_not_an_unpaid_document():
    assert not document_flags('本書は今回分16,800円のみの領収証です。総額を再度領収したものではありません。')['unpaid']
    assert document_flags('本書は請求書であり、領収書ではありません。')['unpaid']


def test_annual_service_waits_for_period_allocation():
    checked=apply_account_policies({'account':'前払費用','total':48000},'年間契約 年額48,000円')
    assert checked['account']=='' and not checked['blocking_reasons']


def test_document_date_is_not_quote_expiry_and_product_is_not_issuer():
    assert dated_evidence('有効期限2029年8月31日\n見積日2029年7月11日',dates_in)[0]=='2029-07-11'
    assert issuer_candidates({'text':'レンタルサーバー スタンダード\nご利用明細','pages':[]})==[]
    assert 'さくらクラウド株式会社' in issuer_candidates({'text':'(1) さくらクラウド株式会社','pages':[]})


def test_trusted_pdf_issuer_text_excludes_image_ocr_variants_and_room_names():
    o={'text':'単 南市店\n第3スタジオ','raw_text':'まちがいスーパー 南市店','native_pdf_text':'スーパー さくら 南市店\nSTUDIO はるか\n第3スタジオ','pages':[]}
    names=issuer_candidates(o)
    assert 'スーパー さくら 南市店' in names and 'STUDIO はるか' in names
    assert '単 南市店' not in names and '第3スタジオ' not in names and 'まちがいスーパー 南市店' not in names
    names=issuer_candidates({'text':'単 南市店\nスーパー さくら 南市店\nアンケートでサービス券が使える','pages':[]})
    assert names==['スーパー さくら 南市店']
