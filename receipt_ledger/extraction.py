"""Evidence-based fields and document checks, independent of any test fixture."""
import re
import unicodedata
from datetime import date


def compact(value):
    return re.sub(r"\s+", "", unicodedata.normalize("NFKC", value or "")).casefold()


def money_tokens(text):
    text=unicodedata.normalize("NFKC",text)
    text=re.sub(r'(?<=\d)\.\s*(?=\d{3}(?!\d))',',',text)
    text=re.sub(r"(?<=\d)\s*,\s*(?=\d)",",",text)
    return [(int(m.group().replace(',','')),m.start(),m.end()) for m in re.finditer(r"(?<![\d.])(?:\d+(?:,\d{3})+|\d+)(?![\d.])",text)]


def kanji_amount(text):
    digits=dict(zip("〇零一壱二弐三参四肆五伍六陸七漆八捌九玖",[0,0,1,1,2,2,3,3,4,4,5,5,6,6,7,7,8,8,9,9]))
    units={"十":10,"拾":10,"百":100,"佰":100,"陌":100,"千":1000,"阡":1000,"仟":1000}
    match=re.search(r"(?:金)?([〇零一壱二弐三参四肆五伍六陸七漆八捌九玖十拾百佰陌千阡仟万萬億]+)円",compact(text))
    if not match:return None
    total=group=number=0
    for c in match[1]:
        if c in digits:number=number*10+digits[c]
        elif c in units:group+=(number or 1)*units[c];number=0
        else:
            scale=100_000_000 if c=='億' else 10_000
            total+=(group+number or 1)*scale;group=number=0
    return total+group+number


TOTAL_LABEL=re.compile(r"今回.*(?:領収|受領|支払|決済|請求)|実(?:受領金額|振込額)|領収(?:金額|総額|額)|受領(?:金額|額)|(?:カード|現金|本日).*受領|お支払(?:い)?(?:金額|額|合計)|支払(?:い)?(?:済額|金額|額|合計)|ご購入(?:金)?額|御買上金額|お買上金額|総合計|合計(?:ご)?請求額|合計領収額|ご利用(?:金)?額|ご?請求(?:金額|額|分)|請求合計|合計|総額|\btotal\b|amountpaid|納付(?:金)?額|ご寄付額|返済額|^料金",re.I)
EXCLUDE_TOTAL=re.compile(r"小計|繰越|前回|前月|お預[りか]|釣[り銭]|おつり|返品について|ポイント付与|税.*合計|対象.*合計",re.I)


def amount_on_line(line):
    """Prefer an explicit currency amount, excluding quantities, rates and tax suffixes."""
    raw=unicodedata.normalize('NFKC',line)
    raw=re.sub(r'(?<=\d)\.\s*(?=\d{3}(?!\d))',',',raw)
    raw=re.sub(r"(?<=\d)\s*,\s*(?=\d)",",",raw)
    # A tax amount later on the same line is not the gross total.
    raw=re.split(r'内消費税|内税|うち消費税',raw)[0]
    if '=' in raw:raw=raw.rsplit('=',1)[-1]
    yen=re.search(r"[¥￥Y]\s*([0-9][0-9,]*)|([0-9][0-9,]*)\s*円",raw,re.I)
    if yen:return int((yen[1] or yen[2]).replace(',',''))
    k=kanji_amount(raw)
    if k is not None:return k
    candidates=[]
    for value,start,end in money_tokens(raw):
        before=raw[max(0,start-12):start];after=raw[end:end+7]
        if re.match(r"\s*(?:%|点|名|人|個|枚|本|年|月|日|mm|cm|kg|kWh|ml|㎡|:|/)",after,re.I):continue
        if re.search(r"No\.?\s*$|税(?:額)?\s*$|消費税\s*$",before,re.I):continue
        candidates.append(value)
    return candidates[0] if candidates and len(set(candidates))==1 else None


TAX_LABEL = re.compile(r"(?:(?:合計|総額)\s*)?(?:うち|内)?(?:消費税(?:等|額)?|内税|税額)(?:\s*(?:の)?(?:合計|総額))?")
TAX_RATE = re.compile(r"(?<![\d.])(8|10)\s*[%％]")


