#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Author folder: opus (claude-opus-5-5)
Cases V091-V100, layouts opus-01 .. opus-10.

Every case below is an independently composed page. Only primitive helpers are
shared: font picking, text/vertical text, seals, collision bookkeeping. No
common receipt template is used. All content is fictional test data.

Usage: python generate.py --output <absolute-dir>
"""
import argparse
import json
import os
import sys

from reportlab.pdfgen import canvas
from reportlab.lib.units import mm
from reportlab.lib.colors import HexColor, black, white
from reportlab.pdfbase import pdfmetrics
from reportlab.pdfbase.ttfonts import TTFont

AUTHOR = "opus"
AUTHOR_MODEL = "claude-opus-5-5"

INK = HexColor('#1a1a1a')
GRAY = HexColor('#666666')
LIGHT = HexColor('#bbbbbb')
RED = HexColor('#c8322d')

# ---------------------------------------------------------------- fonts

SAMPLE = "領収書金額円年月日税込合計架空テスト様壱弐参萬阡￥０123,"

ROLE_CANDIDATES = {
    'G': [('msgothic.ttc', 0), ('YuGothM.ttc', 0), ('meiryo.ttc', 0)],
    'M': [('msmincho.ttc', 0), ('YuMincho.ttc', 0)],
    'B': [('YuGothB.ttc', 0), ('meiryob.ttc', 0)],
    'R': [('meiryo.ttc', 0), ('YuGothM.ttc', 0)],
    'Mono': [('msgothic.ttc', 0)],
    'H': [('HGRGY.TTC', 0), ('HGRSKP.TTF', 0)],   # handwriting-like (gyosho)
    'K': [('HGRSKP.TTF', 0), ('HGRGY.TTC', 0)],   # brush kaisho
}
ROLE_FALLBACK = {'B': 'G', 'R': 'G', 'Mono': 'G', 'H': 'M', 'K': 'M',
                 'M': 'G', 'G': 'M'}

FONTS = {}   # role -> registered name
FACES = {}   # registered name -> set of code points
FILES = {}   # registered name -> path


def font_dirs():
    dirs = []
    windir = os.environ.get('WINDIR')
    if windir:
        dirs.append(os.path.join(windir, 'Fonts'))
    dirs.append('C:/Windows/Fonts')
    return dirs


def register_fonts():
    loaded = {}

    def load(fname, idx):
        key = (fname.lower(), idx)
        if key in loaded:
            return loaded[key]
        loaded[key] = None
        for d in font_dirs():
            p = os.path.join(d, fname)
            if not os.path.isfile(p):
                continue
            try:
                reg = 'OP_' + os.path.splitext(fname)[0] + '_' + str(idx)
                f = TTFont(reg, p, subfontIndex=idx)
                cmap = set(f.face.charToGlyph.keys())
                missing = [ch for ch in SAMPLE if ord(ch) not in cmap]
                if missing:
                    raise ValueError('missing glyphs ' + ''.join(missing))
                pdfmetrics.registerFont(f)
                FACES[reg] = cmap
                FILES[reg] = p
                loaded[key] = reg
                return reg
            except Exception as e:  # embedding restriction, CFF outlines, etc.
                print(f'[font] skip {p}: {e}', file=sys.stderr)
        return None

    for role, cands in ROLE_CANDIDATES.items():
        for fname, idx in cands:
            reg = load(fname, idx)
            if reg:
                FONTS[role] = reg
                break
    if 'G' not in FONTS and 'M' not in FONTS:
        raise SystemExit('No usable Japanese font found in C:/Windows/Fonts')
    for _ in range(4):
        for role, fb in ROLE_FALLBACK.items():
            if role not in FONTS and fb in FONTS:
                FONTS[role] = FONTS[fb]
    distinct = {FILES[n] for n in FONTS.values()}
    if len(distinct) < 2:
        raise SystemExit('Need at least two distinct Japanese fonts; found ' + str(distinct))
    for role in sorted(FONTS):
        print(f'[font] {role:4s} -> {FILES[FONTS[role]]}')


def covers(name, s):
    cmap = FACES[name]
    return all(ch == ' ' or ord(ch) in cmap for ch in s)


def pick(role, s):
    name = FONTS[role]
    if covers(name, s):
        return name
    for alt in ('G', 'M', 'R'):
        n2 = FONTS.get(alt)
        if n2 and covers(n2, s):
            return n2
    return name


# ---------------------------------------------------------------- primitives

class Sheet:
    """One-page canvas plus collision / overflow bookkeeping."""

    def __init__(self, path, w, h, title):
        self.c = canvas.Canvas(path, pagesize=(w, h), invariant=1, pageCompression=1)
        self.c.setTitle(title)
        self.c.setAuthor('fictional test data')
        self.c.setSubject('架空テスト')
        self.c.setLineWidth(0.6)
        self.w, self.h = w, h
        self.blocks = []
        self.issues = []

    def claim(self, name, x, y, w, h):
        if x < -0.01 or y < -0.01 or x + w > self.w + 0.01 or y + h > self.h + 0.01:
            self.issues.append(f'block {name} outside page')
        for (n, bx, by, bw, bh) in self.blocks:
            if x < bx + bw and bx < x + w and y < by + bh and by < y + h:
                self.issues.append(f'block {name} overlaps {n}')
        self.blocks.append((name, x, y, w, h))

    def text(self, x, y, s, role='G', size=9.0, align='l', maxw=None,
             color=None, minsize=4.0, chk=True):
        font = pick(role, s)
        w = pdfmetrics.stringWidth(s, font, size)
        if maxw is not None and w > maxw:
            while w > maxw and size > minsize:
                size = round(size - 0.25, 2)
                w = pdfmetrics.stringWidth(s, font, size)
            if w > maxw:
                self.issues.append(f'overflow: {s}')
        if align == 'r':
            x0 = x - w
        elif align == 'c':
            x0 = x - w / 2
        else:
            x0 = x
        if chk and (x0 < -0.5 or x0 + w > self.w + 0.5 or y < 0 or y + size > self.h + 0.5):
            self.issues.append(f'text off page: {s}')
        c = self.c
        c.saveState()
        c.setFillColor(color if color is not None else INK)
        c.setFont(font, size)
        c.drawString(x0, y, s)
        c.restoreState()
        return w

    def vtext(self, x, ytop, s, role='M', size=12.0, step=None, color=None):
        """Top-to-bottom vertical text centred on x. Returns last baseline."""
        font = pick(role, s)
        step = step or size * 1.08
        c = self.c
        c.saveState()
        c.setFillColor(color if color is not None else INK)
        c.setFont(font, size)
        y = ytop - size
        last = y
        for ch in s:
            if ch not in (' ', '　'):
                c.drawCentredString(x, y + size * 0.12, ch)
            last = y
            y -= step
        c.restoreState()
        if ytop > self.h + 0.5 or last < 0:
            self.issues.append(f'vertical text off page: {s}')
        return last

    def finish(self):
        self.c.showPage()
        self.c.save()
        return self.issues


def round_seal(sh, cx, cy, r, chars, role='K', color=RED):
    c = sh.c
    c.saveState()
    c.setStrokeColor(color)
    c.setFillColor(color)
    c.setLineWidth(max(0.8, r * 0.07))
    c.circle(cx, cy, r, stroke=1, fill=0)
    n = len(chars)
    size = min(r * 0.95, 1.55 * r / n)
    c.setFont(pick(role, chars), size)
    top = cy + n * size / 2
    for i, ch in enumerate(chars):
        c.drawCentredString(cx, top - (i + 1) * size + size * 0.14, ch)
    c.restoreState()


def square_seal(sh, x, y, s, chars, role='K', color=RED):
    """chars: right column top, right column bottom, left column top, left column bottom."""
    c = sh.c
    c.saveState()
    c.setStrokeColor(color)
    c.setFillColor(color)
    c.setLineWidth(1.4)
    c.rect(x, y, s, s, stroke=1, fill=0)
    c.setLineWidth(0.5)
    c.rect(x + 2, y + 2, s - 4, s - 4, stroke=1, fill=0)
    size = s * 0.36
    c.setFont(pick(role, ''.join(chars)), size)
    pos = [(x + s * 0.72, y + s * 0.56), (x + s * 0.72, y + s * 0.14),
           (x + s * 0.28, y + s * 0.56), (x + s * 0.28, y + s * 0.14)]
    for ch, (px, py) in zip(chars, pos):
        c.drawCentredString(px, py, ch)
    c.restoreState()


def dashed(c, x1, y1, x2, y2, color=GRAY, on=3, off=2, lw=0.5):
    c.saveState()
    c.setStrokeColor(color)
    c.setDash(on, off)
    c.setLineWidth(lw)
    c.line(x1, y1, x2, y2)
    c.restoreState()


DAIJI = ['', '壱', '弐', '参', '四', '伍', '六', '七', '八', '九']


def daiji(n):
    assert 0 < n < 100000000

    def group(g):
        out = ''
        for val, unit in ((1000, '阡'), (100, '百'), (10, '拾'), (1, '')):
            d = g // val % 10
            if d:
                out += DAIJI[d] + unit
        return out

    man, rest = divmod(n, 10000)
    s = ''
    if man:
        s += group(man) + '萬'
    return s + group(rest)


# ---------------------------------------------------------------- V091

def case_v091(path):
    W, H = 300 * mm, 190 * mm
    sh = Sheet(path, W, H, 'ホテル精算書 (bifold folio)')
    c = sh.c
    navy = HexColor('#1f3554')

    # centre fold of the bifold folio
    dashed(c, W / 2, 6 * mm, W / 2, H - 6 * mm, color=HexColor('#9aa4b0'), on=4, off=3)

    # ---------- left panel: charges
    L, LR = 14 * mm, 143 * mm
    sh.claim('L-head', L, H - 37 * mm, LR - L, 23 * mm)
    sh.text(L, H - 20 * mm, 'ホテル架空ベイテラス', 'M', 16, color=navy)
    sh.text(L, H - 26 * mm, 'テスト市架空町3-1　TEL 000-0000-0000', 'G', 7)
    sh.text(L, H - 35 * mm, 'ご利用明細　GUEST FOLIO', 'B', 10)
    sh.text(LR, H - 35 * mm, 'Folio No. F-24111407', 'G', 7, align='r')

    sh.claim('L-guest', L, H - 58 * mm, LR - L, 17 * mm)
    c.rect(L, H - 58 * mm, LR - L, 17 * mm)
    c.line(L, H - 50.5 * mm, LR, H - 50.5 * mm)
    cw = (LR - L) / 3
    for i in (1, 2):
        c.line(L + i * cw, H - 58 * mm, L + i * cw, H - 41 * mm)
    row1 = [('客室', '0712'), ('ご宿泊者', '架空 一郎 様'), ('人数', '1名')]
    row2 = [('ご到着', '2024-11-12'), ('ご出発', '2024-11-14'), ('泊数', '2泊')]
    for i, (k, v) in enumerate(row1):
        x = L + i * cw + 2 * mm
        sh.text(x, H - 45 * mm, k, 'G', 6, color=GRAY)
        sh.text(x, H - 49 * mm, v, 'G', 9, maxw=cw - 4 * mm)
    for i, (k, v) in enumerate(row2):
        x = L + i * cw + 2 * mm
        sh.text(x, H - 53.5 * mm, k, 'G', 6, color=GRAY)
        sh.text(x, H - 57 * mm, v, 'G', 9, maxw=cw - 4 * mm)

    rows = [
        ('2024-11-12', '宿泊料 シングル（素泊まり）', '10%', 12100),
        ('2024-11-12', '宿泊税', '対象外', 200),
        ('2024-11-13', '朝食ビュッフェ（レストラン汐風）', '10%', 1650),
        ('2024-11-13', '宿泊料 シングル（素泊まり）', '10%', 12100),
        ('2024-11-13', '宿泊税', '対象外', 200),
        ('2024-11-14', '朝食ビュッフェ（レストラン汐風）', '10%', 1650),
    ]
    total = sum(r[3] for r in rows)
    taxable = sum(r[3] for r in rows if r[2] == '10%')
    tax = taxable // 11
    assert (total, taxable, tax) == (27900, 27500, 2500)

    sh.claim('L-table', L, H - 112 * mm, LR - L, 51 * mm)
    c.saveState()
    c.setFillColor(HexColor('#e6eaf0'))
    c.rect(L, H - 67 * mm, LR - L, 6 * mm, stroke=0, fill=1)
    c.restoreState()
    hy = H - 65.2 * mm
    sh.text(L + 1 * mm, hy, '日付', 'B', 7.5)
    sh.text(L + 28 * mm, hy, '摘要', 'B', 7.5)
    sh.text(L + 86 * mm, hy, '税区分', 'B', 7.5)
    sh.text(LR - 1 * mm, hy, '金額（円）', 'B', 7.5, align='r')
    y = H - 73 * mm
    c.saveState()
    c.setStrokeColor(LIGHT)
    for d, desc, rate, amt in rows:
        sh.text(L + 1 * mm, y, d, 'G', 8)
        sh.text(L + 28 * mm, y, desc, 'G', 8.5, maxw=56 * mm)
        sh.text(L + 86 * mm, y, rate, 'G', 7.5)
        sh.text(LR - 1 * mm, y, f'{amt:,}', 'G', 9, align='r')
        c.line(L, y - 2.2 * mm, LR, y - 2.2 * mm)
        y -= 7 * mm
    c.restoreState()
    c.setLineWidth(1.0)
    c.line(L, H - 112 * mm, LR, H - 112 * mm)
    c.setLineWidth(0.6)
    sh.text(L + 28 * mm, H - 119 * mm, 'ご利用合計', 'B', 11)
    sh.text(LR - 1 * mm, H - 119 * mm, f'￥{total:,}', 'B', 13, align='r')
    sh.text(L, H - 126 * mm,
            f'10%対象 ￥{taxable:,}（内消費税 ￥{tax:,}）／ 対象外（宿泊税） ￥400',
            'G', 7.5, maxw=LR - L)
    sh.text(L, H - 131.5 * mm, '登録番号 T0000000000000', 'G', 6.5, color=GRAY)
    sh.text(L, 16 * mm, 'またのご利用をお待ちしております', 'M', 7.5, color=navy)
    sh.text(L, 9 * mm, '架空テスト', 'G', 6, color=GRAY)

    # ---------- right panel: settlement
    R, RR = 157 * mm, 286 * mm
    sh.claim('R-head', R, H - 23 * mm, RR - R, 9 * mm)
    sh.text(R, H - 20 * mm, 'ご精算書', 'M', 14, color=navy)
    sh.text(R + 33 * mm, H - 20 * mm, 'SETTLEMENT', 'G', 8, color=GRAY)

    sh.claim('R-total', R, H - 50 * mm, RR - R, 20 * mm)
    c.saveState()
    c.setStrokeColor(navy)
    c.setLineWidth(1.2)
    c.rect(R, H - 50 * mm, RR - R, 20 * mm)
    c.restoreState()
    sh.text(R + 4 * mm, H - 36 * mm, 'ご請求総額（税込）', 'G', 8)
    sh.text(RR - 5 * mm, H - 46 * mm, f'￥{total:,}', 'B', 22, align='r', color=navy)

    sh.claim('R-settle', R, H - 93 * mm, RR - R, 37 * mm)
    sh.text(R + 1 * mm, H - 60 * mm, '日付', 'B', 7.5)
    sh.text(R + 26 * mm, H - 60 * mm, 'お支払内容', 'B', 7.5)
    sh.text(RR - 1 * mm, H - 60 * mm, '金額（円）', 'B', 7.5, align='r')
    c.line(R, H - 62 * mm, RR, H - 62 * mm)
    pays = [('2024-11-12', 'お預り金（現金・チェックイン時）', 10000),
            ('2024-11-14', 'クレジットカード一括　承認番号 000000', 17900)]
    assert sum(p[2] for p in pays) == total
    y = H - 67 * mm
    for d, desc, amt in pays:
        sh.text(R + 1 * mm, y, d, 'G', 8)
        sh.text(R + 26 * mm, y, desc, 'G', 8, maxw=80 * mm)
        sh.text(RR - 1 * mm, y, f'{amt:,}', 'G', 9, align='r')
        y -= 7 * mm
    c.line(R, H - 77.5 * mm, RR, H - 77.5 * mm)
    sh.text(R + 26 * mm, H - 84 * mm, 'お支払合計', 'B', 9.5)
    sh.text(RR - 1 * mm, H - 84 * mm, f'{total:,}', 'B', 10, align='r')
    sh.text(R + 26 * mm, H - 91 * mm, '差引残高', 'G', 9)
    sh.text(RR - 1 * mm, H - 91 * mm, '0', 'G', 10, align='r')

    sh.text(R, H - 104 * mm, '上記の金額を正に領収いたしました。', 'M', 9)
    sh.text(R, H - 112 * mm, '発行日　2024年11月14日　10:12', 'G', 8)
    sh.claim('R-seal', RR - 27 * mm, H - 139 * mm, 18 * mm, 18 * mm)
    round_seal(sh, RR - 18 * mm, H - 130 * mm, 8.5 * mm, '架空印', role='M')
    sh.text(R, H - 141 * mm, 'フロント担当', 'G', 8)
    c.line(R + 20 * mm, H - 142 * mm, R + 70 * mm, H - 142 * mm)
    sh.text(R + 22 * mm, H - 141 * mm, 'テスト', 'G', 8, color=GRAY)
    sh.text(R, 16 * mm, '※本精算書は領収書を兼ねます。', 'G', 7)

    items = [{'name': d + ' ' + desc, 'gross': amt,
              'rate': '10' if rate == '10%' else 'exempt'} for d, desc, rate, amt in rows]
    meta = {
        'title': 'ホテル見開き精算書（明細左・精算右）',
        'layout_id': 'opus-01',
        'layout_description': '300x190mm横長の見開き(中央に折り線)。左面に日付別の宿泊・朝食・宿泊税の明細表と税率別内訳、'
                              '右面に請求総額ボックス、デポジットとカードの二段精算表、残高0、署名欄と丸印。'
                              '合計は左面下部と右面上部の二か所に出る。',
        'document_kind': 'invoice_receipt',
        'merchant': 'ホテル架空ベイテラス',
        'date': '2024-11-14',
        'date_printed': '2024年11月14日',
        'total': total,
        'acceptable_accounts': [],
        'expected_decision': 'hold',
        'human_context': '2泊の出張宿泊。宿泊料24,200円と宿泊税400円は業務だが、朝食2回(計3,300円)は社内規程上個人負担扱いの予定で、'
                         '按分か朝食分の除外かが未確認。支払はチェックイン時の現金デポジット10,000円とチェックアウト時カード17,900円の合計。',
        'review_questions': ['朝食3,300円を個人負担として除外するか', '宿泊部分(24,600円)を旅費交通費として計上してよいか',
                             'デポジット10,000円の現金支出を別途二重計上していないか'],
        'features': ['bifold wide landscape', 'charges left / settlement right', 'deposit plus card split tender',
                     'non-taxable accommodation tax line', 'round seal', 'ISO dates in rows, kanji issue date'],
        'items': items,
    }
    return meta, sh.finish()


# ---------------------------------------------------------------- V092

def case_v092(path):
    W, H = 48 * mm, 150 * mm
    sh = Sheet(path, W, H, '領収証 (historic register)')
    c = sh.c
    ink = HexColor('#3b2f6e')   # ribbon-ink purple
    x0, x1 = 3.5 * mm, W - 3.5 * mm
    cw = x1 - x0
    st = {'y': 0.0}

    def ln(left, right='', size=7.0, role='Mono'):
        y = st['y']
        sh.text(x0, y, left, role, size, color=ink, maxw=cw * (0.66 if right else 1.0))
        if right:
            sh.text(x1, y, right, role, size, align='r', color=ink, maxw=cw * 0.32)
        st['y'] = y - size * 1.5

    def dash():
        y = st['y'] + 3
        c.saveState()
        c.setStrokeColor(ink)
        c.setDash(1.2, 1.2)
        c.setLineWidth(0.4)
        c.line(x0, y, x1, y)
        c.restoreState()
        st['y'] -= 5

    lines = [('ノートB5', 1, 220), ('ボールペン黒', 2, 165), ('封筒角2 50枚', 1, 528)]
    total = sum(q * u for _, q, u in lines)
    assert total == 1078
    tax = total // 11

    # total printed first, as on old journal-style registers
    sh.claim('total-top', x0, H - 12 * mm, cw, 8 * mm)
    sh.text(x0, H - 10 * mm, '合計', 'Mono', 10, color=ink)
    sh.text(x1, H - 10 * mm, f'￥{total:,}', 'Mono', 13, align='r', color=ink)
    st['y'] = H - 16 * mm
    ln('お預り', '2,000')
    ln('お釣り', '922')
    ln('      領 収 証', size=8)
    dash()
    ln('R.6- 3- 8', 'No.0417')
    ln('レジ02', '担当07')
    dash()
    ln('03 ノートB5', '220')
    ln('03 ボールペン黒')
    ln('   @165 x 2', '330')
    ln('05 封筒角2 50枚', '528')
    dash()
    ln('小計', f'{total:,}')
    ln('点数', '4')
    ln('(内税10%', f'{tax})')
    dash()
    st['y'] -= 6
    ln('テスト文具店', size=8, role='M')
    ln('架空町本店')
    ln('TEL 000-000-0000')
    ln('毎度ありがとうございます', size=6)
    ln('架空テスト', size=5.5)

    meta = {
        'title': '細幅レジ紙・合計先頭型（旧式キャッシュレジスター）',
        'layout_id': 'opus-02',
        'layout_description': '48mm幅の極細ロール。先頭行に合計金額、直後に預り/釣り、その下に日付・レジ番号、'
                              '部門コード付き品目、末尾に店名が来る逆順構成。紫リボン色の等幅文字と点線区切りのみ。',
        'document_kind': 'thermal_receipt',
        'merchant': 'テスト文具店 架空町本店',
        'date': '2024-03-08',
        'date_printed': 'R.6- 3- 8',
        'total': total,
        'acceptable_accounts': ['消耗品費', '事務用品費'],
        'expected_decision': 'propose',
        'human_context': '事務所用のノート・ボールペン・封筒を現金購入。業務用途のみ。和暦略記の日付をそのまま読めるか確認する目的。',
        'review_questions': ['R.6- 3- 8 を2024年3月8日と解釈してよいか', '預り2,000円・釣り922円を合計と取り違えていないか'],
        'features': ['48mm narrow roll', 'total at top', 'department codes', 'abbreviated Reiwa date',
                     'tender and change', 'store name at bottom', 'monospace ribbon ink'],
        'items': [{'name': 'ノートB5', 'gross': 220, 'rate': '10'},
                  {'name': 'ボールペン黒 x2', 'gross': 330, 'rate': '10'},
                  {'name': '封筒角2 50枚', 'gross': 528, 'rate': '10'}],
    }
    return meta, sh.finish()


# ---------------------------------------------------------------- V093

def case_v093(path):
    W, H = 210 * mm, 148 * mm
    sh = Sheet(path, W, H, '領収証 (stamp voucher)')
    c = sh.c
    green = HexColor('#24543e')
    amount = 132000
    net, tax = 120000, 12000
    assert net + tax == amount

    c.saveState()
    c.setStrokeColor(green)
    c.setLineWidth(1.6)
    c.rect(6 * mm, 6 * mm, W - 12 * mm, H - 12 * mm)
    c.setLineWidth(0.5)
    c.rect(7.5 * mm, 7.5 * mm, W - 15 * mm, H - 15 * mm)
    c.line(32 * mm, 7.5 * mm, 32 * mm, H - 7.5 * mm)
    c.restoreState()

    # left strip: vertical title and vertical marker
    sh.vtext(19.5 * mm, 128 * mm, '領収証', 'M', 24, step=30, color=green)
    sh.vtext(19.5 * mm, 40 * mm, '架空テスト', 'G', 6, color=GRAY)

    sh.claim('no', 160 * mm, 128 * mm, 37 * mm, 5 * mm)
    sh.text(196 * mm, 130 * mm, 'No. 0093-27', 'G', 8, align='r')
    sh.claim('recipient', 38 * mm, 118 * mm, 95 * mm, 10 * mm)
    sh.text(40 * mm, 122 * mm, '株式会社テスト商会　御中', 'M', 14, maxw=88 * mm)
    c.line(40 * mm, 120 * mm, 130 * mm, 120 * mm)
    sh.claim('date', 158 * mm, 118 * mm, 39 * mm, 6 * mm)
    sh.text(196 * mm, 120 * mm, '令和五年九月二十九日', 'M', 10, align='r')

    chars = '金' + daiji(amount) + '円也'
    assert chars == '金壱拾参萬弐阡円也'
    bs = 13 * mm
    bx, by = 56 * mm, 86.5 * mm
    sh.claim('kanji-boxes', bx, by, bs * len(chars), bs)
    sh.text(40 * mm, by + bs / 2 - 3, '金額', 'M', 9)
    c.saveState()
    c.setStrokeColor(green)
    c.setLineWidth(0.9)
    for i, ch in enumerate(chars):
        c.rect(bx + i * bs, by, bs, bs)
    c.restoreState()
    c.setFont(pick('M', chars), 20)
    for i, ch in enumerate(chars):
        c.drawCentredString(bx + i * bs + bs / 2, by + bs / 2 - 20 * 0.36, ch)

    sh.claim('numeric', bx, 70 * mm, 110 * mm, 8 * mm)
    sh.text(bx, 72 * mm, f'￥{amount:,}－', 'B', 17)
    sh.text(bx + 52 * mm, 72.5 * mm, '（税込）', 'G', 8)
    sh.text(bx, 64 * mm, f'（税抜金額 {net:,}円　消費税額等（10%）{tax:,}円）', 'G', 8, maxw=120 * mm)

    sh.text(40 * mm, 52 * mm, '但　ロゴデザイン制作業務委託料として', 'M', 11, maxw=108 * mm)
    c.line(40 * mm, 50.5 * mm, 150 * mm, 50.5 * mm)
    sh.text(40 * mm, 42 * mm, '上記正に領収いたしました', 'M', 9)
    sh.text(40 * mm, 30 * mm, 'お支払方法　□現金　■銀行振込　□小切手', 'G', 8, maxw=76 * mm)

    sh.claim('issuer', 120 * mm, 20 * mm, 78 * mm, 26 * mm)
    sh.text(122 * mm, 40 * mm, '合同会社架空デザイン工房', 'M', 10, maxw=52 * mm)
    sh.text(122 * mm, 34 * mm, 'テスト市架空町2-8-1', 'G', 7.5)
    sh.text(122 * mm, 29 * mm, '登録番号 T0000000000000', 'G', 7)
    sh.text(122 * mm, 24 * mm, '担当　デザイン部 テスト', 'G', 7)
    square_seal(sh, 177 * mm, 24 * mm, 18 * mm, ['架', '空', '工', '房'], role='K')

    meta = {
        'title': '横型社印領収証（大字枠＋算用数字併記）',
        'layout_id': 'opus-03',
        'layout_description': 'A5横。二重枠と左端の縦書き表題帯。金額は一文字ずつ罫枠に入れた大字(金壱拾参萬弐阡円也)と、'
                              'その下の算用数字の二重表記。漢数字和暦の日付は右上、発行者と角印は右下、支払方法チェック欄は左下。',
        'document_kind': 'voucher',
        'merchant': '合同会社架空デザイン工房',
        'date': '2023-09-29',
        'date_printed': '令和五年九月二十九日',
        'total': amount,
        'acceptable_accounts': ['外注費', '広告宣伝費', '支払手数料'],
        'expected_decision': 'propose',
        'human_context': '自社ロゴ制作を外部デザイン事務所に委託し銀行振込で支払った分の領収証。業務用。',
        'review_questions': ['大字表記と算用数字が一致しているか', '振込記録と二重計上になっていないか',
                             'ロゴ制作費の勘定科目(外注費か広告宣伝費か)'],
        'features': ['A5 landscape', 'boxed daiji kanji numerals', 'numeric total duplicate', 'vertical title strip',
                     'kanji Reiwa date', 'square company seal', 'payment method checkboxes'],
        'items': [{'name': 'ロゴデザイン制作業務委託料', 'gross': amount, 'rate': '10'}],
    }
    return meta, sh.finish()


# ---------------------------------------------------------------- V094

ROWS94 = [
    ('炭素皮膜抵抗 1/4W 10kΩ（100本入）', 'TR-CF14-103', 3, 380),
    ('積層セラミックコンデンサ 0.1μF（50個）', 'TC-MLC-104', 2, 450),
    ('電解コンデンサ 470μF 25V', 'TC-EL-47125', 10, 48),
    ('LED 赤 5mm（100個）', 'TL-R5-100', 1, 980),
    ('ピンヘッダ 1×40P', 'TP-H140', 20, 35),
    ('XHコネクタ ハウジング 4P', 'TX-H04', 50, 12),
    ('圧着端子 XH用', 'TX-T01', 100, 4),
    ('ユニバーサル基板 95×72mm', 'TB-U9572', 5, 260),
    ('はんだ 0.8mm 100g', 'TS-08-100', 2, 1480),
    ('フラックス 20ml', 'TF-20', 1, 690),
    ('熱収縮チューブ φ3 1m', 'TH-3-1M', 10, 55),
    ('ビニル線 AWG24 10m 黒', 'TW-24-BK', 3, 420),
    ('ビニル線 AWG24 10m 赤', 'TW-24-RD', 3, 420),
    ('ターミナルブロック 2P', 'TT-B2', 10, 88),
    ('DC-DCコンバータ 5V 2A', 'TD-0502', 4, 1250),
    ('ACアダプタ 12V 3A', 'TA-1203', 2, 2380),
    ('ロジックIC 実験セット', 'TK-LOGIC-S', 1, 3600),
    ('ESD対策マット 600×450mm', 'TE-M6045', 1, 6800),
    ('デジタルオシロスコープ 2ch 100MHz', 'TQ-OSC-2100', 1, 98000),
    ('オシロプローブ 10:1（2本組）', 'TQ-PRB-10', 1, 4200),
]


def case_v094(path):
    W, H = 210 * mm, 297 * mm
    sh = Sheet(path, W, H, '請求書兼領収書 (technical goods)')
    c = sh.c

    net = sum(q * u for _, _, q, u in ROWS94)
    tax = net * 10 // 100
    total = net + tax
    assert (net, tax, total) == (136460, 13646, 150106)

    # recipient window block (top-left)
    sh.claim('recipient', 40, 742, 260, 60)
    sh.text(40, 790, '〒000-0000', 'G', 8)
    sh.text(40, 778, 'テスト市架空町5-5', 'G', 8)
    sh.text(40, 760, '架空ロボティクス株式会社', 'M', 12)
    sh.text(52, 746, '開発部　御中', 'M', 10)

    # title and issuer (top-right)
    sh.claim('issuer', 400, 738, 155, 72)
    sh.text(555, 798, '請求書 兼 領収書', 'B', 15, align='r')
    sh.text(555, 776, '株式会社テスト電子商会', 'M', 11, align='r')
    sh.text(555, 763, 'テスト市架空町1-20-3 架空ビル2F', 'G', 7, align='r')
    sh.text(555, 752, 'TEL 000-0000-0000　FAX 000-0000-0001', 'G', 7, align='r')
    sh.text(555, 741, '登録番号 T0000000000000', 'G', 7, align='r')

    # meta grid
    sh.claim('meta', 40, 700, 515, 28)
    c.rect(40, 700, 515, 28)
    cells = [('請求番号', 'INV-250210-031'), ('請求日', '2025/02/10'),
             ('入金日', '2025/02/14'), ('お支払方法', '銀行振込')]
    cwid = 515 / 4
    for i, (k, v) in enumerate(cells):
        x = 40 + i * cwid
        if i:
            c.line(x, 700, x, 728)
        sh.text(x + 5, 719, k, 'G', 6.5, color=GRAY)
        sh.text(x + 5, 705, v, 'G', 9.5, maxw=cwid - 10)
    sh.text(40, 686, '下記のとおりご請求申し上げます。本書は入金確認後の領収書を兼ねます。', 'G', 8, maxw=515)

    # dense table with carried subtotals
    cols = [40, 62, 262, 372, 410, 475, 555]
    top, hb = 680, 664
    sh.claim('table', 40, 304, 515, 376)
    c.saveState()
    c.setFillColor(HexColor('#e4e8ec'))
    c.rect(40, hb, 515, 16, stroke=0, fill=1)
    c.restoreState()
    heads = [('No', 51, 'c'), ('品名', 66, 'l'), ('型番', 266, 'l'), ('数量', 406, 'r'),
             ('単価', 471, 'r'), ('金額', 551, 'r')]
    for s, x, a in heads:
        sh.text(x, hb + 5, s, 'B', 7.5, align=a)
    y = hb
    cum = 0
    for g in range(4):
        sub = 0
        for i in range(5):
            n = g * 5 + i
            name, model, q, u = ROWS94[n]
            amt = q * u
            sub += amt
            y -= 15
            if i % 2 == 1:
                c.saveState()
                c.setFillColor(HexColor('#f6f7f9'))
                c.rect(40, y, 515, 15, stroke=0, fill=1)
                c.restoreState()
            sh.text(51, y + 4.5, str(n + 1), 'G', 7.5, align='c')
            sh.text(66, y + 4.5, name, 'G', 7.5, maxw=192)
            sh.text(266, y + 4.5, model, 'G', 7, maxw=102)
            sh.text(406, y + 4.5, f'{q:,}', 'G', 7.5, align='r')
            sh.text(471, y + 4.5, f'{u:,}', 'G', 7.5, align='r')
            sh.text(551, y + 4.5, f'{amt:,}', 'G', 7.5, align='r')
            c.saveState()
            c.setStrokeColor(LIGHT)
            c.setLineWidth(0.3)
            c.line(40, y, 555, y)
            c.restoreState()
        cum += sub
        y -= 15
        c.saveState()
        c.setFillColor(HexColor('#f3efe0'))
        c.rect(40, y, 515, 15, stroke=0, fill=1)
        c.restoreState()
        sh.text(66, y + 4.5, f'小計（No.{g * 5 + 1}〜{g * 5 + 5}）', 'B', 7.5)
        sh.text(266, y + 4.5, '累計（繰越）', 'G', 7, color=GRAY)
        sh.text(471, y + 4.5, f'{cum:,}', 'G', 7.5, align='r')
        sh.text(551, y + 4.5, f'{sub:,}', 'B', 8, align='r')
        c.line(40, y, 555, y)
    assert cum == net and abs(y - 304) < 0.01
    c.saveState()
    c.setLineWidth(0.4)
    for x in cols:
        c.line(x, y, x, top)
    c.line(40, top, 555, top)
    c.restoreState()

    # summary box (right)
    sh.claim('summary', 355, 228, 200, 68)
    c.saveState()
    c.setFillColor(HexColor('#e4e8ec'))
    c.rect(355, 228, 200, 26, stroke=0, fill=1)
    c.restoreState()
    c.rect(355, 228, 200, 68)
    c.line(355, 254, 555, 254)
    sh.text(365, 280, '税抜合計', 'G', 8.5)
    sh.text(549, 280, f'{net:,}', 'G', 9, align='r')
    sh.text(365, 262, '消費税（10%）', 'G', 8.5)
    sh.text(549, 262, f'{tax:,}', 'G', 9, align='r')
    sh.text(365, 237, 'ご請求金額（税込）', 'B', 9)
    sh.text(549, 236, f'￥{total:,}', 'B', 13, align='r')
    sh.text(555, 214, f'税率別内訳　10%対象 {net:,}円　消費税 {tax:,}円', 'G', 7, align='r')

    # paid stamp (left)
    sh.claim('stamp', 40, 228, 290, 68)
    c.saveState()
    c.translate(118, 262)
    c.rotate(-6)
    c.setStrokeColor(RED)
    c.setLineWidth(1.6)
    c.rect(-62, -24, 124, 48)
    c.setLineWidth(0.5)
    c.rect(-59, -21, 118, 42)
    sh.text(0, 1, '領 収 済', 'B', 17, align='c', color=RED, chk=False)
    sh.text(0, -15, '2025.02.14 振込入金確認', 'G', 7, align='c', color=RED, chk=False)
    c.restoreState()
    sh.text(200, 278, '上記金額を', 'G', 8)
    sh.text(200, 266, '2025年2月14日', 'G', 8)
    sh.text(200, 254, '銀行振込にて', 'G', 8)
    sh.text(200, 242, '領収いたしました。', 'G', 8)

    sh.text(40, 190, '※ 計測器（No.19）の校正証明書は別送いたします。', 'G', 7.5)
    sh.text(40, 178, '※ 単価・金額は税抜表示です。消費税は合計額に対して計算しています。', 'G', 7.5)
    sh.text(297, 28, '1 / 1', 'G', 7, align='c')
    sh.text(555, 28, '架空テスト', 'G', 6, align='r', color=GRAY)

    items = [{'name': name, 'gross': q * u * 110 // 100, 'net': q * u, 'rate': '10'} for name, _, q, u in ROWS94]
    meta = {
        'title': 'A4電子部品商社 請求書兼領収書（20行・繰越小計）',
        'layout_id': 'opus-04',
        'layout_description': 'A4縦の高密度表。左上に窓封筒型の宛先、右上に表題と発行者、4セルのメタ罫線行。'
                              '20明細を5行ごとに小計行と累計(繰越)で区切り、右下に税抜/消費税/税込の集計枠、'
                              '左下に回転した赤い領収済スタンプと入金日文言。',
        'document_kind': 'invoice_receipt',
        'merchant': '株式会社テスト電子商会',
        'date': '2025-02-14',
        'date_printed': '2025/02/14',
        'total': total,
        'acceptable_accounts': ['工具器具備品', '消耗品費'],
        'expected_decision': 'asset_review',
        'human_context': '試作開発用の電子部品一式とオシロスコープ1台を購入し振込で支払済み。オシロスコープ(税抜98,000円)は'
                         '資産計上の要否を確認する必要がある。請求日2025/02/10、入金日2025/02/14。',
        'review_questions': ['オシロスコープを固定資産として扱うか消耗品とするか', '請求日と入金日のどちらを取引日とするか',
                             '部品類と計測器を分けて計上するか'],
        'features': ['A4 portrait', '20 dense item rows', 'group subtotals with carried running total',
                     'tax-exclusive line amounts', 'window-envelope recipient block', 'rotated paid stamp',
                     'invoice date vs payment date', 'manifest gross normalized from printed net line amounts'],
        'items': items,
    }
    return meta, sh.finish()


# ---------------------------------------------------------------- V095

def case_v095(path):
    W, H = 175 * mm, 100 * mm
    sh = Sheet(path, W, H, '領収証 (handwritten)')
    c = sh.c
    form = HexColor('#6f8fa8')
    ink = HexColor('#1d2a5c')
    stamp = HexColor('#5b3a7a')

    def hand(x, y, s, size, angle, maxw=None):
        c.saveState()
        c.translate(x, y)
        c.rotate(angle)
        sh.text(0, 0, s, 'H', size, color=ink, maxw=maxw, chk=False)
        c.restoreState()

    # pre-printed form
    c.saveState()
    c.setStrokeColor(form)
    c.setLineWidth(0.8)
    c.rect(14, 14, W - 28, H - 28)
    c.setLineWidth(0.5)
    c.line(415, 250, 470, 250)
    c.line(360, 225, 470, 225)
    c.line(40, 200, 300, 200)
    c.rect(50, 135, 330, 45)
    c.line(56, 103, 380, 103)
    c.restoreState()
    sh.text(W / 2, 245, '領　収　証', 'M', 15, align='c', color=form)
    sh.text(400, 252, 'No.', 'G', 7, color=form)
    sh.text(470, 228, '　　年　　月　　日', 'M', 9, align='r', color=form)
    sh.text(304, 202, '様', 'M', 12, color=form)
    sh.text(56, 166, '金額', 'M', 8, color=form)
    sh.text(40, 106, '但', 'M', 10, color=form)
    sh.text(40, 82, '上記正に領収いたしました', 'M', 8, color=form)

    # handwriting (date intentionally left blank)
    sh.claim('hand-name', 120, 198, 60, 26)
    hand(140, 204, '上', 20, 2)
    sh.claim('hand-amount', 52, 137, 326, 41)
    hand(110, 146, '￥6,500－', 26, -1, maxw=260)
    hand(62, 107, '中古電動ドリル（充電式）代として', 13, 0.8, maxw=310)
    hand(40, 58, '※ケース・充電器なし', 9, -1, maxw=200)

    # rubber stamp of the shop
    sh.claim('stamp', 296, 26, 178, 60)
    c.saveState()
    c.translate(300, 32)
    c.rotate(-2.5)
    c.setStrokeColor(stamp)
    c.setLineWidth(1.0)
    c.rect(0, 0, 170, 50)
    sh.text(85, 32, '架空町リサイクル工具', 'B', 11, align='c', color=stamp, maxw=160, chk=False)
    sh.text(85, 19, 'テスト市架空町9-9', 'G', 7.5, align='c', color=stamp, chk=False)
    sh.text(85, 7, '中古工具 買取・販売　TEL 000-0000-0000', 'G', 6.5, align='c', color=stamp,
            maxw=160, chk=False)
    c.restoreState()
    sh.text(40, 22, '架空テスト', 'G', 6, color=GRAY)

    meta = {
        'title': '手書き中古工具領収証（日付空欄）',
        'layout_id': 'opus-05',
        'layout_description': '175x100mmの横長伝票。薄青の印刷済み書式に、行書体の手書き風文字を微小回転で記入。'
                              '日付欄・No.欄は空白のまま、宛名は「上様」、紫のゴム印で発行者を表示した疎な構成。',
        'document_kind': 'voucher',
        'merchant': '架空町リサイクル工具',
        'date': '',
        'date_printed': '',
        'total': 6500,
        'acceptable_accounts': ['工具器具備品', '消耗品費'],
        'expected_decision': 'hold',
        'human_context': '作業用に中古の充電式ドリル本体のみを現金購入。領収証に日付の記入がなく、購入日は本人の記憶でしか分からない。'
                         '宛名も上様のため、取引日の確認が必要。',
        'review_questions': ['取引日はいつか(書面に記載なし)', '宛名が上様で問題ないか', '中古工具の勘定科目'],
        'features': ['handwriting-like font', 'blank date field', 'blank receipt number', '上様 recipient',
                     'rubber stamp issuer', 'sparse layout', 'slight per-line rotation'],
        'items': [{'name': '中古電動ドリル（充電式）', 'gross': 6500, 'rate': 'unknown'}],
    }
    return meta, sh.finish()


# ---------------------------------------------------------------- V096

def case_v096(path):
    W, H = 612.0, 792.0   # US Letter
    sh = Sheet(path, W, H, 'SaaS 領収書')
    c = sh.c
    teal = HexColor('#0f4c5c')
    net, tax = 108000, 10800
    total = net + tax
    assert total == 118800

    # sidebar
    c.saveState()
    c.setFillColor(teal)
    c.rect(0, 0, 150, H, stroke=0, fill=1)
    c.restoreState()
    sh.claim('sidebar', 0, 0, 150, H)
    sh.text(20, 750, 'NoteBridge', 'B', 13, color=white)
    sh.text(20, 736, 'ノートブリッジ', 'G', 8, color=white)
    c.saveState()
    c.translate(100, 70)
    c.rotate(90)
    sh.text(0, 0, '領収書  RECEIPT', 'B', 34, color=HexColor('#d7ebee'), chk=False)
    c.restoreState()
    sh.text(20, 24, '架空テスト', 'G', 6, color=white)

    X, XR = 180, 572
    sh.claim('issuer', X, 712, XR - X, 44)
    sh.text(X, 742, 'クラウド帳票テスト株式会社', 'B', 12)
    sh.text(X, 728, 'テスト市架空町1-1-1　billing@example.invalid', 'G', 7.5)
    sh.text(X, 716, '登録番号 T0000000000000', 'G', 7.5)

    sh.text(X, 672, '領収書', 'M', 26)
    sh.text(X + 88, 672, 'Receipt', 'G', 11, color=GRAY)
    green = HexColor('#2e7d32')
    sh.claim('badge', 470, 664, 102, 30)
    c.saveState()
    c.setStrokeColor(green)
    c.setLineWidth(1.4)
    c.roundRect(470, 664, 102, 30, 6, stroke=1, fill=0)
    c.restoreState()
    sh.text(521, 675, 'お支払済み', 'B', 11, align='c', color=green)

    kv = [('領収書番号', 'RCP-2025-000418'), ('発行日', '2025-04-01'), ('お支払日', '2025-04-01'),
          ('お支払方法', 'クレジットカード（末尾 0000）'), ('ご契約者', '架空ソリューションズ合同会社'),
          ('ご契約ID', 'ACC-7781-TEST')]
    sh.claim('kv', X, 546, XR - X, 102)
    y = 636
    for k, v in kv:
        sh.text(X, y, k, 'G', 8, color=GRAY)
        sh.text(X + 100, y, v, 'G', 9.5, maxw=290)
        c.saveState()
        c.setStrokeColor(HexColor('#dddddd'))
        c.line(X, y - 5, XR, y - 5)
        c.restoreState()
        y -= 17

    # billing period timeline
    sh.claim('period', X, 460, XR - X, 72)
    sh.text(X, 520, 'ご利用期間', 'B', 9)
    sh.text(X + 62, 520, '2025-04-01 〜 2026-03-31（12か月・年額前払い）', 'G', 9, maxw=325)
    seg = (XR - X) / 12
    months = [4, 5, 6, 7, 8, 9, 10, 11, 12, 1, 2, 3]
    for i, m in enumerate(months):
        c.saveState()
        c.setFillColor(HexColor('#cfe6ea') if i % 2 == 0 else HexColor('#b5d8de'))
        c.rect(X + i * seg, 488, seg, 12, stroke=0, fill=1)
        c.restoreState()
        sh.text(X + i * seg + seg / 2, 476, f'{m}月', 'G', 6.5, align='c')
    sh.text(X, 504, '2025', 'G', 6.5, color=GRAY)
    sh.text(X + 9 * seg, 504, '2026', 'G', 6.5, color=GRAY)
    sh.text(X, 464, '▲お支払日 2025-04-01（期間開始日に一括請求）', 'G', 6.5, color=teal)

    # line item + totals
    sh.claim('items', X, 330, XR - X, 120)
    sh.text(X, 440, '内容', 'G', 8, color=GRAY)
    sh.text(440, 440, '数量', 'G', 8, color=GRAY, align='r')
    sh.text(XR, 440, '金額（税抜）', 'G', 8, color=GRAY, align='r')
    c.line(X, 435, XR, 435)
    sh.text(X, 418, 'ノートブリッジ Pro 年間プラン（5ユーザー）', 'G', 9.5, maxw=230)
    sh.text(440, 418, '1', 'G', 9.5, align='r')
    sh.text(XR, 418, f'{net:,}', 'G', 9.5, align='r')
    c.line(X, 408, XR, 408)
    sh.text(380, 390, '小計', 'G', 9)
    sh.text(XR, 390, f'{net:,}', 'G', 9, align='r')
    sh.text(380, 374, '消費税（10%）', 'G', 9)
    sh.text(XR, 374, f'{tax:,}', 'G', 9, align='r')
    c.setLineWidth(1.0)
    c.line(380, 366, XR, 366)
    c.setLineWidth(0.6)
    sh.text(380, 345, '合計（税込）', 'B', 11)
    sh.text(XR, 342, f'￥{total:,}', 'B', 18, align='r')

    sh.text(X, 300, '本領収書は電子的に発行しています。年額料金は上記ご利用期間分の前払いです。', 'G', 8, maxw=XR - X)
    sh.text(X, 287, 'お問い合わせは上記メールアドレスまでお願いいたします。', 'G', 8, maxw=XR - X)

    meta = {
        'title': 'SaaS年額前払い 電子領収書（利用期間タイムライン付）',
        'layout_id': 'opus-06',
        'layout_description': 'US Letter縦。左に濃色の縦帯(回転した大見出しとブランド名)、右側に発行者、領収書見出しと支払済みバッジ、'
                              'ラベル/値のキー・バリュー表、12か月を色分けした利用期間バー、単一明細と右寄せ合計。',
        'document_kind': 'invoice_receipt',
        'merchant': 'クラウド帳票テスト株式会社',
        'date': '2025-04-01',
        'date_printed': '2025-04-01',
        'total': total,
        'acceptable_accounts': ['通信費', '支払手数料', '前払費用'],
        'expected_decision': 'hold',
        'human_context': '業務用クラウドサービスの年額プランをカードで一括払い。利用期間が2025-04-01〜2026-03-31にわたるため、'
                         '期間按分(前払費用)にするか一括費用とするかの方針確認が必要。',
        'review_questions': ['年額前払いを期間按分するか一括で費用処理するか', '会計期間をまたぐ部分の扱い',
                             'カード明細の計上日と発行日の整合'],
        'features': ['US Letter portrait', 'colored left sidebar with rotated heading', 'issue date vs billing period',
                     '12-month timeline bar', 'paid badge', 'card last digits', 'annual prepayment'],
        'items': [{'name': 'ノートブリッジ Pro 年間プラン（5ユーザー）', 'gross': total, 'rate': '10'}],
    }
    return meta, sh.finish()


# ---------------------------------------------------------------- V097

def case_v097(path):
    W, H = 148 * mm, 210 * mm
    sh = Sheet(path, W, H, '返金明細書')
    c = sh.c
    orig, refund = 33000, -11000
    remain = orig + refund
    assert remain == 22000

    sh.claim('title', 24, 548, 200, 22)
    sh.text(24, 555, '返金明細書（クレジットノート）', 'B', 13, maxw=200)
    sh.claim('merchant', 280, 530, 116, 38)
    sh.text(396, 557, 'テスト事務機オンライン', 'G', 9, align='r')
    sh.text(396, 546, 'テスト市架空町4-2-1', 'G', 6.5, align='r')
    sh.text(396, 536, '登録番号 T0000000000000', 'G', 6.5, align='r')
    c.line(24, 525, 396, 525)
    sh.text(24, 503, '架空工房　御中', 'M', 12)
    sh.text(24, 488, '注文番号 ORD-25-06031　／　返金番号 RF-0620-07', 'G', 7.5, maxw=260)
    sh.text(396, 503, '発行日 2025年6月20日', 'G', 8, align='r')
    sh.text(24, 462, '下記のとおり、ご注文の一部返品にともない返金を行いました。', 'G', 8.5, maxw=372)

    boxes = [
        (24, '元のご請求額', '2025/06/03 決済', f'￥{orig:,}', INK, HexColor('#e6e6e6'), 'ご注文時にカード決済済'),
        (153, '今回の返金額', '2025/06/20 処理', f'−￥{-refund:,}', RED, HexColor('#f6dcdc'), 'ご利用カードへ返金'),
        (282, '返金後のご負担額', '差引（参考）', f'￥{remain:,}', INK, HexColor('#e6e6e6'), '追加のご請求はありません'),
    ]
    sh.claim('boxes', 24, 330, 372, 110)
    for x, head, sub, amt, col, band, note in boxes:
        c.saveState()
        c.setFillColor(band)
        c.rect(x, 418, 114, 22, stroke=0, fill=1)
        c.restoreState()
        c.rect(x, 330, 114, 110)
        sh.text(x + 57, 425, head, 'B', 8.5, align='c', maxw=106)
        sh.text(x + 57, 400, sub, 'G', 7, align='c', maxw=106)
        sh.text(x + 57, 368, amt, 'B', 15, align='c', maxw=106, color=col)
        sh.text(x + 57, 342, note, 'G', 6.3, align='c', maxw=106)
    sh.text(145.5, 380, '＋', 'B', 11, align='c')
    sh.text(274.5, 380, '＝', 'B', 11, align='c')

    sh.claim('return-table', 24, 238, 372, 72)
    sh.text(24, 300, '返品明細', 'B', 9)
    c.line(24, 295, 396, 295)
    sh.text(26, 285, '品名', 'G', 7.5, color=GRAY)
    sh.text(260, 285, '数量', 'G', 7.5, color=GRAY, align='r')
    sh.text(330, 285, '税込金額', 'G', 7.5, color=GRAY, align='r')
    sh.text(396, 285, '内消費税', 'G', 7.5, color=GRAY, align='r')
    c.line(24, 280, 396, 280)
    sh.text(26, 265, 'レーザー複合機用トナー 黒（互換）', 'G', 8.5, maxw=210)
    sh.text(260, 265, '1', 'G', 8.5, align='r')
    sh.text(330, 265, '11,000', 'G', 8.5, align='r')
    sh.text(396, 265, '1,000', 'G', 8.5, align='r')
    c.line(24, 257, 396, 257)
    sh.text(24, 243, '返品理由：数量誤り（2本ご注文のうち1本を未開封にて返品）', 'G', 7.5, maxw=372)

    sh.claim('original', 24, 180, 372, 50)
    sh.text(24, 222, 'ご参考：元のご注文内容', 'B', 8)
    sh.text(30, 208, 'レーザー複合機用トナー 黒（互換）×2　22,000円', 'G', 7.5, maxw=360)
    sh.text(30, 196, 'コピー用紙 A4（500枚×5冊）×2箱　11,000円', 'G', 7.5, maxw=360)
    sh.text(30, 184, '合計 33,000円（10%対象・内消費税 3,000円）', 'G', 7.5, maxw=360)

    sh.text(24, 150, '返金方法：ご注文時のクレジットカードへ返金（カード会社の締め日により翌月以降の明細に反映）',
            'G', 7, maxw=372)
    sh.text(24, 138, '本書は返金の明細であり、新たなご請求書・領収書ではありません。', 'G', 7, maxw=372)
    c.line(24, 70, 396, 70)
    sh.text(W / 2, 58, 'テスト事務機オンライン　カスタマーセンター', 'G', 7, align='c', color=GRAY)
    sh.vtext(410, 300, '架空テスト', 'G', 6, color=GRAY)

    meta = {
        'title': '一部返金クレジットノート（元請求・返金・差引の三分割）',
        'layout_id': 'opus-07',
        'layout_description': 'A5縦。中央に横並び三つの枠(元のご請求額＋今回の返金額＝返金後のご負担額)を演算記号で連結。'
                              '下に返品明細表と元注文の参考一覧、返金方法の注記。架空テスト表記は右端の縦書き。',
        'document_kind': 'refund',
        'merchant': 'テスト事務機オンライン',
        'date': '2025-06-20',
        'date_printed': '2025/06/20',
        'total': refund,
        'acceptable_accounts': [],
        'expected_decision': 'block',
        'human_context': '6/3に事務用品33,000円をカード購入済み(別途計上済)。トナー1本を返品し11,000円がカードに返金される。'
                         '本書は返金明細であり支払証憑ではない。元の計上の修正(減額)として扱う必要がある。',
        'review_questions': ['元の33,000円の計上を11,000円減額修正するか', 'カード明細の返金反映月はいつか',
                             '22,000円を新たな支出として誤計上していないか'],
        'features': ['A5 portrait', 'three side-by-side amount boxes', 'negative refund amount',
                     'original vs refund vs remaining', 'reference original order list', 'vertical test marker'],
        'items': [{'name': 'レーザー複合機用トナー 黒（互換）返品', 'gross': refund, 'rate': '10'}],
    }
    return meta, sh.finish()


# ---------------------------------------------------------------- V098

def case_v098(path):
    W, H = 182 * mm, 257 * mm
    sh = Sheet(path, W, H, '領収書 (balance after deposit)')
    c = sh.c
    contract, deposit = 88000, 33000
    now = contract - deposit
    assert now == 55000 and now % 11 == 0
    now_tax = now // 11
    brown = HexColor('#6b4a2b')

    # counterfoil stub on the left
    sh.claim('stub', 18, 24, 110, 690)
    dashed(c, 140, 20, 140, H - 20, color=GRAY, on=2, off=2, lw=0.7)
    for yy in range(40, int(H) - 20, 28):
        c.saveState()
        c.setFillColor(HexColor('#cccccc'))
        c.circle(140, yy, 1.4, stroke=0, fill=1)
        c.restoreState()
    sh.text(73, 680, '控', 'M', 22, align='c', color=brown)
    sh.text(73, 655, 'No.1093', 'G', 8, align='c')
    c.line(26, 648, 120, 648)
    sh.text(26, 630, '架空町商事(株)様', 'G', 8, maxw=96)
    stub_rows = [('契約', f'{contract:,}'), ('前受', f'{deposit:,}'), ('今回', f'{now:,}'), ('残', '0')]
    y = 610
    for k, v in stub_rows:
        sh.text(26, y, k, 'G', 8)
        sh.text(120, y, v, 'G', 8, align='r')
        y -= 16
    sh.text(26, 540, 'R4.1.31 受領', 'G', 8)
    sh.text(26, 30, '架空テスト', 'G', 6, color=GRAY)

    X, XR = 160, 496
    sh.claim('head', X, 596, XR - X, 108)
    sh.text(X, 676, '領　収　書', 'M', 20, color=brown)
    sh.text(XR, 690, 'No.1093', 'G', 8, align='r')
    sh.text(XR, 676, '発行日　令和4年1月31日', 'G', 8.5, align='r')
    sh.text(X, 640, '架空町商事株式会社　様', 'M', 13)
    c.line(X, 636, 420, 636)
    sh.text(X, 614, '工事名　事務所空調機 分解洗浄（天井カセット形 3台）', 'G', 9, maxw=XR - X)
    sh.text(X, 600, '作業日　令和4年1月28日', 'G', 9)

    # payment ladder
    sh.claim('ladder', X, 458, XR - X, 122)
    cols = [X, 265, 355, 425, XR]
    c.saveState()
    c.setFillColor(HexColor('#efe7dc'))
    c.rect(X, 562, XR - X, 18, stroke=0, fill=1)
    c.setFillColor(HexColor('#fff4c2'))
    c.rect(X, 484, XR - X, 26, stroke=0, fill=1)
    c.restoreState()
    for s, x in (('区分', X + 4), ('日付', 269), ('金額（円）', 421), ('備考', 429)):
        sh.text(x, 567, s, 'B', 7.5, align='r' if s.startswith('金額') else 'l')
    ladder = [('ご契約金額（税込）', '—', f'{contract:,}', '契約額'),
              ('着手金（前受）', '令和4年1月10日', f'{deposit:,}', '受領済 No.1077'),
              ('今回ご入金（残金）', '令和4年1月31日', f'{now:,}', '本書で領収'),
              ('残金', '—', '0', '完済')]
    y = 562
    for k, d, a, note in ladder:
        y -= 26
        sh.text(X + 4, y + 9, k, 'G', 8.5, maxw=98)
        sh.text(269, y + 9, d, 'G', 8, maxw=84)
        sh.text(421, y + 9, a, 'B' if k.startswith('今回') else 'G', 9, align='r')
        sh.text(429, y + 9, note, 'G', 7, maxw=64)
        c.line(X, y, XR, y)
    c.rect(X, 458, XR - X, 122)
    for x in cols[1:-1]:
        c.line(x, 458, x, 580)

    sh.claim('big', 200, 370, 256, 60)
    c.saveState()
    c.setStrokeColor(brown)
    c.setLineWidth(2.0)
    c.rect(200, 370, 256, 60)
    c.setLineWidth(0.6)
    c.rect(204, 374, 248, 52)
    c.restoreState()
    sh.text(212, 414, '今回領収金額', 'G', 9, color=brown)
    sh.text(328, 384, f'￥{now:,}－', 'B', 24, align='c')

    sh.text(X, 350, f'内訳　今回領収分 {now:,}円（税抜 {now - now_tax:,}円・消費税 {now_tax:,}円 10%）',
            'G', 8, maxw=XR - X)
    sh.text(X, 337, 'ご契約金額 88,000円（税抜 80,000円・消費税 8,000円）', 'G', 7.5, maxw=XR - X)
    sh.text(X, 325, '着手金 33,000円（令和4年1月10日受領・別紙領収書 No.1077）を差し引いた残金です。',
            'G', 7.5, maxw=XR - X)
    sh.text(X, 300, '上記金額を正に領収いたしました。', 'M', 9)

    sh.claim('issuer', 300, 200, 196, 75)
    sh.text(300, 255, '架空町空調サービス', 'M', 11)
    sh.text(300, 241, 'テスト市架空町7-3-12', 'G', 7.5)
    sh.text(300, 229, '登録番号 T0000000000000', 'G', 7)
    sh.text(300, 218, 'TEL 000-0000-0000', 'G', 7)
    round_seal(sh, 470, 240, 18, '架空印', role='M')

    sh.text(X, 80, '本書は残金分の領収書です。着手金の領収書と合わせて保管してください。', 'G', 7.5, maxw=XR - X)

    meta = {
        'title': '着手金控除後の残金領収書（控え半券付き）',
        'layout_id': 'opus-08',
        'layout_description': 'JIS B5縦。左に点線ミシン目で区切った控え半券(契約・前受・今回・残の要約)、右に本券。'
                              '本券は契約額→着手金→今回入金→残金0の四段ラダー表、二重枠の今回領収金額、内訳文、右下に発行者と丸印。',
        'document_kind': 'voucher',
        'merchant': '架空町空調サービス',
        'date': '2022-01-31',
        'date_printed': '令和4年1月31日',
        'total': now,
        'acceptable_accounts': ['修繕費', '雑費'],
        'expected_decision': 'propose',
        'human_context': '事務所の空調機3台の分解洗浄。契約88,000円のうち着手金33,000円は1/10に支払済で別の領収書で計上済み。'
                         '本書は残金55,000円の支払分のみ。',
        'review_questions': ['着手金33,000円と二重計上になっていないか', '契約総額88,000円を誤って計上していないか',
                             '空調洗浄の勘定科目'],
        'features': ['JIS B5 portrait', 'perforated counterfoil stub', 'contract total vs paid now',
                     'deposit deduction ladder', 'Reiwa dates', 'work date vs issue date', 'round seal'],
        'items': [{'name': '事務所空調機 分解洗浄 残金', 'gross': now, 'rate': '10'}],
    }
    return meta, sh.finish()


# ---------------------------------------------------------------- V099

def case_v099(path):
    W, H = 80 * mm, 205 * mm
    sh = Sheet(path, W, H, 'スーパー レシート')
    c = sh.c
    x0, x1 = 12, W - 12
    cx = W / 2

    items = [('牛乳 1L', True, 238, ''), ('食パン 6枚切', True, 178, ''), ('バナナ', True, 198, ''),
             ('コピー用紙 A4 500枚', False, 548, ''), ('アルカリ乾電池 単3 4本', False, 398, ''),
             ('ゴミ袋 45L 30枚', False, 278, ''), ('缶コーヒー 微糖', True, 256, '2コ×単128')]
    t8 = sum(a for _, l, a, _ in items if l)
    t10 = sum(a for _, l, a, _ in items if not l)
    total = t8 + t10
    tax8 = t8 * 8 // 108
    tax10 = t10 * 10 // 110
    assert (t8, t10, total, tax8, tax10) == (870, 1224, 2094, 64, 111)
    paid, change = 3000, 3000 - total

    sh.claim('logo', x0, H - 42, x1 - x0, 28)
    c.saveState()
    c.setFillColor(INK)
    c.roundRect(x0, H - 42, x1 - x0, 28, 5, stroke=0, fill=1)
    c.restoreState()
    sh.text(cx, H - 33, 'スーパーテストマート', 'B', 14, align='c', color=white, maxw=x1 - x0 - 10)
    sh.text(cx, H - 54, '架空町店　TEL 000-0000-0000', 'G', 7.5, align='c')
    sh.text(cx, H - 64, '登録番号 T0000000000000', 'G', 7, align='c')
    sh.text(x0, H - 78, '2023年12月09日(土) 18:42', 'G', 8)
    sh.text(x1, H - 78, 'レジ3', 'G', 8, align='r')
    sh.text(x0, H - 89, '責No.112　取引0841', 'G', 7)
    sh.text(cx, H - 105, '領　収　証', 'B', 11, align='c')

    y = H - 122
    for name, light, amt, sub in items:
        sh.text(x0, y, name, 'G', 8.5, maxw=x1 - x0 - 60)
        sh.text(x1 - 10, y, f'￥{amt:,}', 'G', 8.5, align='r')
        if light:
            sh.text(x1, y, '※', 'G', 7, align='r')
        y -= 13
        if sub:
            sh.text(x0 + 10, y + 1, sub, 'G', 7.5)
            y -= 12
    c.setLineWidth(0.6)
    c.line(x0, y + 7, x1, y + 7)
    y -= 4
    sh.text(x0, y, '小計　8点', 'G', 8.5)
    sh.text(x1, y, f'￥{total:,}', 'G', 8.5, align='r')
    y -= 26
    sh.claim('total', x0, y - 4, x1 - x0, 25)
    sh.text(x0, y, '合計', 'B', 15)
    sh.text(x1, y, f'￥{total:,}', 'B', 21, align='r')
    y -= 14
    sh.claim('taxbox', x0, y - 38, x1 - x0, 38)
    c.rect(x0, y - 38, x1 - x0, 38)
    sh.text(x0 + 5, y - 11, f'（ 8%対象　￥{t8:,}　内税　￥{tax8:,}）', 'G', 7.5, maxw=x1 - x0 - 10)
    sh.text(x0 + 5, y - 22, f'（10%対象　￥{t10:,}　内税　￥{tax10:,}）', 'G', 7.5, maxw=x1 - x0 - 10)
    sh.text(x0 + 5, y - 33, '※印は軽減税率（8%）適用商品です', 'G', 6.5, maxw=x1 - x0 - 10)
    y -= 54
    sh.text(x0, y, 'お預り　現金', 'G', 9)
    sh.text(x1, y, f'￥{paid:,}', 'G', 9, align='r')
    y -= 13
    sh.text(x0, y, 'お釣り', 'G', 9)
    sh.text(x1, y, f'￥{change:,}', 'G', 9, align='r')
    y -= 17
    sh.text(x0, y, '今回付与ポイント　20P', 'G', 7)
    y -= 10
    sh.text(x0, y, '累計ポイント　1,345P（金額ではありません）', 'G', 7, maxw=x1 - x0)
    y -= 18
    sh.text(cx, y, 'ご来店ありがとうございました', 'G', 7, align='c')
    y -= 50
    code = '2312090841003'
    bx = cx - 62
    c.saveState()
    c.setFillColor(black)
    for ch in code:
        d = int(ch)
        for k in range(2):
            bw = 0.8 * (1 + (d + k) % 3)
            c.rect(bx, y, bw, 26, stroke=0, fill=1)
            bx += bw + 0.8 * (1 + (d * 7 + k) % 3)
    c.restoreState()
    if bx > x1:
        sh.issues.append('barcode wider than roll')
    sh.text(cx, y - 10, code, 'G', 7, align='c')
    sh.text(x0, 14, '架空テスト', 'G', 6, color=GRAY)

    meta = {
        'title': 'スーパー80mmレシート（8%/10%混在・私用品混在）',
        'layout_id': 'opus-09',
        'layout_description': '80mm幅ロール。黒地白抜きの店舗ロゴ帯、品目右端の※で軽減税率表示、小計の後に倍角の合計、'
                              '罫線枠の税率別内税表、現金預り/釣り、ポイント表示、末尾に装飾バーコード。',
        'document_kind': 'thermal_receipt',
        'merchant': 'スーパーテストマート 架空町店',
        'date': '2023-12-09',
        'date_printed': '2023年12月09日',
        'total': total,
        'acceptable_accounts': [],
        'expected_decision': 'hold',
        'human_context': '事務所用のコピー用紙・乾電池・ゴミ袋と、自宅用の牛乳・パン・バナナ・缶コーヒーを同時に現金購入。'
                         '業務分(10%対象1,224円)と私用分(8%対象870円)の切り分けが必要。',
        'review_questions': ['私用の食品870円を除外し業務分のみ計上するか', '業務分1,224円の科目(消耗品費など)',
                             'ポイントを金額と誤認していないか'],
        'features': ['80mm thermal', 'two tax rates with marker', 'tax-inclusive per-rate table', 'cash tender and change',
                     'loyalty points (non-monetary)', 'mixed business/private basket', 'inverted logo band', 'barcode'],
        'items': [{'name': n, 'gross': a, 'rate': '8' if l else '10'} for n, l, a, _ in items],
    }
    return meta, sh.finish()


# ---------------------------------------------------------------- V100

def case_v100(path):
    W, H = 257 * mm, 182 * mm
    sh = Sheet(path, W, H, 'セミナー受講料 領収書')
    c = sh.c
    net, tax = 15000, 1500
    total = net + tax

    c.saveState()
    c.setStrokeColor(HexColor('#333333'))
    c.setLineWidth(0.8)
    c.rect(14, 14, W - 28, H - 28)
    c.line(650, 30, 650, H - 30)
    c.restoreState()

    # recipient as a vertical column on the right
    sh.claim('recipient', 672, 248, 36, 224)
    last = sh.vtext(690, 470, '株式会社架空設計', 'K', 20, step=22)
    sh.vtext(690, last - 12, '御中', 'K', 14)
    sh.text(689, 24, '架空テスト', 'G', 6, align='c', color=GRAY)

    sh.claim('title', 40, 440, 590, 40)
    sh.text(40, 455, '受講料領収書', 'K', 22)
    sh.text(630, 460, '発行日　令和7年5月17日', 'M', 10, align='r')
    sh.text(630, 444, 'No. S-0517-022', 'G', 7, align='r')

    rows = [('講座名', '中小企業のための電子帳簿実務講座（基礎編）'),
            ('開催日', '令和7年5月17日（土）13:30〜16:30'),
            ('会場', 'テスト市架空町 市民ホール 第2研修室'),
            ('受講者', '架空 花子 様（1名）'),
            ('受講料', f'{net:,}円　＋　消費税（10%）{tax:,}円'),
            ('お支払方法', '開催当日 受付にて現金')]
    top, rh = 420, 26
    sh.claim('table', 40, top - 6 * rh, 590, 6 * rh)
    c.saveState()
    c.setFillColor(HexColor('#f0ede4'))
    c.rect(40, top - 6 * rh, 100, 6 * rh, stroke=0, fill=1)
    c.restoreState()
    c.rect(40, top - 6 * rh, 590, 6 * rh)
    c.line(140, top - 6 * rh, 140, top)
    for i, (k, v) in enumerate(rows):
        yb = top - (i + 1) * rh
        if i < 5:
            c.line(40, yb, 630, yb)
        sh.text(50, yb + 9, k, 'M', 9.5, maxw=86)
        sh.text(150, yb + 9, v, 'M', 10.5, maxw=470)
    sh.text(40, 240, '上記の金額を受講料として正に領収いたしました。', 'M', 9)

    # total at bottom-left
    sh.claim('total', 40, 60, 260, 90)
    c.saveState()
    c.setLineWidth(1.5)
    c.rect(40, 60, 260, 90)
    c.restoreState()
    sh.text(50, 132, '領収金額', 'M', 9)
    sh.text(50, 88, f'￥{total:,}－', 'B', 28, maxw=240)
    sh.text(50, 70, f'（税込　うち消費税等 {tax:,}円）', 'G', 8)

    sh.claim('issuer', 330, 60, 300, 100)
    sh.text(330, 140, '一般社団法人テスト経営研究会', 'M', 11, maxw=190)
    sh.text(330, 124, 'テスト市架空町6-1-4', 'G', 8)
    sh.text(330, 110, '登録番号 T0000000000000', 'G', 7.5)
    sh.text(330, 96, '事務局　電話 000-0000-0000', 'G', 7.5)
    round_seal(sh, 560, 120, 26, '研究会', role='K')

    meta = {
        'title': 'セミナー受講料領収書（宛名右縦書き・金額左下）',
        'layout_id': 'opus-10',
        'layout_description': 'JIS B5横。右端の縦罫で区切った縦書き宛名列、左上に楷書体の表題、中央に講座名・開催日・会場などの'
                              '見出し付き罫線表、金額は左下の太枠、発行者と丸印は下段中央右。',
        'document_kind': 'voucher',
        'merchant': '一般社団法人テスト経営研究会',
        'date': '2025-05-17',
        'date_printed': '令和7年5月17日',
        'total': total,
        'acceptable_accounts': ['研修費', '教育研修費'],
        'expected_decision': 'propose',
        'human_context': '従業員1名が業務に関連する電子帳簿の実務セミナーを受講し、当日会場で現金払い。業務研修。',
        'review_questions': ['受講者が従業員本人であり業務目的か', '現金支払の出金記録があるか'],
        'features': ['B5 landscape', 'vertical recipient on right', 'total box bottom-left', 'brush kaisho font',
                     'exact Reiwa era date with weekday', 'labelled detail table', 'round seal'],
        'items': [{'name': 'セミナー受講料（電子帳簿実務講座 基礎編）', 'gross': total, 'rate': '10'}],
    }
    return meta, sh.finish()


# ---------------------------------------------------------------- main

CASES = [
    ('V091', case_v091), ('V092', case_v092), ('V093', case_v093), ('V094', case_v094),
    ('V095', case_v095), ('V096', case_v096), ('V097', case_v097), ('V098', case_v098),
    ('V099', case_v099), ('V100', case_v100),
]

KEY_ORDER = ['case_id', 'file', 'title', 'layout_id', 'layout_description', 'document_kind', 'merchant',
             'date', 'date_printed', 'total', 'acceptable_accounts', 'expected_decision', 'human_context',
             'review_questions', 'features', 'items', 'author', 'author_model']


def main():
    ap = argparse.ArgumentParser(description='opus author receipts V091-V100 (fictional)')
    ap.add_argument('--output', required=True, help='output directory (created if missing)')
    ap.add_argument('--strict', action='store_true', help='exit non-zero on layout warnings')
    args = ap.parse_args()

    out = os.path.abspath(args.output)
    ind = os.path.join(out, 'individual')
    os.makedirs(ind, exist_ok=True)
    register_fonts()

    manifest = []
    warnings = {}
    seen_layouts = set()
    for cid, fn in CASES:
        pdf_path = os.path.join(ind, f'{cid}.pdf')
        meta, issues = fn(pdf_path)
        if issues:
            warnings[cid] = issues
        assert meta['layout_id'] not in seen_layouts, meta['layout_id']
        seen_layouts.add(meta['layout_id'])
        if meta['total'] is not None:
            s = sum(it['gross'] for it in meta['items'])
            assert s == meta['total'], f'{cid}: items {s} != total {meta["total"]}'
        if meta['document_kind'] == 'refund':
            assert meta['total'] < 0 and meta['expected_decision'] == 'block'
        if not meta['date']:
            assert meta['date_printed'] == ''
        meta['case_id'] = cid
        meta['file'] = f'individual/{cid}.pdf'
        meta['author'] = AUTHOR
        meta['author_model'] = AUTHOR_MODEL
        manifest.append({k: meta[k] for k in KEY_ORDER})

    try:
        from pypdf import PdfReader
        for item in manifest:
            n = len(PdfReader(os.path.join(out, item['file'])).pages)
            if n != 1:
                raise SystemExit(f'{item["case_id"]}: expected 1 page, got {n}')
    except ImportError:
        print('[check] pypdf not available; page-count check skipped', file=sys.stderr)

    with open(os.path.join(out, 'manifest-fragment.json'), 'w', encoding='utf-8') as f:
        json.dump(manifest, f, ensure_ascii=False, indent=2)

    for cid, issues in warnings.items():
        for msg in issues:
            print(f'[layout] {cid}: {msg}', file=sys.stderr)
    print(f'wrote {len(manifest)} PDFs and manifest-fragment.json to {out}')
    if warnings and args.strict:
        sys.exit(2)


if __name__ == '__main__':
    main()