def printed_tax(text, mixed=False, total=None):
    """Read an existing tax amount only when its scope is the selected receipt.

    Equal amounts can be separate line taxes. They are not a document total just
    because a set happens to contain one value. Reconciliation below only checks
    whether repeated observations describe the same total; it never invents tax.
    """
    records=[]
    normalized=unicodedata.normalize('NFKC',text)
    for line in normalized.splitlines():
        labels=list(TAX_LABEL.finditer(line))
        for index,label in enumerate(labels):
            tail=line[label.end():labels[index+1].start() if index+1<len(labels) else len(line)]
            # A parenthetical description can contain the taxable gross, not the
            # tax: 「うち消費税（手数料税込660円の内数）60円」.
            annotation=re.match(r'^\s*[:：]?\s*\(([^()]*)\)',tail)
            if annotation and re.search(r'%|内数|税率|税込|税抜|対象',annotation[1]):
                tail=tail[annotation.end():]
            tail=re.split(r'税(?:込|抜)(?:合計|総額|金額)|総合計|報酬総額|回線合計|(?<!税)合計',tail,maxsplit=1)[0]
            amount=amount_on_line(tail)
            if amount is None:continue
            prefix=line[:label.start()]
            current=bool(re.search(r'今回.*(?:領収|受領|入金|支払|決済)',prefix))
            current=current and total is not None and total in [value for value,_,_ in money_tokens(prefix)]
            other=bool(re.search(r'前回|前月|前受|着手金|予約金|契約(?:金額|額)|(?:回線|部門|商品|品目|明細)[A-Z0-9一二三四五六七八九十甲乙]|回線別|部門別|品目別|明細別',prefix)) and not current
            records.append({'amount':amount,'aggregate':bool(re.search(r'合計|総額',label.group())),
                            'current':current,'other_scope':other})
    if not records:return None
    current=[r['amount'] for r in records if r['current']]
    if current:return current[0] if len(set(current))==1 else None
    aggregates=[r['amount'] for r in records if r['aggregate'] and not r['other_scope']]
    if aggregates:return aggregates[0] if len(set(aggregates))==1 else None
    if mixed:return None
    if len(records)==1:return None if records[0]['other_scope'] else records[0]['amount']
    values={r['amount'] for r in records}
    if len(values)!=1 or any(r['other_scope'] for r in records) or total is None:return None
    amount=next(iter(values))
    rates={int(rate) for rate in TAX_RATE.findall(normalized)} or {8,10}
    # Two copies of an entire receipt/stub may repeat its tax. A per-line tax
    # such as 653+653 on a discounted invoice does not reconcile with its gross.
    return amount if any(abs(amount*(100+rate)-total*rate)<=100+rate for rate in rates) else None


def total_candidates(text):
    lines=[l.strip() for l in unicodedata.normalize('NFKC',text).splitlines() if l.strip()]
    found=[]
    for i,line in enumerate(lines):
        key=compact(line)
        matches=list(TOTAL_LABEL.finditer(key));match=matches[-1] if matches else None
        if not match:continue
        before=key[max(0,match.start()-12):match.start()]
        if re.search(r'小計$|税抜$|消費税$|課税(?:分|対象)?$|対象(?:額)?$|前回|繰越',before):continue
        if re.search('返品について|返品の際|返品規定',key):continue
        tail=key[match.end():]
        # Avoid taking a reference conversion of a foreign currency as a yen payment.
        if re.search(r"usd|eur|参考|換算",key):continue
        # Multiple monetary fields on one row: use the suffix belonging to this label.
        label=match
        normalized_line=unicodedata.normalize('NFKC',line)
        value=amount_on_line(normalized_line) if label is None else amount_on_line(compact(line)[label.end():])
        evidence=line
        if value is None:
            for offset in [1,-1,2]:
                j=i+offset
                if not 0<=j<len(lines):continue
                near=lines[j]
                if EXCLUDE_TOTAL.search(compact(near)) or re.search(r"消費税|内税|税額|年|月|日|No\.",near,re.I):continue
                # Adjacent value must be a stand-alone monetary line, not an item or ID.
                if not re.fullmatch(r"[¥￥Y$*金\d, .\-−–ー\"'()税込〇零一壱二弐三参四肆五伍六陸七漆八捌九玖十拾百佰陌千阡仟万萬億円也am]+",near,re.I):continue
                value=amount_on_line(near)
                if value is not None:evidence=near if abs(offset)>1 else near+'\n'+line if offset<0 else line+'\n'+near;break
        if value is not None and value<=999_999_999:
            priority=6 if re.search(r'実受領|実振込|今回|本日|税込支払額|月分ご請求額',key) else 5 if re.search(r"領収(?:金額|総額|額)|受領額|(?:カード|現金)受領|支払金額|支払額|支払済額|お支払|ご購入(?:金)?額|御買上金額|amountpaid|ご請求金額|合計ご請求額",key) else 4 if re.search(r"総合計|請求合計|合計請求",key) else 3
            found.append({'value':value,'evidence':evidence,'priority':priority})
    if not found:
        for line in lines:
            # Large unlabeled amount on taxi coupons and simple handwritten receipts.
            if re.fullmatch(r'[¥￥Y$*金\d, .\-−–ー()税込〇零一壱二弐三参四肆五伍六陸七漆八捌九玖十拾百佰陌千阡仟万萬億円也]+',line) and (re.search(r'[¥￥Y$*円,]',line) or kanji_amount(line) is not None):
                value=amount_on_line(line)
                if value is not None and 10<=value<=999_999_999:found.append({'value':value,'evidence':line,'priority':1})
        for line in lines:
            if EXCLUDE_TOTAL.search(compact(line)) or re.search('消費税|税額|内税',line):continue
            match=re.search(r'(?:[¥￥$]|(?<![A-Za-z])Y)\s*\d[\d, ]*',line,re.I)
            if match:
                value=amount_on_line(match.group())
                if value is not None and value>=10:found.append({'value':value,'evidence':match.group(),'priority':2})
    return found


def geometry_totals(pages):
    result=[]
    from statistics import median
    for page in pages or []:
        lines=[l for l in page.get('lines',[]) if l.get('text','').strip() and len(l.get('box',[]))==4]
        values=[]
        for l in lines:
            t=compact(l['text'])
            if re.fullmatch(r'[¥￥y$*金\d,.\-−–ー()税込〇零一壱二弐三参四肆五伍六陸七漆八捌九玖十拾百佰陌千阡仟万萬億円也m]+',t):
                amount=amount_on_line(t)
                if amount is not None and 0<amount<=999_999_999:values.append((l,amount))
        typical=median([max(1,v[0]['box'][3]-v[0]['box'][1]) for v in values]) if values else 1
        for l,value in values:
            height=l['box'][3]-l['box'][1]
            if height>=typical*1.35 and re.search(r'[¥￥Y$*円,]',l['text']):
                result.append({'value':value,'evidence':l['text'],'priority':2,'method':'prominent_amount'})
        for label in lines:
            key=compact(label['text'])
            if len(key)>28 or EXCLUDE_TOTAL.search(key) or not (TOTAL_LABEL.search(key) or key in {'金額','料金(税込)','御買上金額','領収金額','納付金額','領収合計'}):continue
            match=TOTAL_LABEL.search(key)
            if match and amount_on_line(key[match.end():]) is not None:continue
            lb=label['box'];lh=max(1,lb[3]-lb[1]);cx=(lb[0]+lb[2])/2;cy=(lb[1]+lb[3])/2
            possible=[]
            for l,value in values:
                if l is label:continue
                b=l['box'];dy=(b[1]+b[3])/2-cy
                same_row=abs(dy)<max(lh,b[3]-b[1])*.8 and b[0]>=lb[2]-lh
                near_below=-lh<dy<lh*5 and b[0]<=lb[2]+lh*3 and b[2]>=lb[0]-lh
                if not same_row and not near_below:continue
                distance=abs(dy)+(max(0,b[0]-lb[2])*.12 if same_row else abs((b[0]+b[2])/2-cx)*.15)
                if b[3]-b[1]<lh*.8:continue
                possible.append((distance,l,value))
            if possible:
                _,line,value=min(possible,key=lambda v:v[0])
                if key=='金額' and line['box'][3]-line['box'][1]<typical*1.3:continue
                priority=6 if re.search('今回|実受領|実振込',key) else 5 if re.search('領収|受領|支払|請求|購入|買上|納付',key) else 3
                result.append({'value':value,'evidence':line['text'],'label_evidence':label['text'],'priority':priority,'method':'label_geometry'})
    return result


def extract_total(text, native_text='', pages=None):
    found=total_candidates(text)
    native=total_candidates(native_text)
    notes=[]
    highest_primary=max((c['priority'] for c in found),default=0)
    primary_values={c['value'] for c in found if c['priority']==highest_primary}
    if not native and (highest_primary<3 or len(primary_values)>1):found+=geometry_totals(pages)
    if native:
        # The PDF layer is only provided after Indra's visual agreement check.
        found=native+found
    if not found:return None, '', notes, []
    # A fee receipt can also display a much larger transferred principal.
    if re.search(r'本(?:受付票|書|票)は振込手数料の支払証明|振込金額の領収書ではありません',compact(native_text or text)):
        for line in (native_text or text).splitlines():
            if re.search('振込手数料',line) and not re.search('合計|消費税|本受付',line):
                value=amount_on_line(line)
                if value is not None:return value,line,notes,found
    # Japanese yen signs are sometimes recognized as a leading 4. Repair only
    # when the exact shorter amount independently occurs in the same source.
    if not native_text:
        all_values={v for v,_,_ in money_tokens(text)}
        for c in found:
            value=str(c['value'])
            if len(value)>=4 and value.startswith('4') and not re.search(r'[¥￥Y$]',c['evidence']):
                short=int(value[1:])
                verified=any(re.search(r'消費税|内税|税額',l) and any(t in {short*10//110,round(short*10/110),short*8//108,round(short*8/108)} for t,_,_ in money_tokens(l) if t>10) for l in text.splitlines())
                if verified and short>=100:
                    c['value']=short;c['tax_check']={'observed':int(value),'corrected':short}
                    notes.append('円記号の誤読を、記載された内税額との整合性で照合しました')
                    continue
            if len(value)>=4 and value.startswith('4') and not re.search(r'[¥￥Y$]',c['evidence']) and int(value[1:]) in all_values and int(value[1:])>=100:
                corrected=int(value[1:])
                tax_verified=any(re.search(r'10\s*[%％]',l) and int(corrected*10/110) in [v for v,_,_ in money_tokens(l)] for l in text.splitlines())
                line=next((l for l in text.splitlines() if corrected in [v for v,_,_ in money_tokens(l)] and not re.search(r'消費税|税額|小計|税抜|源泉|お預|釣|おつり',l) and (re.search(r'[¥￥Y$*]',l) or tax_verified)),None)
                if line and (c['priority']<=2 or tax_verified):
                    c['value']=corrected;c['evidence']=line
                    notes.append('円記号に由来する先頭の誤読を、同じ原本の別の金額表記で照合しました')
    # Validate an OCR-only total with explicit cash tender and change.
    if not native_text:
        tender=[];change=[]
        for line in text.splitlines():
            for pattern,target in [(r'お預(?:り|かり|かり金)?',tender),(r'お(?:釣り|つり)|釣銭',change)]:
                m=re.search(pattern,line)
                if m:
                    tail=re.split(r'お(?:預|釣|つり)|釣銭',line[m.end():])[0]
                    values=money_tokens(tail)
                    if values:target.append(values[0][0])
        if len(set(tender))==len(set(change))==1 and tender[0]>=change[0]:
            paid=tender[0]-change[0]
            if paid>0:
                for c in found:
                    if str(c['value'])=='4'+str(paid):
                        c['value']=paid;c['cash_check']={'tender':tender[0],'change':change[0]}
                        notes.append('円記号の誤読を、預り金とお釣りの差額で照合しました')
    if found and max(c['priority'] for c in found)<=2:
        corroborated={c['value'] for c in found if (c.get('tax_check') or c.get('cash_check')) and any(c['value'] in [v for v,_,_ in money_tokens(other['evidence'])] for other in found)}
        if len(corroborated)==1:
            value=next(iter(corroborated))
            literal=next(c for c in found if c['value']==value and value in [v for v,_,_ in money_tokens(c['evidence'])])
            return value,literal['evidence'],notes,found
    highest=max(c['priority'] for c in found)
    candidates=[c for c in found if c['priority']==highest]
    values={c['value'] for c in candidates}
    if len(values)==1:
        c=candidates[0];return c['value'],c['evidence'],notes,found
    if native:
        top=[c for c in native if c['priority']==max(v['priority'] for v in native)]
        if len({c['value'] for c in top})==1:
            notes.append('画像OCRとPDFの文字層に金額の相違があります。文字層の候補を原本と照合してください')
            return top[0]['value'],top[0]['evidence'],notes,found
    notes.append('異なる支払合計が見つかりました。原本から今回支払額を確認してください')
    return None,'',notes,found


def secondary_amounts(text):
    """Unlabeled repeated currency-like tokens for semantic review, never auto-total."""
    from collections import Counter
    tokens=[]
    for line in text.splitlines():
        for m in re.finditer(r'(?<!\d)\d{1,3}(?:[, .]\s*\d{3})+(?!\d)',line):
            values=money_tokens(m.group())
            if len(values)==1 and values[0][0]>=100:tokens.append((values[0][0],m.group()))
    counts=Counter(v for v,_ in tokens)
    return [{'value':v,'evidence':next(t for n,t in tokens if n==v),'priority':0,'method':'unlabeled_repeated_number'} for v,count in counts.items() if count>=2][:8]


def vertical_text(lines):
    """Recover vertical character columns (including two columns in one OCR box)."""
    columns=[]
    for line in lines:
        box=line['box'];text=line['text'].strip()
        chars=text.split()
        if not chars or len(chars)>3 or any(len(s)!=1 or not re.match(r'[一-龥ぁ-んァ-ヶーA-Z0-9]',s) for s in chars):continue
        width=(box[2]-box[0])/len(chars);height=box[3]-box[1]
        if width>height*2.5:continue
        for n,char in enumerate(chars):
            x=box[0]+(n+.5)*width;y=(box[1]+box[3])/2
            col=next((c for c in columns if abs(c['x']-x)<max(8,height*.55)),None)
            if col is None:col={'x':x,'height':height,'chars':[]};columns.append(col)
            col['chars'].append((y,char))
    result=[]
    for col in sorted(columns,key=lambda c:-c['x']):
        chars=sorted(col['chars']);run=[];last=None
        for y,c in chars:
            if last is not None and y-last>col['height']*3.5:
                if len(run)>=3:result.append(''.join(run))
                run=[]
            run.append(c);last=y
        if len(run)>=3:result.append(''.join(run))
    return '\n'.join(result)


def document_flags(text):
    """Recognize the current transaction, excluding general return-policy boilerplate."""
    t=compact(text)
    # A policy paragraph mentioning refunds does not make a purchase a refund.
    lines=[compact(l) for l in text.splitlines()]
    active=[]
    for line in lines:
        if re.search(r'返品.*(?:について|の際|の場合|以内|承り|未使用|未開封)|返品交換|キャンセルポリシー|返品規定|払戻条件',line):continue
        active.append(line)
    current='\n'.join(active)
    refund=bool(re.search(r'返金(?:明細|処理|額|金額|合計|完了|通知|済)|返品(?:伝票|領収)|払戻(?:金額|額|済)|払い戻し(?:済|金額)|creditnote|クレジットノート|取消(?:通知|伝票|精算)|キャンセル料0',current,re.I))
    payment_text=re.sub(r'(?:未入金額|未払残高|未払い額|未払額|未払金)[:：]?0円?', '', t)
    negative_proof=bool(re.search(r'(?:本書|本票|この納品書|本資料|本計算書)[^。]{0,100}(?:領収書|領収証|支払(?:い)?の証|支払証明)[^。]{0,20}(?:ではありません|ではなく|でありません)|(?:支払|入金)[^。]{0,20}証明(?:しません|するものでは)',payment_text))
    unpaid=negative_proof or bool(re.search(r'未入金|未払い|未払(?!金)|後日.*(?:請求|決済)|入金(?:額)?[:：]?0円|見積書|発注前|支払い?前|領収書未添付|社内精算.*(?:内訳|明細)|口座振替予定|お支払日.{0,30}予定|口座振替日.*お支払い済みの場合があります',payment_text))
    currency_text=unicodedata.normalize('NFKC',text)
    foreign=bool(re.search(r'(?<![A-Za-z])(?:USD|EUR|GBP)\s*[:：]?\s*\d|\d[\d,.]*\s*(?:USD|EUR|GBP)(?![A-Za-z])|(?:通貨|Currency)[:：\s]*(?:USD|EUR|GBP)(?![A-Za-z])',currency_text,re.I))
    if re.search(r'円で決済|円建て確定|決済通貨[:：]?JPY|支払通貨[:：]?JPY',t,re.I):foreign=False
    return {'refund':refund,'unpaid':unpaid,'foreign':foreign}
