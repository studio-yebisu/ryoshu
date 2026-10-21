#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Kimi K3 author: 20 independently drawn retail receipt layouts, V001-V020.

Each case has its own procedural drawing routine (different page proportion,
ruling, columns, total placement, tender block, era/date style, density).
Outputs individual/Vxxx.pdf (one page each) and manifest-fragment.json.
Only reportlab / stdlib are used. No network, no subprocess.
"""
import argparse
import json
import math
import os
import random

from reportlab.pdfgen import canvas
from reportlab.pdfbase import pdfmetrics
from reportlab.pdfbase.ttfonts import TTFont
from reportlab.lib.colors import Color, black, white

MM = 72.0 / 25.4
WF = "C:/Windows/Fonts"
F = {}

GRAY_BG = Color(0.88, 0.88, 0.88)
GRAY_TX = Color(0.35, 0.35, 0.35)
RED = Color(0.62, 0.10, 0.10)
DARK = Color(0.12, 0.12, 0.12)


# ---------------------------------------------------------------- fonts ----
def register_fonts():
    plan = {
        "GOTH": [("meiryo.ttc", 0), ("msgothic.ttc", 0)],
        "GOTH-B": [("meiryo.ttc", 1), ("YuGothB.ttc", 0),
                   ("msgothic.ttc", 1), ("msgothic.ttc", 0)],
        "MINCH": [("YuMincho.ttc", 0), ("msmincho.ttc", 0)],
        "PMIN": [("msmincho.ttc", 1), ("msmincho.ttc", 0)],
        "YG-B": [("YuGothB.ttc", 0), ("meiryo.ttc", 1),
                 ("msgothic.ttc", 1), ("msgothic.ttc", 0)],
        "PGOTH": [("msgothic.ttc", 1), ("msgothic.ttc", 0)],
    }
    F.clear()
    for alias, cands in plan.items():
        ok = False
        for fname, idx in cands:
            p = os.path.join(WF, fname)
            if not os.path.exists(p):
                continue
            try:
                pdfmetrics.registerFont(TTFont(alias, p, subfontIndex=idx))
                F[alias] = alias
                ok = True
                break
            except Exception:
                continue
        if not ok:
            F[alias] = None
    for alias in plan:
        if not F[alias]:
            F[alias] = F.get("GOTH") or "Helvetica"
    if F["GOTH"] == "Helvetica":
        raise RuntimeError("no usable Japanese font found in C:/Windows/Fonts")


# -------------------------------------------------------------- helpers ----
def P(wmm, hmm):
    return (wmm * MM, hmm * MM)


def Wd(s, font, size):
    return pdfmetrics.stringWidth(s, F[font], size)


def fit_size(s, font, size, maxw):
    while size > 4.0 and Wd(s, font, size) > maxw:
        size -= 0.5
    return size


def T(c, x, y, s, font="GOTH", size=9, align="l", color=None, maxw=None):
    """Draw one baseline of text; shrink to fit when maxw is given."""
    if maxw is not None:
        size = fit_size(s, font, size, maxw)
    c.setFont(F[font], size)
    c.setFillColor(color if color is not None else black)
    if align == "r":
        c.drawRightString(x, y, s)
    elif align == "c":
        c.drawCentredString(x, y, s)
    else:
        c.drawString(x, y, s)
    if color is not None:
        c.setFillColor(black)
    return Wd(s, font, size)


def wrap_lines(s, font, size, maxw):
    """Character based wrap (Japanese has no spaces)."""
    out, cur = [], ""
    for ch in s:
        if ch == "\n":
            out.append(cur)
            cur = ""
            continue
        if cur and Wd(cur + ch, font, size) > maxw:
            out.append(cur)
            cur = ch
        else:
            cur += ch
    if cur or not out:
        out.append(cur)
    return out


def para(c, x, y, s, font, size, maxw, lead=None):
    lead = lead or size * 1.35
    for line in wrap_lines(s, font, size, maxw):
        T(c, x, y, line, font, size)
        y -= lead
    return y


def hline(c, x1, y, x2, w=0.6, dash=None, color=None):
    c.saveState()
    c.setLineWidth(w)
    if dash:
        c.setDash(dash, 0)
    if color:
        c.setStrokeColor(color)
    c.line(x1, y, x2, y)
    c.restoreState()


def vline(c, x, y1, y2, w=0.6, dash=None, color=None):
    c.saveState()
    c.setLineWidth(w)
    if dash:
        c.setDash(dash, 0)
    if color:
        c.setStrokeColor(color)
    c.line(x, y1, x, y2)
    c.restoreState()


def box(c, x, y, w, h, r=0.0, dash=None, lw=0.8, fill=None, color=None):
    c.saveState()
    c.setLineWidth(lw)
    if dash:
        c.setDash(dash, 0)
    if color:
        c.setStrokeColor(color)
    if fill is not None:
        c.setFillColor(fill)
    if r:
        c.roundRect(x, y, w, h, r, stroke=1, fill=1 if fill else 0)
    else:
        c.rect(x, y, w, h, stroke=1, fill=1 if fill else 0)
    c.restoreState()


def dbox(c, x, y, w, h, lw=0.9):
    """Double bordered box."""
    box(c, x, y, w, h, lw=lw)
    box(c, x + 1.6, y + 1.6, w - 3.2, h - 3.2, lw=0.4)


def leader(c, x1, x2, y):
    hline(c, x1, y, x2, w=0.5, dash=[0.8, 2.2])


def vtext(c, x, y_top, s, font, size, lead=None):
    """Vertical Japanese text, one glyph per line, column goes downward.
    '\\n' starts a new column to the left."""
    lead = lead or size * 1.12
    y, cx = y_top, x
    for ch in s:
        if ch == "\n":
            cx -= lead
            y = y_top
            continue
        c.setFont(F[font], size)
        c.setFillColor(black)
        c.drawCentredString(cx, y - size, ch)
        y -= lead
    return y


def yen(n, symbol="¥"):
    if n < 0:
        return "-%s%s" % (symbol, format(-n, ","))
    return "%s%s" % (symbol, format(n, ","))


def num(n):
    return format(n, ",")


def tax_in(amount, rate):
    """Included consumption tax, floor (zaimu shohizei naizei)."""
    return amount * rate // (100 + rate)


def barcode(c, x, y, w, h, seed):
    rnd = random.Random(seed)
    cx = x
    while cx < x + w - 0.8:
        bw = rnd.choice([0.7, 1.1, 1.6, 2.3])
        if bw > x + w - cx:
            bw = x + w - cx
        if rnd.random() < 0.62:
            c.rect(cx, y, bw * 0.55, h, stroke=0, fill=1)
        cx += bw


def faux_qr(c, x, y, cell, n, seed):
    rnd = random.Random(seed)
    box(c, x - cell, y - cell, (n + 2) * cell, (n + 2) * cell, lw=0.6)
    for i in range(n):
        for j in range(n):
            if rnd.random() < 0.42:
                c.rect(x + i * cell + 0.7, y + j * cell + 0.7,
                       cell - 1.4, cell - 1.4, stroke=0, fill=1)
    for fx, fy in ((0, n - 3), (n - 3, n - 3), (0, 0)):
        box(c, x + fx * cell, y + fy * cell, 3 * cell, 3 * cell, lw=0.9)
        c.rect(x + fx * cell + cell, y + fy * cell + cell,
               cell, cell, stroke=0, fill=1)


def stamp_circle(c, cx, cy, r, s, font="MINCH", size=8):
    c.saveState()
    c.setStrokeColor(RED)
    c.setFillColor(RED)
    c.setLineWidth(1.0)
    c.circle(cx, cy, r, stroke=1, fill=0)
    c.setFont(F[font], size)
    if len(s) <= 2:
        c.drawCentredString(cx, cy - size * 0.4, s)
    else:
        half = (len(s) + 1) // 2
        c.drawCentredString(cx, cy + 1.2, s[:half])
        c.drawCentredString(cx, cy - size - 0.2, s[half:])
    c.restoreState()


def stamp_square(c, x, y, side, s, font="MINCH", size=9):
    c.saveState()
    c.setStrokeColor(RED)
    c.setFillColor(RED)
    c.setLineWidth(1.0)
    c.rect(x, y, side, side, stroke=1, fill=0)
    c.setFont(F[font], size)
    c.drawCentredString(x + side / 2.0, y + side / 2.0 - size * 0.38, s)
    c.restoreState()


def wavy(c, x1, x2, y, amp=1.4, period=7.0):
    p = c.beginPath()
    p.moveTo(x1, y)
    steps = max(2, int((x2 - x1) / 2.0))
    for i in range(1, steps + 1):
        xx = x1 + (x2 - x1) * i / steps
        yy = y + math.sin((xx - x1) / period * math.pi) * amp
        p.lineTo(xx, yy)
    c.setLineWidth(0.6)
    c.drawPath(p, stroke=1, fill=0)


def outline_text(c, x, y, s, font, size, angle, lw=1.1):
    c.saveState()
    c.translate(x, y)
    c.rotate(angle)
    t = c.beginText(0, 0)
    t.setFont(F[font], size)
    t.setTextRenderMode(1)
    t.textOut(s)
    c.setLineWidth(lw)
    c.drawText(t)
    c.restoreState()


def guard(x, y, w, h, PW, PH, tag):
    """Collision guard for major blocks: keep them inside the page."""
    if x < -0.5 or y < -0.5 or x + w > PW + 0.5 or y + h > PH + 0.5:
        raise RuntimeError("layout overflow: %s" % tag)


def side_mark(c, PW, PH, s="架空テスト"):
    """Vertical unobtrusive fiction mark along an edge."""
    c.saveState()
    c.translate(5.5, PH / 2.0)
    c.rotate(90)
    T(c, 0, 0, s, "GOTH", 5.5, align="c", color=GRAY_TX)
    c.restoreState()


# ================================================================ V001 =====
def draw_v001(c, size):
    """58mm thermal convenience receipt: centered logo, stacked rules,
    rate summary, change, barcode footer."""
    PW, PH = size
    M = 4 * MM
    L, R = M, PW - M
    c.setTitle("V001")
    # logo circle
    c.setLineWidth(0.9)
    c.circle(PW / 2.0, PH - 24, 10.5)
    T(c, PW / 2.0, PH - 27.5, "み", "MINCH", 10, align="c")
    T(c, PW / 2.0, PH - 46, "コンビニ みちのく", "YG-B", 11.5, align="c")
    T(c, PW / 2.0, PH - 58, "テスト市架空町1-2-3 テストビル前", "GOTH", 6.8,
      align="c")
    T(c, PW / 2.0, PH - 67, "TEL 000-0000-0000", "GOTH", 6.8, align="c")
    hline(c, L, PH - 74, R, w=0.7, dash=[2, 1.6])
    y = PH - 86
    T(c, L, y, "2023/11/2(木) 19:41", "GOTH", 7.2)
    y -= 10.5
    T(c, L, y, "レジ #3", "GOTH", 7.2)
    T(c, R, y, "No.0429-113", "GOTH", 7.2, align="r")
    y -= 6
    hline(c, L, y, R, w=0.6)
    y -= 12
    items = [("単3乾電池 4本パック", 580),
             ("A4コピー用紙 500枚入", 780),
             ("クリアファイル A4 2枚組", 400)]
    for nm, pr in items:
        T(c, L, y, nm, "GOTH", 8.2, maxw=R - L - 42)
        T(c, R, y, num(pr), "GOTH", 8.2, align="r")
        y -= 12.5
    y += 2
    hline(c, L, y, R, w=0.6)
    y -= 12
    T(c, L, y, "10%対象 計", "GOTH", 7.6)
    T(c, R, y, yen(1760), "GOTH", 7.6, align="r")
    y -= 10.5
    T(c, L, y, " (内消費税等)", "GOTH", 6.8)
    T(c, R, y, num(tax_in(1760, 10)), "GOTH", 6.8, align="r")
    y -= 8
    # total box
    guard(L, y - 18, R - L, 18, PW, PH, "v001 total")
    box(c, L, y - 18, R - L, 18, r=2, lw=1.0)
    T(c, L + 4, y - 13, "合 計", "GOTH-B", 9.5)
    T(c, R - 4, y - 14, yen(1760, "¥"), "YG-B", 12.5, align="r")
    y -= 28
    T(c, L, y, "お預かり", "GOTH", 7.8)
    T(c, R, y, yen(2000), "GOTH", 7.8, align="r")
    y -= 11
    T(c, L, y, "おつり", "GOTH", 7.8)
    T(c, R, y, yen(240), "GOTH", 7.8, align="r")
    y -= 7
    hline(c, L, y, R, w=0.7, dash=[2, 1.6])
    barcode(c, PW / 2 - 48, y - 32, 96, 20, seed=101)
    T(c, PW / 2, y - 40, "4 2 9 1 1 3", "GOTH", 6.5, align="c")
    T(c, PW / 2, y - 54, "またのご利用をお待ちしております", "GOTH", 6.4,
      align="c", maxw=R - L)
    T(c, PW / 2, 10, "※ 架空テスト用データ", "GOTH", 5.8, align="c",
      color=GRAY_TX)


# ================================================================ V002 =====
def draw_v002(c, size):
    """80mm extra-long supermarket basket: banner header, starred 8% food
    group plus sundry group, two-rate summary, big total band, survey box."""
    PW, PH = size
    M = 5 * MM
    L, R = M, PW - M
    c.setTitle("V002")
    c.setFillColor(black)
    c.rect(0, PH - 24, PW, 24, stroke=0, fill=1)
    T(c, PW / 2, PH - 16.5, "スーパー いちばん星 テスト市店", "YG-B", 9.8,
      align="c", color=white)
    T(c, PW / 2, PH - 36, "〒000-0000 テスト市架空町2-5-8", "GOTH", 7.0,
      align="c")
    y = PH - 50
    T(c, L, y, "R6.3.16(土) 10:12", "GOTH", 7.6)
    T(c, R, y, "POS-12 係 1032", "GOTH", 7.6, align="r")
    y -= 11
    T(c, L, y, "会員番号 ****-2210", "GOTH", 7.0)
    hline(c, L, y - 4, R, w=1.2)
    y -= 15
    T(c, L, y, "◆ 食料品 (★印は軽減対象)", "GOTH-B", 7.4)
    y -= 12
    food = [("牛乳 1L", "×2", 430), ("食パン 6枚切", "", 178),
            ("りんご バラ", "", 298), ("たまご 10個入", "", 248),
            ("味噌 こうじ 750g", "", 345), ("絹ごし豆腐", "2丁", 176),
            ("キャベツ 1玉", "", 158), ("鶏むね肉", "", 512),
            ("玄米 2kg", "", 1580), ("納豆 3P", "", 128),
            ("バナナ", "", 198)]
    for nm, q, pr in food:
        T(c, L, y, "%s%s ★" % (nm, (" " + q) if q else ""), "GOTH", 7.8,
          maxw=R - L - 44)
        T(c, R, y, num(pr), "GOTH", 7.8, align="r")
        y -= 11.5
    y -= 2
    T(c, L, y, "◆ 日用雑貨・医薬部外品", "GOTH-B", 7.4)
    y -= 12
    sundry = [("台所用洗剤 本体", 328), ("同 詰替用", 264),
              ("キッチンスポンジ 5P", 214), ("食品用ラップ", 305),
              ("トイレット紙 12R", 668), ("柔軟剤 詰替", 549),
              ("除菌ウェットシート", 385)]
    for nm, pr in sundry:
        T(c, L, y, nm, "GOTH", 7.8, maxw=R - L - 44)
        T(c, R, y, num(pr), "GOTH", 7.8, align="r")
        y -= 11.5
    hline(c, L, y, R, w=0.7)
    y -= 12
    T(c, L, y, "8%対象  計 4,251 (内消費税 314)", "GOTH", 7.2)
    y -= 10.5
    T(c, L, y, "10%対象 計 2,713 (内消費税 246)", "GOTH", 7.2)
    y -= 9
    guard(L, y - 26, R - L, 26, PW, PH, "v002 total")
    c.setFillColor(black)
    c.roundRect(L, y - 26, R - L, 26, 4, stroke=0, fill=1)
    T(c, PW / 2, y - 18, "合計  ¥6,964  (税込)", "YG-B", 13.5, align="c",
      color=white)
    c.setFillColor(black)
    y -= 38
    T(c, L, y, "お預り", "GOTH", 8.4)
    T(c, R, y, yen(7000), "GOTH", 8.4, align="r")
    y -= 12
    T(c, L, y, "お釣り", "GOTH", 8.4)
    T(c, R, y, yen(36), "GOTH", 8.4, align="r")
    y -= 12
    hline(c, L, y, R, w=0.6, dash=[2, 1.8])
    y -= 12
    y = para(c, L, y,
             "レシートはお買い上げ日を含め7日間有効です。返品交換の際は"
             "必ずご持参ください。本日のチラシ特売品はマイル付与の対象外"
             "となります。", "GOTH", 6.4, R - L)
    y -= 8
    box(c, L, y - 44, R - L, 40, dash=[2, 2], lw=0.7)
    para(c, L + 4, y - 14,
         "アンケートにご協力いただくと、次回使える駐車場サービス券を"
         "発行します(本券サンプル)。", "GOTH", 6.2, R - L - 8)
    T(c, L, 14, "No. 31612-0481", "GOTH", 6.2)
    side_mark(c, PW, PH)


# ================================================================ V003 =====
def draw_v003(c, size):
    """A6 bookshop: left vertical store strip, ledger ruling (horizontal
    lines only), code/name/qty/amount columns, stacked total, policy box."""
    PW, PH = size
    strip = 16 * MM
    x0 = strip + 4 * MM
    R = PW - 4.5 * MM
    c.setTitle("V003")
    c.setFillColor(GRAY_BG)
    c.rect(0, 0, strip, PH, stroke=0, fill=1)
    c.setFillColor(black)
    vtext(c, strip / 2 + 2, PH - 18, "本と文具\nことば舎", "MINCH", 10.5,
          lead=13)
    vtext(c, strip / 2 - 8, 70, "架空テスト", "GOTH", 5.5, lead=7)
    T(c, R, PH - 24, "御 買 上 票", "MINCH", 13.5, align="r")
    T(c, x0, PH - 24, "領収書 No. B-1029", "GOTH", 7.6)
    T(c, x0, PH - 36, "令和3年6月30日(水)", "GOTH", 8.2)
    T(c, R, PH - 36, "ご利用: 現金", "GOTH", 7.6, align="r")
    T(c, x0, PH - 47, "お客様: テスト事務所 様", "GOTH", 8.2)
    cols = [x0, x0 + 40, x0 + 168, x0 + 196, R]
    top = PH - 58
    hline(c, x0, top, R, w=0.9)
    T(c, cols[0] + 2, top - 11, "品番", "GOTH", 7.2)
    T(c, cols[1] + 2, top - 11, "書名・品名", "GOTH", 7.2)
    T(c, cols[3] - 14, top - 11, "数量", "GOTH", 7.2)
    T(c, cols[4] - 2, top - 11, "金額", "GOTH", 7.2, align="r")
    hline(c, x0, top - 15, R, w=0.5)
    rows = [("SB-0042", "実践データ整備術 第2版", 1, 2420),
            ("SB-0117", "店舗数字の読み方 入門", 1, 1870)]
    y = top - 27
    for code, nm, q, pr in rows:
        T(c, cols[0] + 2, y, code, "PGOTH", 6.8)
        T(c, cols[1] + 2, y, nm, "MINCH", 8.4, maxw=cols[2] - cols[1] - 6)
        T(c, cols[3] - 8, y, num(q), "GOTH", 8, align="r")
        T(c, cols[4] - 2, y, num(pr), "GOTH", 8.4, align="r")
        y -= 13
        hline(c, x0, y + 4, R, w=0.3)
    y -= 10
    tx = R - 130
    T(c, tx, y, "小 計", "GOTH", 8.4)
    T(c, R, y, yen(4290), "GOTH", 8.4, align="r")
    y -= 12
    T(c, tx, y, "内消費税", "GOTH", 7.2)
    T(c, R, y, num(tax_in(4290, 10)), "GOTH", 7.2, align="r")
    y -= 24
    guard(tx - 6, y - 6, R - tx + 6, 22, PW, PH, "v003 total")
    dbox(c, tx - 6, y - 6, R - tx + 6, 22)
    T(c, tx, y, "総合計", "GOTH-B", 9)
    T(c, R - 5, y - 1, yen(4290, "¥"), "YG-B", 12, align="r")
    # policy box at bottom
    box(c, x0, 30, R - x0, 52, dash=[2, 1.8], lw=0.7)
    T(c, x0 + 4, 70, "ご返品・ご交換について", "GOTH-B", 7)
    para(c, x0 + 4, 60,
         "書籍は未開封・未読の場合のみ、お買上日より7日以内で返品を承ります。"
         "汚損・書き込みのある商品はご容赦ください。", "GOTH", 6.3,
         R - x0 - 8)
    T(c, x0, 16, "テスト市架空町4-9 / TEL 000-000-0000", "GOTH", 6.2)


# ================================================================ V004 =====
def draw_v004(c, size):
    """A5 landscape electronics checkout: black banner, gridded spec table,
    warranty/QR guidance panel on the right, full-width price band."""
    PW, PH = size
    c.setTitle("V004")
    c.setFillColor(black)
    c.rect(0, PH - 34, PW, 34, stroke=0, fill=1)
    T(c, 14, PH - 22, "電器専門店 デンキノハコ", "YG-B", 13, color=white)
    T(c, PW - 14, PH - 16, "御 領 収 書  No.EX-2207", "GOTH", 8.6, align="r",
      color=white)
    T(c, PW - 14, PH - 28, "架空テスト", "GOTH", 6, align="r", color=white)
    c.setFillColor(black)
    T(c, 14, PH - 46, "テスト市架空町3-14-2", "GOTH", 7.4)
    T(c, 14, PH - 57, "発行 2022-12-14 16:55 / レジ #18 / 販売員: おおの",
      "GOTH", 7.4)
    # left grid table
    x0, x1 = 14, 330
    cols = [x0, x0 + 128, x0 + 220, x0 + 258, x1]
    top = PH - 70
    c.setFillColor(GRAY_BG)
    c.rect(x0, top - 16, x1 - x0, 16, stroke=0, fill=1)
    c.setFillColor(black)
    heads = ["品名", "品番・仕様", "数量", "金額(税込)"]
    for i, htxt in enumerate(heads):
        T(c, cols[i] + 3, top - 12, htxt, "GOTH-B", 7.4,
          align="r" if i == 3 else "l")
    rows = [("27型業務用モニタ", "K27-T01 WQHD", 1, 89800),
            ("HDMIケーブル 2m", "HD-20A ver2.0", 1, 1480)]
    y = top - 16
    for nm, spec, q, pr in rows:
        y -= 15
        T(c, cols[0] + 3, y + 4, nm, "GOTH", 8, maxw=cols[1] - cols[0] - 8)
        T(c, cols[1] + 3, y + 4, spec, "PGOTH", 7,
          maxw=cols[2] - cols[1] - 8)
        T(c, cols[2] + 3, y + 4, num(q), "GOTH", 8)
        T(c, cols[3] - 3, y + 4, num(pr), "GOTH", 8, align="r")
    grid_bot = y - 4
    for gx in cols:
        vline(c, gx, grid_bot, top, w=0.5)
    for gy in (top, top - 16, grid_bot):
        hline(c, x0, gy, x1, w=0.5)
    y = grid_bot - 14
    T(c, x0 + 118, y, "小 計", "GOTH", 8.2)
    T(c, x1 - 3, y, yen(91280), "GOTH", 8.2, align="r")
    y -= 12
    T(c, x0 + 118, y, "内消費税(10%)", "GOTH", 7)
    T(c, x1 - 3, y, num(tax_in(91280, 10)), "GOTH", 7, align="r")
    # full-width price band
    guard(14, 58, PW - 28, 34, PW, PH, "v004 band")
    c.setFillColor(black)
    c.rect(14, 58, PW - 28, 34, stroke=0, fill=1)
    T(c, 24, 71, "ご購入金額(税込)", "GOTH", 8.8, color=white)
    T(c, PW - 24, 66, "¥91,280-", "YG-B", 16, align="r", color=white)
    c.setFillColor(black)
    T(c, 14, 46, "お支払: 現金 ¥92,000   お釣り ¥720", "GOTH", 7.6)
    T(c, 14, 36, "お買い上げ誠にありがとうございました", "GOTH", 6.6)
    # right panel
    px0 = 348
    px1 = PW - 14
    guard(px0, 110, px1 - px0, top - 110, PW, PH, "v004 panel")
    box(c, px0, 110, px1 - px0, top - 110, r=4, lw=0.9)
    T(c, px0 + 8, top - 18, "保証・ご登録のご案内", "GOTH-B", 8.4)
    hline(c, px0 + 8, top - 23, px1 - 8, w=0.5)
    faux_qr(c, px0 + 8, top - 92, 3.4, 15, seed=404)
    yy = para(c, px0 + 74, top - 36,
              "メーカー保証1年(要レシート提示)。製品番号: K27-T01-No.0048。"
              "化粧箱と本票は保証期間中保管してください。", "GOTH", 6.6,
              px1 - px0 - 82)
    para(c, px0 + 8, top - 100,
         "設置・配線サービスは承っておりません。初期不良は購入日より"
         "14日以内にご相談ください。", "GOTH", 6.6, px1 - px0 - 16)
    T(c, px0 + 8, 118, "※ 本票は架空テスト用です", "GOTH", 6)


# ================================================================ V005 =====
def draw_v005(c, size):
    """100x210mm hardware delivery-cum-receipt (No.2 copy): full grid with
    part numbers, units, unit prices; issue vs delivery dates; stamp box."""
    PW, PH = size
    M = 4.5 * MM
    L, R = M, PW - M
    c.setTitle("V005")
    T(c, PW / 2, PH - 16, "資材センター たてものや", "YG-B", 10.5, align="c")
    T(c, PW / 2, PH - 27, "御納品書・兼領収  (No.2 写)", "GOTH", 7.2,
      align="c")
    hline(c, L, PH - 32, R, w=0.7, dash=[2.5, 1.8])
    y = PH - 44
    T(c, L, y, "納品日 2020/08/24", "GOTH", 7.6)
    T(c, R, y, "伝票No. HM-3301", "GOTH", 7.6, align="r")
    y -= 11
    T(c, L, y, "発行日 2020/08/21", "GOTH", 7.6)
    T(c, R, y, "担当: やまだ", "GOTH", 7.6, align="r")
    stamp_square(c, R - 15, y + 6, 15, "建", size=7)
    y -= 8
    T(c, L, y, "納入先: テスト工務店 御中(テスト市架空町6-1 現場)", "GOTH", 7.6,
      maxw=R - L)
    y -= 6
    cols_w = [34, 86, 22, 20, 40, R - (L + 34 + 86 + 22 + 20 + 40)]
    cols = [L]
    for w in cols_w:
        cols.append(cols[-1] + w)
    heads = ["品番", "品名", "数量", "単位", "単価", "金額(税込)"]
    top = y
    c.setFillColor(GRAY_BG)
    c.rect(L, top - 14, R - L, 14, stroke=0, fill=1)
    c.setFillColor(black)
    for i, htxt in enumerate(heads):
        T(c, cols[i] + 2, top - 10.5, htxt, "GOTH-B", 6.2,
          align="r" if i >= 4 else "l")
    data = [("WD-24", "2x4材 6尺", 8, "本", 398, 3184),
            ("SC-38", "木ネジ 38mm 箱", 2, "箱", 540, 1080),
            ("PT-W2", "水性ペンキ 白 2L", 3, "缶", 1880, 5640),
            ("BR-40", "平刷毛 40mm", 2, "本", 312, 624),
            ("TP-CR", "養生テープ 50m", 1, "巻", 480, 480)]
    y = top - 14
    for code, nm, q, un, up, pr in data:
        y -= 13
        T(c, cols[0] + 2, y + 3.5, code, "PGOTH", 6.2)
        T(c, cols[1] + 2, y + 3.5, nm, "GOTH", 6.8,
          maxw=cols[2] - cols[1] - 4)
        T(c, cols[2] + 2, y + 3.5, num(q), "GOTH", 6.8, align="r")
        T(c, cols[3] + 2, y + 3.5, un, "GOTH", 6.8)
        T(c, cols[4] + 2, y + 3.5, num(up), "GOTH", 6.8, align="r")
        T(c, cols[5] - 2, y + 3.5, num(pr), "GOTH", 7, align="r")
    for _ in range(2):  # intentional blank ruled rows
        y -= 13
    grid_bot = y
    for gx in cols:
        vline(c, gx, grid_bot, top, w=0.45)
    hh = [top, top - 14]
    hh += [top - 14 - 13 * i for i in range(1, 8)]
    for gy in hh:
        hline(c, L, gy, R, w=0.45)
    y = grid_bot - 16
    tx = R - 118
    T(c, tx, y, "10%対象合計", "GOTH", 7.4)
    T(c, R, y, num(11008), "GOTH", 7.4, align="r")
    y -= 11
    T(c, tx, y, "内消費税", "GOTH", 7)
    T(c, R, y, num(tax_in(11008, 10)), "GOTH", 7, align="r")
    y -= 8
    hline(c, tx, y, R, w=0.6)
    hline(c, tx, y - 1.5, R, w=0.6)
    y -= 15
    T(c, tx, y, "合計請求額(税込)", "GOTH-B", 8)
    T(c, R, y - 2, "11,008円也", "YG-B", 11.5, align="r")
    y -= 24
    y = para(c, L, y, "備考: 現場2階への搬入分。荷受け印をお願いします。"
                      "代金は現金にて同時領収済。", "GOTH", 6.6, R - L - 60)
    stamp_square(c, R - 30, y + 10, 26, "受領", size=7)
    T(c, R, 12, "※ 架空テスト用", "GOTH", 5.8, align="r", color=GRAY_TX)


# ================================================================ V006 =====
def draw_v006(c, size):
    """76x200mm art-supply vertical certificate: framed page, vertical
    columns for date/store/items (right-to-left), kanji-numeral total.
    Purchase date printed without year."""
    PW, PH = size
    c.setTitle("V006")
    box(c, 6, 6, PW - 12, PH - 12, r=9, lw=1.1)
    box(c, 11, 11, PW - 22, PH - 22, r=6, lw=0.5)
    T(c, PW / 2, PH - 34, "御 買 上 証", "MINCH", 13, align="c")
    hline(c, PW / 2 - 40, PH - 40, PW / 2 + 40, w=0.5)
    # vertical date (year omitted) and vertical store name
    vtext(c, PW - 24, PH - 52, "六月四日", "MINCH", 8.5, lead=11)
    vtext(c, PW - 24, PH - 150, "十五時二分", "MINCH", 7, lead=9)
    vtext(c, 30, PH - 52, "画材 ときわ堂", "MINCH", 10.5, lead=13.5)
    vtext(c, 30, PH - 176, "テスト市架空町", "MINCH", 6.5, lead=8.6)
    # item columns read right to left
    items = [("キャンバスF6×五", 3100), ("アクリル絵具十二色", 4620),
             ("平筆二点", 960), ("パレットナイフ", 320)]
    colx = PW - 48
    for nm, pr in items:
        vtext(c, colx, PH - 84, nm, "PMIN", 8.2, lead=10.4)
        T(c, colx, PH - 84 - 10.4 * len(nm) - 10, num(pr), "GOTH", 6.8,
          align="c")
        colx -= 26
    band_y = 70
    guard(20, band_y - 22, PW - 40, 66, PW, PH, "v006 sum")
    T(c, PW / 2, band_y + 32, "合 計 金 額", "MINCH", 9, align="c")
    T(c, PW / 2, band_y + 12, "金九阡円也", "YG-B", 14.5, align="c")
    T(c, PW / 2, band_y - 2, "(¥9,000 税込 / 内消費税 818)", "GOTH", 6.6,
      align="c")
    T(c, PW - 18, 15, "架空テスト", "GOTH", 5.2, align="r", color=GRAY_TX)


# ================================================================ V007 =====
def draw_v007(c, size):
    """180x90mm landscape stationery receipt with perforated left stub,
    addressee line, dotted leaders, stamp overlapping the total box."""
    PW, PH = size
    c.setTitle("V007")
    div = 55 * MM
    vline(c, div, 4, PH - 4, w=0.7, dash=[2.5, 2])
    # stub
    T(c, div / 2, PH - 18, "ほしぞら堂 控え", "GOTH-B", 8, align="c")
    T(c, div / 2, PH - 32, "No. R1-058", "PGOTH", 7, align="c")
    T(c, div / 2, PH - 46, "令和元年5月7日", "GOTH", 7.6, align="c")
    T(c, div / 2, PH - 62, "¥4,422", "YG-B", 11.5, align="c")
    stamp_circle(c, div / 2, 34, 11, "済")
    c.saveState()
    c.translate(6, PH / 2)
    c.rotate(90)
    T(c, 0, 0, "架空テスト", "GOTH", 5.5, align="c", color=GRAY_TX)
    c.restoreState()
    # main
    x0 = div + 5 * MM
    R = PW - 5 * MM
    T(c, x0, PH - 18, "御 領 収 書", "GOTH-B", 12)
    T(c, R, PH - 14, "文具 ほしぞら堂", "YG-B", 9, align="r")
    T(c, R, PH - 25, "テスト市架空町5-7 / TEL 000-0000-0000", "GOTH", 6.2,
      align="r")
    T(c, x0, PH - 36, "株式会社テスト産業 御中", "GOTH", 9)
    hline(c, x0, PH - 39, x0 + 128, w=0.5)
    T(c, R, PH - 36, "令和 元年 5月 7日", "MINCH", 8, align="r")
    y = PH - 56
    items = [("長3封筒 100枚入", 660), ("朱肉 大", 1100),
             ("ゴム印(氏名印) 特注", 2200), ("スティックのり 小 ×3", 462)]
    for nm, pr in items:
        T(c, x0, y, nm, "GOTH", 8.2)
        nw = Wd(nm, "GOTH", 8.2)
        leader(c, x0 + nw + 4, R - 96, y + 1)
        T(c, R - 92, y, num(pr), "GOTH", 8.2, align="r")
        y -= 12
    y -= 6
    guard(R - 182, y - 10, 182, 24, PW, PH, "v007 total")
    dbox(c, R - 182, y - 10, 182, 24)
    T(c, R - 176, y - 1, "合計金額", "GOTH-B", 8.6)
    T(c, R - 66, y - 2, yen(4422, "¥"), "YG-B", 11.5, align="r")
    T(c, R - 58, y, "(内税 327)", "GOTH", 6.4)
    stamp_circle(c, R - 186, y + 2, 12, "ほし", size=7)
    T(c, x0, 22, "上記金額、現金にて頂戴いたしました。", "GOTH", 7)


# ================================================================ V008 =====
def draw_v008(c, size):
    """72x152mm bakery takeaway ticket: rounded outer card, bread motif,
    item price-tag tiles in two columns, wavy thank-you line."""
    PW, PH = size
    c.setTitle("V008")
    box(c, 8, 8, PW - 16, PH - 16, r=13, lw=1.1)
    # bread motif
    c.setLineWidth(0.8)
    c.roundRect(PW / 2 - 26, PH - 50, 52, 17, 8)
    for i in (-14, 0, 14):
        vline(c, PW / 2 + i, PH - 42, PH - 36, w=0.9)
    T(c, PW / 2, PH - 66, "ベーカリー ひだまり", "GOTH-B", 10.5, align="c")
    T(c, PW / 2, PH - 77, "お持ち帰り専門店(8% 税込価格)", "GOTH", 6.6,
      align="c")
    T(c, PW / 2, PH - 86, "架空テスト", "GOTH", 5.6, align="c",
      color=GRAY_TX)
    items = [("ミルクフランス", "×4", 648), ("あんぱん", "×2", 356),
             ("クロワッサン", "×3", 648), ("食パン(半斤)", "", 324),
             ("アイスコーヒー", "M", 300)]
    tw = (PW - 40 - 6) / 2.0
    th = 36
    for i, (nm, q, pr) in enumerate(items):
        col = i % 2
        row = i // 2
        tx = 20 + col * (tw + 6)
        ty = PH - 104 - row * (th + 7) - th
        box(c, tx, ty, tw, th, r=5, dash=[1.5, 1.5], lw=0.7)
        T(c, tx + 5, ty + th - 12, nm, "GOTH", 6.9, maxw=tw - 10)
        if q:
            T(c, tx + 5, ty + th - 22, q, "GOTH", 6.2)
        T(c, tx + tw - 5, ty + 4, num(pr), "GOTH-B", 9.5, align="r")
    y = PH - 104 - 3 * (th + 7) - 8
    hline(c, 20, y, PW - 20, w=0.8)
    y -= 15
    T(c, 20, y, "合計(5点)", "GOTH-B", 8.6)
    T(c, PW - 20, y - 2, yen(2276, "¥"), "YG-B", 12.5, align="r")
    y -= 12
    T(c, 20, y, "(うち消費税等 168)", "GOTH", 6.6)
    y -= 13
    T(c, 20, y, "お預り ¥2,500  おつり ¥224", "GOTH", 7.6)
    y -= 14
    wavy(c, 24, PW - 24, y)
    T(c, PW / 2, y - 11, "焼きたてを、またのお越しを", "MINCH", 7, align="c")
    T(c, 20, 28, "2024/9/9(月) 09:02", "GOTH", 6.8)
    T(c, PW - 20, 28, "No.0909-021", "GOTH", 6.8, align="r")


# ================================================================ V009 =====
def draw_v009(c, size):
    """90x150mm cafe bill: side-by-side 10% dine-in and 8% takeout panels
    with separate subtotals, one combined total band, table/staff row."""
    PW, PH = size
    c.setTitle("V009")
    T(c, PW / 2, PH - 18, "カフェ つきよみ", "GOTH-B", 11.5, align="c")
    T(c, PW / 2, PH - 29, "お 会 計 票(お客様控)", "GOTH", 7.2, align="c")
    T(c, 8, PH - 42, "テーブル No.4", "GOTH", 7.4)
    T(c, PW - 8, PH - 42, "2025年1月12日(日)", "GOTH", 7.4, align="r")
    T(c, 8, PH - 53, "係: こばやし", "GOTH", 6.8)
    T(c, PW - 8, PH - 53, "12:44 会計", "GOTH", 6.8, align="r")
    top = PH - 62
    pw = (PW - 24 - 8) / 2.0
    ph = 128
    panels = [
        (8, "店内飲食 (10%)",
         [("カフェラテ ×2", 1100), ("チーズケーキ", 638)], 1738, 10),
        (16 + pw, "お持ち帰り (8%)",
         [("たまごサンド", 864), ("本日のコーヒー", 450)], 1314, 8),
    ]
    for px, title, rows, sub, rate in panels:
        box(c, px, top - ph, pw, ph, r=3, lw=0.8)
        c.setFillColor(GRAY_BG)
        c.rect(px, top - 15, pw, 15, stroke=0, fill=1)
        c.setFillColor(black)
        T(c, px + pw / 2, top - 11, title, "GOTH-B", 7.4, align="c")
        yy = top - 28
        for nm, pr in rows:
            T(c, px + 5, yy, nm, "GOTH", 7.1, maxw=pw - 40)
            T(c, px + pw - 5, yy, num(pr), "GOTH", 7.1, align="r")
            yy -= 11
        hline(c, px + 5, top - ph + 24, px + pw - 5, w=0.5)
        T(c, px + 5, top - ph + 13, "小計", "GOTH-B", 7)
        T(c, px + pw - 5, top - ph + 13, num(sub), "GOTH-B", 7.4, align="r")
        T(c, px + pw - 5, top - ph + 4, "(内税 %d)" % tax_in(sub, rate),
          "GOTH", 5.8, align="r")
    y = top - ph - 14
    guard(8, y - 22, PW - 16, 22, PW, PH, "v009 total")
    dbox(c, 8, y - 22, PW - 16, 22)
    T(c, PW / 2, y - 15, "お会計合計  ¥3,052 (税込)", "YG-B", 11.5, align="c")
    y -= 34
    T(c, 8, y, "お預り ¥4,000", "GOTH", 8.2)
    T(c, PW - 8, y, "おつり ¥948", "GOTH", 8.2, align="r")
    y -= 12
    T(c, PW / 2, y, "店内とお持ち帰りで税率が異なります", "GOTH", 6.2,
      align="c")
    box(c, 8, 14, PW - 16, 18, dash=[2, 2], lw=0.6)
    T(c, PW / 2, 20, "架空テスト", "GOTH", 6.4, align="c", color=GRAY_TX)


# ================================================================ V010 =====
def draw_v010(c, size):
    """100x160mm apparel slip: hanging-tag header with punched hole and
    string, size/color table columns, right-stacked tender block."""
    PW, PH = size
    c.setTitle("V010")
    # tag
    tag_x, tag_w = 34, PW - 68
    tag_y, tag_h = PH - 128, 96
    vline(c, PW / 2 + 3, tag_y + tag_h - 8, PH + 6, w=0.7)  # string
    box(c, tag_x, tag_y, tag_w, tag_h, r=10, lw=1.2)
    c.setLineWidth(0.9)
    c.circle(PW / 2, tag_y + tag_h - 10, 4.2)
    T(c, PW / 2, tag_y + tag_h - 40, "ワーク&タウン みらい店", "YG-B", 10,
      align="c", maxw=tag_w - 16)
    T(c, PW / 2, tag_y + tag_h - 53, "作業服・ユニフォーム・カジュアル衣料",
      "GOTH", 6.6, align="c")
    T(c, PW / 2, tag_y + tag_h - 66, "架空テスト", "GOTH", 5.4, align="c",
      color=GRAY_TX)
    T(c, tag_x + 8, tag_y + 10, "〒000-0000 テスト市架空町8-15", "GOTH", 6.2)
    T(c, tag_x + tag_w - 8, tag_y + 10, "TEL 000-000-0000", "GOTH", 6.2,
      align="r")
    y = tag_y - 16
    T(c, 10, y, "御 買 上 明 細", "GOTH-B", 9)
    T(c, PW - 10, y, "2023-04-05(水) 14:26", "GOTH", 7.2, align="r")
    y -= 6
    heads = ["品名", "サイズ", "色", "数量", "金額(税込)"]
    cw = [108, 34, 30, 28, PW - 20 - 108 - 34 - 30 - 28]
    cx = [10]
    for w in cw:
        cx.append(cx[-1] + w)
    top = y
    c.setFillColor(GRAY_BG)
    c.rect(cx[0], top - 14, cx[-1] - 10, 14, stroke=0, fill=1)
    c.setFillColor(black)
    for i, htxt in enumerate(heads):
        T(c, cx[i] + 2, top - 10, htxt, "GOTH-B", 6.8,
          align="r" if i >= 3 else "l")
    rows = [("ポロシャツ(ロゴ無)", "M", "紺", 3, 5970),
            ("作業用グローブ", "L", "灰", 5, 1950),
            ("ショートエプロン", "F", "黒", 2, 2580)]
    y = top - 14
    for nm, sz, col, q, pr in rows:
        y -= 14
        T(c, cx[0] + 2, y + 4, nm, "GOTH", 7.6, maxw=cw[0] - 6)
        T(c, cx[1] + 2, y + 4, sz, "GOTH", 7.6)
        T(c, cx[2] + 2, y + 4, col, "GOTH", 7.6)
        T(c, cx[3] - 2, y + 4, num(q), "GOTH", 7.6, align="r")
        T(c, cx[4] - 2, y + 4, num(pr), "GOTH", 7.8, align="r")
        hline(c, 10, y, cx[-1], w=0.3)
    y -= 16
    bx = PW - 10 - 150
    T(c, bx, y, "合計(税込)", "GOTH-B", 8.4)
    T(c, PW - 10, y - 1, yen(10500, "¥"), "YG-B", 11, align="r")
    y -= 12
    T(c, bx, y, "内消費税", "GOTH", 6.8)
    T(c, PW - 10, y, num(tax_in(10500, 10)), "GOTH", 6.8, align="r")
    y -= 12
    T(c, bx, y, "お預り / おつり", "GOTH", 7.2)
    T(c, PW - 10, y, "11,000 / 500", "GOTH", 7.2, align="r")
    y -= 16
    T(c, 10, y, "ご返品は未使用・タグ付き、7日以内に限り承ります。",
      "GOTH", 6.6)
    T(c, 10, 12, "会員番号 ****-7742 / 係: たなか", "GOTH", 6.2)


# ================================================================ V011 =====
def draw_v011(c, size):
    """Postcard-landscape florist delivery slip: double frame, recipient
    box, item table, explicit deferred-billing band (NOT payment proof)."""
    PW, PH = size
    c.setTitle("V011")
    box(c, 8, 8, PW - 16, PH - 16, lw=1.0)
    box(c, 12, 12, PW - 24, PH - 24, lw=0.4)
    T(c, 20, PH - 30, "納  品  書", "GOTH-B", 13)
    T(c, 20, PH - 42, "フラワー&グリーン しきさい", "MINCH", 8)
    T(c, PW - 20, PH - 26, "No. FL-0412", "GOTH", 7.4, align="r")
    T(c, PW - 20, PH - 37, "納品日 2024年5月22日", "GOTH", 7.4, align="r")
    T(c, PW - 20, PH - 48, "発行日 2024年5月20日", "GOTH", 7.4, align="r")
    box(c, 20, PH - 92, 176, 38, lw=0.7)
    T(c, 24, PH - 66, "お届け先", "GOTH-B", 6.6)
    T(c, 24, PH - 78, "テスト市架空町7-3", "GOTH", 7.8)
    T(c, 24, PH - 88, "ひかりオフィス 御中", "GOTH", 7.8)
    T(c, 206, PH - 66, "件名: エントランス装花・観葉植物の設置", "GOTH", 7.4)
    y = PH - 108
    heads = ["品名", "数量", "金額(税込)"]
    cw = [200, 40, PW - 40 - 240]
    cx = [20]
    for w in cw:
        cx.append(cx[-1] + w)
    c.setFillColor(GRAY_BG)
    c.rect(20, y - 13, PW - 40, 13, stroke=0, fill=1)
    c.setFillColor(black)
    for i, htxt in enumerate(heads):
        T(c, cx[i] + 3, y - 9.5, htxt, "GOTH-B", 6.8,
          align="r" if i == 2 else "l")
    rows = [("観葉植物 パキラ (Lサイズ)", 1, 4000),
            ("陶器鉢 白色 14号", 1, 2200),
            ("鉢受け皿 14号用", 1, 400)]
    for nm, q, pr in rows:
        y -= 14
        T(c, cx[0] + 3, y + 4, nm, "GOTH", 7.4, maxw=cw[0] - 10)
        T(c, cx[1] + 3, y + 4, num(q), "GOTH", 7.4)
        T(c, cx[2] - 3, y + 4, num(pr), "GOTH", 7.4, align="r")
    y -= 8
    hline(c, 20, y, PW - 20, w=0.6)
    y -= 14
    T(c, cx[1], y, "合 計", "GOTH-B", 8.6)
    T(c, cx[2] - 3, y - 1, "¥6,600", "YG-B", 11, align="r")
    y -= 12
    T(c, cx[1], y, "(内消費税 600)", "GOTH", 6.4)
    y -= 14
    box(c, 20, y - 12, PW - 40, 20, dash=[3, 2], lw=1.0)
    T(c, PW / 2, y - 5,
      "代金: 後日 御請求書をお送りします(本書はお支払いの証ではありません)",
      "GOTH-B", 7.6, align="c")
    T(c, PW - 20, 26, "お受取者印: ", "GOTH", 7.2, align="r")
    hline(c, PW - 96, 24, PW - 38, w=0.5)
    T(c, 20, 18, "架空テスト", "GOTH", 5.6, color=GRAY_TX)
    # corner leaf motif
    c.setLineWidth(0.7)
    c.line(14, PH - 14, 26, PH - 22)
    c.line(14, PH - 14, 22, PH - 30)
    c.line(14, PH - 14, 30, PH - 16)


# ================================================================ V012 =====
def draw_v012(c, size):
    """130x95mm landscape produce counter slip: price-board tiles with unit
    price and quantity, marker-bold names, oval hand total, cash-only stamp."""
    PW, PH = size
    c.setTitle("V012")
    T(c, 12, PH - 20, "八百屋 あづまや", "YG-B", 12)
    T(c, PW - 12, PH - 18, "お買い上げ控え", "GOTH-B", 8, align="r")
    T(c, PW - 12, PH - 30, "2024年10月3日(木) 11:20", "GOTH", 7, align="r")
    T(c, 12, PH - 32, "テスト市架空町10-2 青空市場内", "GOTH", 6.8)
    items = [("みかん 袋", "@500", "×3", 1500), ("りんご バラ", "@120", "×8", 960),
             ("白菜 1/2", "", "", 178), ("大根", "@158", "×2", 316),
             ("ほうれん草", "@128", "×3", 384), ("バナナ", "@98", "×2", 196)]
    tw = (PW - 24 - 2 * 8) / 3.0
    th = 44
    for i, (nm, unit, q, pr) in enumerate(items):
        col, row = i % 3, i // 3
        tx = 12 + col * (tw + 8)
        ty = PH - 46 - row * (th + 8) - th
        box(c, tx, ty, tw, th, r=4, lw=0.9)
        T(c, tx + 5, ty + th - 13, nm, "GOTH-B", 8.2, maxw=tw - 10)
        lab = (unit + " " + q).strip()
        if lab:
            T(c, tx + 5, ty + th - 25, lab, "GOTH", 6.2)
        T(c, tx + tw - 5, ty + 5, num(pr), "YG-B", 11, align="r")
    y = PH - 46 - 2 * (th + 8) - 20
    guard(40, y - 24, PW - 80, 42, PW, PH, "v012 oval")
    c.setLineWidth(1.4)
    c.ellipse(PW / 2 - 96, y - 22, PW / 2 + 96, y + 16)
    T(c, PW / 2, y + 2, "合計  参阡伍佰参拾四円也", "MINCH", 10.5, align="c")
    T(c, PW / 2, y - 12, "(¥3,534 税込 / 内消費税 261)", "GOTH", 6.6,
      align="c")
    c.saveState()
    c.translate(34, y - 4)
    c.rotate(-8)
    box(c, -28, -9, 56, 18, lw=0.9)
    T(c, 0, -3, "現 金 の み", "GOTH-B", 7.4, align="c")
    c.restoreState()
    T(c, PW - 12, 12, "架空テスト", "GOTH", 5.6, align="r", color=GRAY_TX)


# ================================================================ V013 =====
def draw_v013(c, size):
    """80x150mm fuel POS slip: liters x unit-price grid, self-service meta,
    perforation with customer receipt stub beneath."""
    PW, PH = size
    M = 5 * MM
    L, R = M, PW - M
    c.setTitle("V013")
    T(c, PW / 2, PH - 15, "スタンド ながれやま給油所", "YG-B", 9.2,
      align="c", maxw=R - L)
    T(c, PW / 2, PH - 25, "ハイオク・レギュラー・軽油・灯油", "GOTH", 6.2,
      align="c")
    hline(c, L, PH - 30, R, w=0.8)
    y = PH - 41
    T(c, L, y, "2023/08/11(祝) 08:31", "GOTH", 7.2)
    T(c, R, y, "給油機 #5", "GOTH", 7.2, align="r")
    y -= 10.5
    T(c, L, y, "扱い: セルフ / 車番確認済", "GOTH", 6.8)
    y -= 7
    heads = ["品名", "数量", "単位", "単価", "金額"]
    cw = [52, 34, 22, 30, R - L - 52 - 34 - 22 - 30]
    cx = [L]
    for w in cw:
        cx.append(cx[-1] + w)
    top = y
    c.setFillColor(GRAY_BG)
    c.rect(L, top - 13, R - L, 13, stroke=0, fill=1)
    c.setFillColor(black)
    for i, htxt in enumerate(heads):
        T(c, cx[i] + 2, top - 9.5, htxt, "GOTH-B", 6.4,
          align="r" if i else "l")
    y = top - 13 - 14
    T(c, cx[0] + 2, y + 4, "ハイオク", "GOTH", 7.8)
    T(c, cx[1] + 2, y + 4, "32.4", "PGOTH", 7.6, align="r")
    T(c, cx[2] + 2, y + 4, "L", "GOTH", 7.2)
    T(c, cx[3] + 2, y + 4, "188", "PGOTH", 7.6, align="r")
    T(c, cx[4] - 2, y + 4, "6,091", "GOTH", 7.8, align="r")
    for gx in cx:
        vline(c, gx, y, top, w=0.45)
    for gy in (top, top - 13, y):
        hline(c, L, gy, R, w=0.45)
    y -= 16
    T(c, L, y, "お預り 現金", "GOTH", 8)
    T(c, R, y, yen(7000), "GOTH", 8, align="r")
    y -= 11.5
    T(c, L, y, "おつり", "GOTH", 8)
    T(c, R, y, yen(909), "GOTH", 8, align="r")
    y -= 11
    T(c, L, y, "(内消費税10% 553)", "GOTH", 6.4)
    T(c, PW / 2, y - 12, "※ 架空テスト用", "GOTH", 5.6, align="c",
      color=GRAY_TX)
    # perforation
    py = y - 22
    hline(c, 3, py, PW - 3, w=0.7, dash=[2.5, 2])
    T(c, PW / 2, py - 9, "- - キリトリ線  お客様控え 領収書 - -", "GOTH", 6,
      align="c")
    sy = py - 26
    T(c, PW / 2, sy, "領 収 書", "MINCH", 10, align="c")
    sy -= 15
    T(c, L, sy, "御買上金額", "GOTH", 8)
    T(c, R - 56, sy, "¥6,091-", "YG-B", 11, align="r")
    stamp_circle(c, R - 16, sy + 3, 11, "長", size=7)
    sy -= 14
    T(c, L, sy, "2023年8月11日  /  ハイオク代 として", "GOTH", 6.8)
    T(c, L, sy - 12, "ながれやま給油所  テスト市架空町11-6", "GOTH", 6.4)


# ================================================================ V014 =====
def draw_v014(c, size):
    """A5 portrait used-equipment sale receipt: used-goods oval stamp,
    condition-grade warranty table, per-item notes, terms paragraph box,
    square clerk seal."""
    PW, PH = size
    M = 13 * MM
    L, R = M, PW - M
    c.setTitle("V014")
    T(c, L, PH - 24, "中古機器 りさいくる市場", "YG-B", 11.5)
    T(c, L, PH - 36, "中古品 御領収証(明細付)", "GOTH", 8)
    c.setLineWidth(1.1)
    c.setStrokeColor(RED)
    c.setFillColor(RED)
    c.ellipse(R - 74, PH - 42, R, PH - 18)
    T(c, R - 37, PH - 33.5, "中 古 品", "MINCH", 9.5, align="c", color=RED)
    c.setStrokeColor(black)
    c.setFillColor(black)
    y = PH - 54
    T(c, L, y, "発行 2021年11月19日", "GOTH", 7.8)
    T(c, R, y, "No. UC-1187 / 担当: すずき", "GOTH", 7.8, align="r")
    y -= 11
    T(c, L, y, "テスト市架空町13-9 / TEL 000-000-0000", "GOTH", 7.2)
    y -= 8
    heads = ["品名・状態", "ランク", "保証", "金額(税込)"]
    cw = [160, 52, 44, R - L - 256]
    cx = [L]
    for w in cw:
        cx.append(cx[-1] + w)
    top = y
    c.setFillColor(GRAY_BG)
    c.rect(L, top - 14, R - L, 14, stroke=0, fill=1)
    c.setFillColor(black)
    for i, htxt in enumerate(heads):
        T(c, cx[i] + 3, top - 10, htxt, "GOTH-B", 7.2,
          align="r" if i == 3 else "l")
    rows = [("モノクロレーザープリンタ", "B:良好", "3ヶ月", 48000,
             "印字確認済・トナー残3割程度・ドラム交換歴あり"),
            ("オフィスチェア(布張)", "C:使用感有", "1ヶ月", 12000,
             "昇降・ロッキング動作確認済・肘掛けに小さな破れ")]
    y = top - 14
    for nm, gr, wa, pr, memo in rows:
        y -= 15
        T(c, cx[0] + 3, y + 4, nm, "GOTH", 8, maxw=cw[0] - 8)
        T(c, cx[1] + 3, y + 4, gr, "GOTH", 7.4)
        T(c, cx[2] + 3, y + 4, wa, "GOTH", 7.4)
        T(c, cx[3] - 3, y + 4, num(pr), "GOTH", 8.2, align="r")
        y -= 10
        T(c, cx[0] + 8, y + 3, memo, "GOTH", 6.2, maxw=R - L - 12,
          color=GRAY_TX)
        c.setFillColor(black)
        hline(c, L, y - 3, R, w=0.35)
    y -= 18
    guard(R - 240, y - 24, 240, 40, PW, PH, "v014 total")
    dbox(c, R - 240, y - 24, 240, 40)
    T(c, R - 234, y - 2, "ご購入額(税込)", "GOTH-B", 8.6)
    T(c, R - 90, y - 4, "¥60,000-", "YG-B", 12.5, align="r")
    T(c, R - 82, y - 3, "(内税5,454)", "GOTH", 6)
    T(c, R - 234, y - 16, "お支払: 現金 ¥60,000", "GOTH", 7.2)
    y -= 44
    box(c, L, y - 84, R - L - 48, 84, lw=0.7)
    T(c, L + 5, y - 12, "保証・返品について", "GOTH-B", 7.4)
    para(c, L + 5, y - 24,
         "保証は正常利用における自然故障に限り、表中の期間とします。"
         "消耗品・外装の経年劣化は対象外です。返品は初期不良のみ、お受取日"
         "から7日以内に限り承ります。その際は本票が必要です。中古品のため"
         "同等品との交換対応となる場合があります。", "GOTH", 6.6,
         R - L - 58)
    stamp_square(c, R - 40, y - 46, 32, "鈴", size=11)
    T(c, R - 24, y - 54, "店長・査定", "GOTH", 6, align="c")
    T(c, R, 14, "※ 架空テスト用レシート", "GOTH", 5.8, align="r",
      color=GRAY_TX)


# ================================================================ V015 =====
def draw_v015(c, size):
    """A4 wholesaler combined invoice-and-receipt: addressee with rule,
    billing-amount box, issuer block with registration T-number and seal,
    gray-banded detail grid, bank block, paid-confirmation band with seal."""
    PW, PH = size
    M = 42
    L, R = M, PW - M
    c.setTitle("V015")
    T(c, L, PH - 14, "架空テスト用サンプル", "GOTH", 6.5, color=GRAY_TX)
    c.setFillColor(black)
    T(c, PW / 2, PH - 44, "御請求書・御領収証", "YG-B", 15.5, align="c")
    hline(c, PW / 2 - 108, PH - 50, PW / 2 + 108, w=1.1)
    hline(c, PW / 2 - 108, PH - 53.5, PW / 2 + 108, w=0.5)
    T(c, L, PH - 82, "テスト商事株式会社 御中", "GOTH-B", 11.5)
    hline(c, L, PH - 86, L + 176, w=0.5)
    T(c, L, PH - 99, "件名: 事務消耗品 9月分 一式として", "GOTH", 8.2)
    # billing amount box
    guard(L, PH - 152, 218, 46, PW, PH, "v015 billbox")
    dbox(c, L, PH - 152, 218, 46)
    T(c, L + 10, PH - 126, "ご請求金額(税込)", "GOTH", 8.6)
    T(c, L + 208, PH - 134, "¥81,840-", "YG-B", 15, align="r")
    T(c, L + 10, PH - 142, "(内消費税 ¥7,440)", "GOTH", 6.8)
    # issuer block
    ix = 372
    T(c, ix, PH - 92, "卸売 オフィスサプライ山川", "GOTH-B", 10.5)
    T(c, ix, PH - 105, "〒000-0000 テスト市架空町9-18", "GOTH", 7.8)
    T(c, ix, PH - 116, "登録番号 T0000000000000", "GOTH", 7.8)
    T(c, ix, PH - 127, "発行日 2024年9月25日", "GOTH", 7.8)
    T(c, ix, PH - 138, "No. INV-0925-77   TEL 000-0000-0000", "GOTH", 7.2)
    stamp_square(c, ix - 46, PH - 148, 30, "山川", size=8)
    # meta row
    y = PH - 172
    T(c, L, y, "お支払期日: 2024年9月30日", "GOTH", 8)
    T(c, L + 190, y, "お支払方法: 銀行振込", "GOTH", 8)
    # table
    y -= 14
    heads = ["品 目", "数量", "単価(税抜)", "金額(税抜)"]
    cw = [250, 50, 90, R - L - 390]
    cx = [L]
    for w in cw:
        cx.append(cx[-1] + w)
    c.setFillColor(GRAY_BG)
    c.rect(L, y - 16, R - L, 16, stroke=0, fill=1)
    c.setFillColor(black)
    for i, htxt in enumerate(heads):
        T(c, cx[i] + 4, y - 11.5, htxt, "GOTH-B", 7.6,
          align="r" if i else "l")
    rows = [("コピー用紙 A4(2,500枚/箱)", "10箱", 2980, 29800),
            ("純正トナー TN-00X", "4個", 9800, 39200),
            ("OAマルチクリーナー", "12個", 450, 5400)]
    for nm, q, up, pr in rows:
        y -= 18
        T(c, cx[0] + 4, y + 5, nm, "GOTH", 8.4, maxw=cw[0] - 10)
        T(c, cx[1] + 4, y + 5, q, "GOTH", 8.4, align="r")
        T(c, cx[2] - 4, y + 5, num(up), "GOTH", 8.4, align="r")
        T(c, cx[3] - 4, y + 5, num(pr), "GOTH", 8.4, align="r")
        hline(c, L, y - 1, R, w=0.35)
    y -= 20
    sx = cx[2] - 60
    T(c, sx, y, "小 計", "GOTH", 8.6)
    T(c, R - 4, y, num(74400), "GOTH", 8.6, align="r")
    y -= 15
    T(c, sx, y, "消費税(10%)", "GOTH", 8)
    T(c, R - 4, y, num(7440), "GOTH", 8, align="r")
    y -= 8
    hline(c, sx, y, R, w=0.8)
    y -= 18
    T(c, sx, y, "合 計(税込)", "GOTH-B", 9.4)
    T(c, R - 4, y - 2, "¥81,840", "YG-B", 12, align="r")
    y -= 26
    box(c, L, y - 46, R - L, 46, lw=0.7)
    T(c, L + 6, y - 14, "お振込先", "GOTH-B", 7.6)
    T(c, L + 6, y - 27, "テスト銀行 架空支店(店番 000) 普通 0000000", "GOTH",
      7.8)
    T(c, L + 6, y - 38, "オフィスサプライヤマカワ ※振込手数料は貴社ご負担",
      "GOTH", 7.2)
    y -= 66
    guard(L, y - 30, R - L, 30, PW, PH, "v015 paid")
    box(c, L, y - 30, R - L, 30, lw=1.0)
    box(c, L + 2, y - 28, R - L - 4, 26, lw=0.4)
    T(c, L + 10, y - 19,
      "上記金額、2024年9月30日付 お振込み着金確認のうえ 領収いたしました。",
      "GOTH-B", 8.6)
    stamp_circle(c, R - 30, y - 15, 13, "山川之印", size=6.4)
    T(c, L, 54, "備考: 収入印紙は省略(電子帳票のため)。本書は兼用帳票です。",
      "GOTH", 6.6)
    T(c, L, 42, "不明点は営業担当(架空)までお問い合わせください。", "GOTH",
      6.6)


# ================================================================ V016 =====
def draw_v016(c, size):
    """A4 EC order completion page: browser-style window frame with title
    bar, shadowed order card, status tracker, masked card payment row."""
    PW, PH = size
    c.setTitle("V016")
    guard(28, 28, PW - 56, PH - 56, PW, PH, "v016 window")
    box(c, 28, 28, PW - 56, PH - 56, r=9, lw=1.2)
    # title bar
    c.setFillColor(Color(0.22, 0.28, 0.34))
    c.roundRect(28, PH - 58, PW - 56, 30, 9, stroke=0, fill=1)
    c.rect(28, PH - 58, PW - 56, 15, stroke=0, fill=1)
    for i, colr in enumerate((Color(0.9, 0.3, 0.3), Color(0.95, 0.75, 0.25),
                              Color(0.3, 0.75, 0.4))):
        c.setFillColor(colr)
        c.circle(42 + i * 12, PH - 43, 3.6, stroke=0, fill=1)
    T(c, PW / 2, PH - 46.5, "テストショップ ― ご注文ありがとうございました",
      "GOTH", 8.6, align="c", color=white)
    T(c, PW - 36, PH - 46.5, "架空", "GOTH", 6, align="r", color=white)
    c.setFillColor(black)
    box(c, 40, PH - 78, PW - 80, 12, lw=0.5, fill=Color(0.95, 0.95, 0.97))
    T(c, 46, PH - 74.5, "注文照会ページ(架空アドレス) test-shop / orders",
      "PGOTH", 6.4, color=GRAY_TX)
    c.setFillColor(black)
    # order card with shadow
    cx0, cy0 = 52, 96
    cw_, ch_ = PW - 104, PH - 196
    c.setFillColor(Color(0.8, 0.8, 0.8))
    c.roundRect(cx0 + 3, cy0 - 3, cw_, ch_, 8, stroke=0, fill=1)
    c.setFillColor(white)
    c.roundRect(cx0, cy0, cw_, ch_, 8, stroke=0, fill=1)
    c.setFillColor(black)
    box(c, cx0, cy0, cw_, ch_, r=8, lw=0.9)
    y = cy0 + ch_ - 22
    T(c, cx0 + 14, y, "ご注文番号  ORD-20240718-00012", "PGOTH", 9.6)
    y -= 5
    hline(c, cx0 + 14, y, cx0 + cw_ - 14, w=0.6)
    y -= 15
    T(c, cx0 + 14, y, "ご注文日", "GOTH", 7.6, color=GRAY_TX)
    T(c, cx0 + cw_ - 14, y, "2024-07-18 22:41", "GOTH", 8.2, align="r")
    y -= 14
    T(c, cx0 + 14, y, "発送日", "GOTH", 7.6, color=GRAY_TX)
    T(c, cx0 + cw_ - 14, y, "2024-07-19", "GOTH", 8.2, align="r")
    y -= 14
    T(c, cx0 + 14, y, "お届け予定日", "GOTH", 7.6, color=GRAY_TX)
    T(c, cx0 + cw_ - 14, y, "2024-07-21(午前指定)", "GOTH", 8.2, align="r")
    c.setFillColor(black)
    y -= 12
    hline(c, cx0 + 14, y, cx0 + cw_ - 14, w=0.6)
    y -= 16
    T(c, cx0 + 14, y, "ご注文内容", "GOTH-B", 8.2)
    rows = [("互換テープカートリッジ 10本パック", "×1", 4860),
            ("名刺印刷 両面カラー 100枚(データ入稿)", "×1", 2200),
            ("配送料", "", 550)]
    for nm, q, pr in rows:
        y -= 14
        T(c, cx0 + 14, y, nm, "GOTH", 8.2, maxw=cw_ - 130)
        T(c, cx0 + cw_ - 96, y, q, "GOTH", 8.2, align="r")
        T(c, cx0 + cw_ - 14, y, num(pr), "GOTH", 8.2, align="r")
    y -= 12
    c.setFillColor(black)
    c.rect(cx0 + 14, y - 6, cw_ - 28, 24, stroke=0, fill=1)
    T(c, cx0 + 22, y, "お支払合計(税込)", "GOTH", 8.4, color=white)
    T(c, cx0 + cw_ - 22, y - 1, "¥7,610", "YG-B", 12.5, align="r",
      color=white)
    c.setFillColor(black)
    y -= 22
    T(c, cx0 + 14, y, "(内消費税 ¥691)", "GOTH", 6.6)
    y -= 14
    T(c, cx0 + 14, y, "お支払方法: クレジットカード ****-****-****-1234 "
                      "(一括払い・決済完了)", "GOTH", 7.8, maxw=cw_ - 28)
    y -= 13
    T(c, cx0 + 14, y, "お届け先: 〒000-0000 テスト市架空町12-6 サンプル倉庫",
      "GOTH", 7.8, maxw=cw_ - 28)
    # status tracker
    ty = cy0 - 34
    steps = ["注文確定", "決済完了", "出荷", "お届け"]
    n = len(steps)
    x_start, x_end = cx0 + 30, cx0 + cw_ - 30
    seg = (x_end - x_start) / (n - 1)
    hline(c, x_start, ty, x_end, w=1.4)
    for i, st in enumerate(steps):
        sx = x_start + seg * i
        done = i <= 2
        c.setFillColor(black if done else white)
        c.circle(sx, ty, 5, stroke=1, fill=1)
        c.setFillColor(black)
        T(c, sx, ty - 17, st, "GOTH", 6.4, align="c",
          color=None if done else GRAY_TX)
    c.setFillColor(black)
    T(c, PW / 2, ty - 34, "出荷済(配送状況は架空の追跡番号 TT-0000-1111 でご確認いただけます)",
      "GOTH", 7, align="c")
    T(c, PW / 2, 36, "この画面は架空テスト用に生成されました", "GOTH", 5.8,
      align="c", color=GRAY_TX)
    T(c, 34, 36, "テストショップ運営(架空)", "GOTH", 6, color=GRAY_TX)


# ================================================================ V017 =====
def draw_v017(c, size):
    """80x140mm coupon discount receipt: dashed scissor coupon strip at top,
    crossed-out pre-discount subtotal, discount rows, recalculated total."""
    PW, PH = size
    M = 5 * MM
    L, R = M, PW - M
    c.setTitle("V017")
    # coupon strip on top
    cph = 60
    box(c, 3, PH - cph - 3, PW - 6, cph, dash=[3, 2], lw=0.8)
    T(c, PW / 2, PH - 16, "クーポン使用レシート", "GOTH-B", 8.5, align="c")
    T(c, PW / 2, PH - 28, "「文具まつり 100円引」券 1枚 使用済", "GOTH", 6.8,
      align="c", maxw=PW - 14)
    T(c, PW / 2, PH - 39, "ドラッグストア くすりのもり", "GOTH", 7.2,
      align="c")
    T(c, PW / 2, PH - 49, "テスト市架空町14-2 / No. CP-7712", "GOTH", 6.2,
      align="c")
    # scissor marks
    sx = 10
    while sx < PW - 14:
        T(c, sx, PH - cph - 9, "✂", "GOTH", 6)
        sx += 18
    hline(c, 3, PH - cph - 3, PW - 3, w=0.7, dash=[2.5, 2])
    y = PH - cph - 22
    T(c, L, y, "2022年6月30日(木) 15:08", "GOTH", 7.4)
    T(c, R, y, "レジ #2", "GOTH", 7.4, align="r")
    y -= 7
    hline(c, L, y, R, w=0.6)
    y -= 13
    items = [("ボールペン 3色セット", 440), ("付箋紙 大×小セット", 380),
             ("のりテープ本体", 330), ("除菌ハンドジェル", 640)]
    for nm, pr in items:
        T(c, L, y, nm, "GOTH", 7.8, maxw=R - L - 44)
        T(c, R, y, num(pr), "GOTH", 7.8, align="r")
        y -= 11.5
    y += 1
    hline(c, L, y, R, w=0.6)
    y -= 13
    T(c, L, y, "クーポン前小計", "GOTH", 7.4)
    sub_y = y
    T(c, R, y, num(1790), "GOTH", 7.4, align="r")
    # strike the subtotal figure
    hline(c, R - Wd(num(1790), "GOTH", 7.4) - 2, sub_y + 2.5, R + 1, w=0.8)
    y -= 12
    T(c, L, y, "値引 文具まつり100円券", "GOTH", 7.2)
    T(c, R, y, "-100", "GOTH", 7.4, align="r", color=RED)
    y -= 8
    hline(c, L, y, R, w=0.6)
    y -= 14
    guard(L, y - 20, R - L, 20, PW, PH, "v017 total")
    c.setFillColor(black)
    c.rect(L, y - 20, R - L, 20, stroke=0, fill=1)
    T(c, L + 6, y - 14, "合計(値引後・税込)", "GOTH", 7.2, color=white)
    T(c, R - 6, y - 15, "¥1,690", "YG-B", 11.5, align="r", color=white)
    c.setFillColor(black)
    y -= 32
    T(c, L, y, "10%対象 1,690 (内消費税 153)", "GOTH", 6.6)
    y -= 12
    T(c, L, y, "お預り", "GOTH", 8)
    T(c, R, y, yen(2000), "GOTH", 8, align="r")
    y -= 11
    T(c, L, y, "おつり", "GOTH", 8)
    T(c, R, y, yen(310), "GOTH", 8, align="r")
    y -= 11
    T(c, L, y, "クーポン券は回収しました", "GOTH", 6.4, color=GRAY_TX)
    T(c, PW / 2, 12, "架空テスト", "GOTH", 5.6, align="c", color=GRAY_TX)


# ================================================================ V018 =====
def draw_v018(c, size):
    """90x170mm points statement receipt: two-column points panel
    (redeemed vs earned) beside small item list, running point balance,
    amount paid partly in points."""
    PW, PH = size
    M = 5 * MM
    L, R = M, PW - M
    c.setTitle("V018")
    T(c, PW / 2, PH - 16, "ホームセンター きぼう館", "YG-B", 10, align="c")
    T(c, PW / 2, PH - 27, "ポイント併用レシート(会員控)", "GOTH", 6.8,
      align="c")
    hline(c, L, PH - 32, R, w=0.8)
    y = PH - 43
    T(c, L, y, "2025/2/14(金) 18:52", "GOTH", 7.4)
    T(c, R, y, "No. PT-0091", "GOTH", 7.4, align="r")
    y -= 10.5
    T(c, L, y, "会員カード ****-3308", "GOTH", 7)
    y -= 8
    # two-column points panel
    pw = (R - L - 8) / 2.0
    ph = 64
    guard(L, y - ph, R - L, ph, PW, PH, "v018 points")
    box(c, L, y - ph, pw, ph, r=4, lw=0.9)
    box(c, L + pw + 8, y - ph, pw, ph, r=4, lw=0.9)
    T(c, L + pw / 2, y - 13, "ご利用ポイント", "GOTH-B", 7, align="c")
    T(c, L + pw / 2, y - 34, "-500 P", "YG-B", 12, align="c", color=RED)
    T(c, L + pw / 2, y - 48, "(500円相当を充当)", "GOTH", 5.8, align="c")
    T(c, L + pw + 8 + pw / 2, y - 13, "今回付与ポイント", "GOTH-B", 7,
      align="c")
    T(c, L + pw + 8 + pw / 2, y - 34, "+38 P", "YG-B", 12, align="c")
    T(c, L + pw + 8 + pw / 2, y - 48, "(商品金額1%・四捨五入)", "GOTH", 5.8,
      align="c")
    y -= ph + 14
    T(c, L, y, "お買上明細", "GOTH-B", 7.6)
    y -= 12
    items = [("乾電池 単4×8本", 528), ("LED電球 40W相当", 1280),
             ("結束バンド 100本", 428), ("軍手 12双組", 658),
             ("防水スプレー", 880)]
    for nm, pr in items:
        T(c, L, y, nm, "GOTH", 7.6, maxw=R - L - 44)
        T(c, R, y, num(pr), "GOTH", 7.6, align="r")
        y -= 11
    y += 1
    hline(c, L, y, R, w=0.6)
    y -= 12
    T(c, L, y, "商品合計(税込)", "GOTH", 7.4)
    T(c, R, y, yen(3774), "GOTH", 7.4, align="r")
    y -= 11
    T(c, L, y, "ポイント充当", "GOTH", 7.4)
    T(c, R, y, "-¥500", "GOTH", 7.4, align="r", color=RED)
    y -= 8
    hline(c, L, y, R, w=0.6)
    y -= 15
    guard(L, y - 20, R - L, 20, PW, PH, "v018 total")
    dbox(c, L, y - 20, R - L, 20)
    T(c, L + 6, y - 14, "お支払額", "GOTH-B", 8)
    T(c, R - 6, y - 15, "¥3,274", "YG-B", 12, align="r")
    y -= 31
    T(c, L, y, "お預り ¥3,300 / おつり ¥26", "GOTH", 7.4)
    y -= 11
    T(c, L, y, "(内消費税 297)", "GOTH", 6.4)
    y -= 7
    hline(c, L, y, R, w=0.5, dash=[1.5, 1.5])
    y -= 12
    T(c, L, y, "ポイント残高", "GOTH-B", 7.6)
    T(c, R, y, "1,238 P", "GOTH-B", 8.4, align="r")
    y -= 10.5
    T(c, L, y, "(前回残高 1,700 P − 利用 500 P + 付与 38 P)", "GOTH", 6,
      maxw=R - L)
    y -= 12
    para(c, L, y, "ポイントの有効期限は最終利用日より1年です。",
         "GOTH", 6.2, R - L)
    T(c, PW / 2, 10, "架空テスト", "GOTH", 5.6, align="c", color=GRAY_TX)


# ================================================================ V019 =====
def draw_v019(c, size):
    """80x150mm negative refund slip: RETURN header band, red-negative
    amounts, original purchase reference, cash returned to customer."""
    PW, PH = size
    M = 5 * MM
    L, R = M, PW - M
    c.setTitle("V019")
    c.setFillColor(RED)
    c.rect(0, PH - 26, PW, 26, stroke=0, fill=1)
    T(c, PW / 2, PH - 17, "返 品 伝 票 (RETURN)", "YG-B", 10.5, align="c",
      color=white)
    c.setFillColor(black)
    T(c, PW / 2, PH - 35, "電器専門店 デンキノハコ お客様サービスカウンター",
      "GOTH", 6.6, align="c", maxw=PW - 10)
    hline(c, L, PH - 40, R, w=0.8)
    y = PH - 52
    T(c, L, y, "返品処理日 2024-07-25 13:10", "GOTH", 7.4)
    y -= 11
    T(c, L, y, "元伝票 No.EX-2207(2024-07-20購入分)", "GOTH", 6.8,
      maxw=R - L)
    y -= 10
    T(c, L, y, "処理者: せんと  /  承認印", "GOTH", 7)
    stamp_square(c, R - 16, y - 4, 15, "認", size=6.5)
    y -= 8
    hline(c, L, y, R, w=0.6, dash=[2, 1.6])
    y -= 14
    T(c, L, y, "返品明細", "GOTH-B", 7.8)
    y -= 12
    rows = [("HDMIケーブル 2m HD-20A", -1480, "未開封・未使用"),
            ("配送料(返品対象外のため0円)", 0, "")]
    for nm, pr, note in rows:
        T(c, L, y, nm, "GOTH", 7.4, maxw=R - L - 48)
        T(c, R, y, num(pr), "GOTH", 7.6, align="r",
          color=RED if pr < 0 else None)
        y -= 10
        if note:
            T(c, L + 8, y, note, "GOTH", 6, color=GRAY_TX)
            y -= 10
    hline(c, L, y, R, w=0.6)
    y -= 12
    T(c, L, y, "返金対象額(10%税込)", "GOTH", 7.2)
    T(c, R, y, "-¥1,480", "GOTH", 7.2, align="r", color=RED)
    y -= 10.5
    T(c, L, y, "(内消費税相当額 -134)", "GOTH", 6.4)
    y -= 10
    guard(L, y - 22, R - L, 22, PW, PH, "v019 total")
    c.setFillColor(RED)
    c.roundRect(L, y - 22, R - L, 22, 4, stroke=0, fill=1)
    T(c, L + 6, y - 15, "ご返金額", "GOTH-B", 8, color=white)
    T(c, R - 6, y - 16, "-¥1,480", "YG-B", 12, align="r", color=white)
    c.setFillColor(black)
    y -= 34
    T(c, L, y, "返金方法: 現金にてお渡し", "GOTH", 7.6)
    y -= 11
    T(c, L, y, "お渡し ¥1,480", "GOTH", 7.6)
    T(c, R, y, "受取印 →", "GOTH", 7, align="r")
    hline(c, R - 34, y - 2, R - 12, w=0.5)
    y -= 13
    y = para(c, L, y, "本票は返品・ご返金の記録であり、新たなお支払いの証"
                      "ではありません。元のお買上レシートと併せて保管して"
                      "ください。", "GOTH", 6.4, R - L)
    T(c, PW / 2, 10, "架空テスト", "GOTH", 5.6, align="c", color=GRAY_TX)


# ================================================================ V020 =====
def draw_v020(c, size):
    """74x124mm handwritten-style small-shop voucher: tilted ruled kraft
    paper, pencil-gray mincho hand lettering, kanji date/total, shop stamp,
    date year omitted."""
    PW, PH = size
    c.setTitle("V020")
    # kraft paper fill
    c.saveState()
    c.translate(PW / 2, PH / 2)
    c.rotate(-1.5)
    c.setFillColor(Color(0.97, 0.95, 0.88))
    c.rect(-PW / 2 + 5, -PH / 2 + 5, PW - 10, PH - 10, stroke=0, fill=1)
    c.restoreState()
    c.setFillColor(black)
    # hand-feel text: mincho, slight gray
    pencil = Color(0.25, 0.25, 0.28)
    x0, x1 = 14, PW - 14
    T(c, x0, PH - 22, "八百 たなか", "MINCH", 12, color=pencil)
    hline(c, x0, PH - 26, x0 + 66, w=0.6)
    T(c, x1, PH - 22, "売上控", "MINCH", 7.5, align="r", color=pencil)
    T(c, x1, PH - 36, "五月 十九日", "MINCH", 8, align="r", color=pencil)
    T(c, x0, PH - 40, "(年省略・店内控え)", "GOTH", 5.6, color=GRAY_TX)
    y = PH - 56
    hline(c, x0, y + 5, x1, w=0.4)
    rows = [("ごぼう 2束", 300), ("みょうが 1袋", 150),
            ("青梅 1kg(追熟用)", 520)]
    for nm, pr in rows:
        T(c, x0, y, nm, "MINCH", 9, color=pencil)
        leader(c, x0 + Wd(nm, "MINCH", 9) + 4, x1 - 34, y + 1)
        T(c, x1, y, num(pr), "MINCH", 9, align="r", color=pencil)
        y -= 13
        hline(c, x0, y + 5, x1, w=0.3)
    y -= 4
    T(c, x0, y, "合計", "MINCH", 10, color=pencil)
    T(c, x1, y, "九百七十円", "MINCH", 10.5, align="r", color=pencil)
    y -= 13
    T(c, x1, y, "(¥970 現金)", "MINCH", 7, align="r", color=pencil)
    y -= 9
    hline(c, x0, y, x1, w=0.4)
    # wobbly underline under total area
    wavy(c, x0, x1, y + 6, amp=0.8, period=9)
    y -= 12
    T(c, x0, y, "ありがとうございました", "MINCH", 8.5, color=pencil)
    stamp_circle(c, x1 - 12, y + 3, 10.5, "田", size=9)
    y -= 18
    T(c, x0, y, "※ 消費税は内税(自家計算)", "GOTH", 5.8, color=GRAY_TX)
    y -= 10
    T(c, x0, y, "テスト市架空町の青空市場内", "GOTH", 5.8, color=GRAY_TX)
    T(c, x1, 12, "架空テスト", "GOTH", 5.4, align="r", color=GRAY_TX)


# ============================================================== manifest ====
CASES = [
    ("V001", P(58, 190), draw_v001, "コンビニ58mmサーマル領収書",
     "kimi-01",
     "58mm細長サーマル。丸ロゴ、破線ルール、税率サマリ、預り/釣、フッタのバーコードという典型的縦積みPOS型。",
     "thermal_receipt", "コンビニ みちのく", "2023-11-02",
     "2023/11/2(木) 19:41", 1760, ["消耗品費"], "propose",
     "社内の乾電池・コピー用紙・クリアファイル購入。用途は業務用だが、購入者や部門の記載がない。",
     ["業務用途の確認(備品購入として問題ないか)", "部門・購入者の特定"],
     ["58mm thermal", "change amount", "10% single rate", "barcode"],
     [{"name": "単3乾電池 4本パック", "gross": 580, "rate": "10"},
      {"name": "A4コピー用紙 500枚入", "gross": 780, "rate": "10"},
      {"name": "クリアファイル A4 2枚組", "gross": 400, "rate": "10"}]),
    ("V002", P(80, 240), draw_v002, "スーパー2税率マルチ買い物籠",
     "kimi-02",
     "80mm超長尺。黒バナー店名、★印の8%食料群と10%雑貨群の2グループ、税率別サマリ、黒帯の大きな合計、アンケート枠。",
     "thermal_receipt", "スーパー いちばん星", "2024-03-16",
     "R6.3.16(土) 10:12", 6964, [], "hold",
     "食料品中心だが日用雑貨混在。会社の日用品か私的買い物か判断材料がない混在籠。",
     ["購入目的(社内消耗品か私的か)", "雑貨部分の業務関連性"],
     ["two VAT rates", "star-marked 8% items", "member number", "survey box"],
     [{"name": "食料品11点", "gross": 4251, "rate": "8"},
      {"name": "日用雑貨7点", "gross": 2713, "rate": "10"}]),
    ("V003", P(148, 105), draw_v003, "書店の縦書き帳付き御買上票",
     "kimi-03",
     "A6横。左端の灰色帯に縦書き店名、横罫のみの元帳式明細(品番/書名/数量/金額)、二重枠の総合計、下部に返品ポリシー枠。",
     "voucher", "本と文具 ことば舎", "2021-06-30",
     "令和3年6月30日(水)", 4290, ["図書費", "研修費"], "hold",
     "事務所向けに書籍を購入。専門書2冊で業務性が高いが、図書費か研修費か使途の裏付けがない。",
     ["書籍の業務用途の所在確認", "管理部署の確認"],
     ["vertical store strip", "ledger ruling", "era date", "return policy box"],
     [{"name": "実践データ整備術 第2版", "gross": 2420, "rate": "10"},
      {"name": "店舗数字の読み方 入門", "gross": 1870, "rate": "10"}]),
    ("V004", P(297, 210), draw_v004, "電器店大型モニタ領収(横A5)",
     "kimi-04",
     "A5横。黒バナーヘッダ、縦罫のある仕様表、右側に保証案内+擬似QRパネル、ページ全幅の黒価格帯で金額強調。",
     "thermal_receipt", "電器専門店 デンキノハコ", "2022-12-14",
     "2022-12-14 16:55", 91280, [], "asset_review",
     "業務用27型モニタ購入。金額が大きく固定資産・備品扱いの検討が必要。購入部門の記載なし。",
     ["資産計上区分の確認", "稟議・承認の有無", "設置場所・利用部門"],
     ["landscape", "spec grid", "warranty QR", "full-width price band"],
     [{"name": "27型業務用モニタ", "gross": 89800, "rate": "10"},
      {"name": "HDMIケーブル 2m", "gross": 1480, "rate": "10"}]),
    ("V005", P(100, 210), draw_v005, "資材センター納品兼領収(写)",
     "kimi-05",
     "100x210mm。No.2写の表示、発行日/納品日の2日付、品番・数量・単位・単価・金額の全罫グリッド、空行2行、受領印と備考の受け渡し型。",
     "invoice_receipt", "資材センター たてものや", "2020-08-24",
     "2020/08/24", 11008, ["修繕費", "消耗品費"], "propose",
     "工務現場向け資材納品。納品書兼領収で現金同時領収とあるが、写伝票であり原本確認が望ましい。",
     ["原本との照合", "現場名と発注者の一致", "修繕/消耗の区分"],
     ["delivery-cum-receipt", "issue vs delivery dates", "unit price columns",
      "blank ruled rows", "receipt stamp"],
     [{"name": "2x4材 6尺", "gross": 3184, "rate": "10"},
      {"name": "木ネジ 38mm 箱", "gross": 1080, "rate": "10"},
      {"name": "水性ペンキ 白 2L", "gross": 5640, "rate": "10"},
      {"name": "平刷毛 40mm", "gross": 624, "rate": "10"},
      {"name": "養生テープ 50m", "gross": 480, "rate": "10"}]),
    ("V006", P(76, 200), draw_v006, "画材店の縦書き御買上証(年省略)",
     "kimi-06",
     "76x200mm縦型。角丸二重枠、右から左への縦書き明細列、漢数字中央総計帯。日付は漢数字で「六月四日」のみ(年省略)。",
     "voucher", "画材 ときわ堂", "",
     "六月四日", 9000, [], "hold",
     "広告・販促用の画材購入か私的趣味か不明。年が省略されているため会計年度を確定できない。",
     ["購入年の確認", "業務用途の有無"],
     ["vertical item columns", "kanji numerals", "year omitted", "double frame"],
     [{"name": "キャンバスF6×五", "gross": 3100, "rate": "unknown"},
      {"name": "アクリル絵具十二色", "gross": 4620, "rate": "unknown"},
      {"name": "平筆二点", "gross": 960, "rate": "unknown"},
      {"name": "パレットナイフ", "gross": 320, "rate": "unknown"}]),
    ("V007", P(180, 90), draw_v007, "文具店ミシン目控え付き領収書",
     "kimi-07",
     "180x90mm横。左にミシン目付き控え(金額・済印)、本券は宛名罫線、ドットリーダー付き明細、合計枠を丸印が跨ぐ構成。",
     "voucher", "文具 ほしぞら堂", "2019-05-07",
     "令和 元年 5月 7日", 4422, ["消耗品費"], "propose",
     "封筒・朱肉・ゴム印・のりの購入。消耗品費が自然だがゴム印特注の資産性も軽微検討可能。",
     ["特注ゴム印の管理区分確認"],
     ["perforated stub", "dotted leaders", "overlapping seal",
      "addressee line"],
     [{"name": "長3封筒 100枚入", "gross": 660, "rate": "8"},
      {"name": "朱肉 大", "gross": 1100, "rate": "8"},
      {"name": "ゴム印(氏名印) 特注", "gross": 2200, "rate": "8"},
      {"name": "スティックのり 小 ×3", "gross": 462, "rate": "8"}]),
    ("V008", P(72, 152), draw_v008, "ベーカリー持ち帰り価格札式",
     "kimi-08",
     "72x152mm。角丸カード外枠、パンのモチーフ、明細は2列の値札タイル(品名+単価)、波線の装飾、下部に日時と釣銭。",
     "thermal_receipt", "ベーカリー ひだまり", "2024-09-09",
     "2024/9/9(月) 09:02", 2276, [], "hold",
     "パンとコーヒーの購入。社内朝食・会議用か私食かの区別がつかない飲食系。",
     ["購入目的(会議用・福利か私食か)"],
     ["price-tag tiles", "takeout 8%", "wavy rule", "bread motif"],
     [{"name": "パン4品・飲料1品", "gross": 2276, "rate": "8"}]),
    ("V009", P(90, 150), draw_v009, "カフェ店内/持帰り税率併記会計票",
     "kimi-09",
     "90x150mm。同一票内で10%店内パネルと8%持帰りパネルを左右並置、それぞれ小計と内税を持ち、統合二重枠の総合計。",
     "thermal_receipt", "カフェ つきよみ", "2025-01-12",
     "2025年1月12日(日) 12:44", 3052, [], "hold",
     "飲食で店内+持帰り混在。接待・会議・私用の判別不能。日曜利用でもある。",
     ["利用目的と同伴者の確認", "休日利用の業務性"],
     ["side-by-side rate panels", "two VAT rates", "table/staff meta",
      "combined total band"],
     [{"name": "店内飲食2品", "gross": 1738, "rate": "10"},
      {"name": "持ち帰り2品", "gross": 1314, "rate": "8"}]),
    ("V010", P(100, 160), draw_v010, "衣料店ハンギングタグ式明細票",
     "kimi-10",
     "100x160mm。紐付きパンチ穴のハンギングタグ型店舗ヘッダ、サイズ/色の衣料列、右下に縦積みの支払ブロック。",
     "thermal_receipt", "ワーク&タウン みらい店", "2023-04-05",
     "2023-04-05(水) 14:26", 10500, [], "hold",
     "ポロシャツ・作業手袋・エプロン。制服・作業着なら業務性ありだが、会社指定か私物か不明。",
     ["制服/作業着としての支給か私物か", "購入主体の確認"],
     ["hanging-tag header", "size/color columns", "right tender stack",
      "punched hole"],
     [{"name": "ポロシャツ(ロゴ無) M×3", "gross": 5970, "rate": "10"},
      {"name": "作業用グローブ L×5", "gross": 1950, "rate": "10"},
      {"name": "ショートエプロン ×2", "gross": 2580, "rate": "10"}]),
    ("V011", P(297, 148), draw_v011, "花屋の納品書(後日請求・未入金)",
     "kimi-11",
     "はがき横。二重枠、届け先BOXと件名、明細表、破線帯で「後日請求・未入金=支払の証でない」ことを明示、受取印欄。",
     "statement", "フラワー&グリーン しきさい", "2024-05-22",
     "納品日 2024年5月22日", 6600, [], "block",
     "オフィス装花の納品書。代金は後日請求で、本書は支払い証明ではない(立替計上不可・請求書待ち)。",
     ["正式請求書の到着確認", "装花の契約・発注形態"],
     ["unpaid notice band", "recipient box", "issue vs delivery dates",
      "leaf motif", "no payment proof"],
     [{"name": "観葉植物 パキラ L", "gross": 4000, "rate": "10"},
      {"name": "陶器鉢 14号", "gross": 2200, "rate": "10"},
      {"name": "鉢受け皿", "gross": 400, "rate": "10"}]),
    ("V012", P(130, 95), draw_v012, "青果店プライスボード式手書き風",
     "kimi-12",
     "130x95mm横。3列のプライスボードタイル(単価@・個数併記)、太マーカー風品名、楕円の手書き合計、「現金のみ」の斜め押印。",
     "thermal_receipt", "八百屋 あづまや", "2024-10-03",
     "2024年10月3日(木) 11:20", 3534, [], "hold",
     "青果の購入。社用イベント/福利の食材か私用か不明で典型的な混在候補。",
     ["購入目的の確認"],
     ["price-board tiles", "unit @ price", "oval kanji total",
      "cash-only stamp"],
     [{"name": "青果6品", "gross": 3534, "rate": "8"}]),
    ("V013", P(80, 150), draw_v013, "給油所リットル明細+ミシン目領収",
     "kimi-13",
     "80x150mm。リットル×単価の罫線表、セルフ/給油機メタ、下部ミシン目の下に独立したお客様控え領収書(丸印付)。",
     "thermal_receipt", "スタンド ながれやま給油所", "2023-08-11",
     "2023/08/11(祝) 08:31", 6091, ["車両費"], "hold",
     "ハイオク32.4L給油。社用車か私用車か、車番記載なし。祝日早朝の給油で業務性確認が必要。",
     ["利用車両の確認", "祝日利用の業務経路"],
     ["liters x unit price", "self-service meta", "perforated stub receipt"],
     [{"name": "ハイオク 32.4L", "gross": 6091, "rate": "10"}]),
    ("V014", P(210, 148), draw_v014, "中古機器店ランク保証明細領収",
     "kimi-14",
     "A5縦。赤楕円の中古品スタンプ、ランク/保証列つき状態明細+各品メモ、規約BOXと四角い店長印、二重枠の購入額。",
     "voucher", "中古機器 りさいくる市場", "2021-11-19",
     "2021年11月19日", 60000, [], "asset_review",
     "中古プリンタとチェアの購入。中古でも資産性(減価償却/一括償却等)の判定が要る金額。購入部署記載なし。",
     ["資産計上の要否(中古資産の扱い)", "設置部門の確認"],
     ["used-goods oval stamp", "grade/warranty columns", "terms box",
      "square clerk seal"],
     [{"name": "モノクロレーザープリンタ", "gross": 48000, "rate": "10"},
      {"name": "オフィスチェア(布張)", "gross": 12000, "rate": "10"}]),
    ("V015", P(210, 297), draw_v015, "卸売業請求兼領収(登録番号付)",
     "kimi-15",
     "A4縦。宛名罫線+件名、二重枠の請求金額、発行者ブロック(T0000000000000)と印、グレー帯の税抜グリッド、振込先BOX、着金領収帯に丸印。",
     "invoice_receipt", "卸売 オフィスサプライ山川", "2024-09-30",
     "2024年9月25日発行/9月30日着金", 81840, ["消耗品費"], "propose",
     "事務消耗品(用紙・トナー)の月次請求で振込着金確認済の兼用帳票。支払証憑性が高い。",
     ["月次契約との整合", "トナー純正/互換の管理区分"],
     ["T-number block", "bank details", "paid-confirmation band",
      "gray banded grid", "ex-tax columns"],
     [{"name": "コピー用紙 A4 10箱", "gross": 32780, "rate": "10"},
      {"name": "純正トナー 4個", "gross": 43120, "rate": "10"},
      {"name": "OAマルチクリーナー 12個", "gross": 5940, "rate": "10"}]),
    ("V016", P(210, 297), draw_v016, "EC注文完了画面(ブラウザ風)",
     "kimi-16",
     "A4縦。ブラウザウィンドウ枠+タイトルバー、アドレスバー、影付き注文カード、注文→出荷ステータストラッカー、マスク済カード行。",
     "other", "テストショップ(架空EC)", "2024-07-18",
     "2024-07-18 22:41", 7610, ["消耗品費", "印刷製本費"], "propose",
     "ECでプリンタ用互換テープと名刺印刷を購入、クレカ決済完了。名刺の名義人等の確認が必要。",
     ["名刺の名義人・配布部数の確認", "カード明細との照合"],
     ["browser frame", "order status tracker", "masked card",
      "shadow card", "delivery dates"],
     [{"name": "互換テープカートリッジ 10本", "gross": 4860, "rate": "10"},
      {"name": "名刺印刷 100枚", "gross": 2200, "rate": "10"},
      {"name": "配送料", "gross": 550, "rate": "10"}]),
    ("V017", P(80, 140), draw_v017, "クーポン値引併用レシート",
     "kimi-17",
     "80x140mm。上部にハサミ破線のクーポン帯、値引前小計の打ち消し線、値引行(赤字)、再計算後の黒帯合計という価格修正構造。",
     "thermal_receipt", "ドラッグストア くすりのもり", "2022-06-30",
     "2022年6月30日(木) 15:08", 1690, ["消耗品費"], "propose",
     "文房具と除菌ジェルをクーポン適用で購入。業務用消耗品の可能性が高いが購入者記載なし。",
     ["購入者・使用部門の確認"],
     ["coupon strip", "strikethrough subtotal", "discount row",
      "recalculated total"],
     [{"name": "文具・衛生用品4点", "gross": 1790, "rate": "10"},
      {"name": "クーポン値引", "gross": -100, "rate": "10"}]),
    ("V018", P(90, 170), draw_v018, "ポイント併用レシート(充当/付与)",
     "kimi-18",
     "90x170mm。左右2列のポイントパネル(利用500Pと付与38P)、明細は小さく横に、ポイント充当後の支払額、残高の推移表示。",
     "thermal_receipt", "ホームセンター きぼう館", "2025-02-14",
     "2025/2/14(金) 18:52", 3274, ["消耗品費"], "propose",
     "電池・電球等の消耗品購入。ポイント充当500円は値引相当で支払額は現金3,274円。会員帰属の確認が必要。",
     ["ポイント会員の帰属(会社カードか個人か)",
      "ポイント充当分の経理処理方針"],
     ["points redeemed and earned", "balance progression",
      "two-column points panel", "partial point payment"],
     [{"name": "乾電池ほか消耗品5点", "gross": 3774, "rate": "10"},
      {"name": "ポイント充当", "gross": -500, "rate": "10"}]),
    ("V019", P(80, 150), draw_v019, "返品返金伝票(マイナス金額)",
     "kimi-19",
     "80x150mm。赤帯のRETURNヘッダ、赤字マイナス明細、元伝票参照、承認印と受取印欄、返金帯。収入(支出)証明ではない。",
     "refund", "電器専門店 デンキノハコ", "2024-07-25",
     "2024-07-25 13:10", -1480, [], "block",
     "V004系の購入品一部返品の返金記録。新たな支出ではなく売上戻し/返金のため経費計上対象外。",
     ["元取引の費用計上と相殺処理の確認", "返金受領の証跡"],
     ["negative amounts", "red return band", "original slip reference",
      "acknowledgement stamps"],
     [{"name": "HDMIケーブル返品", "gross": -1480, "rate": "10"}]),
    ("V020", P(74, 124), draw_v020, "手書き風個人商店売上控(年省略)",
     "kimi-20",
     "74x124mm。生成り紙に薄い明朝手書き風、罫線入りの手書き明細、漢数字合計+波線、丸い名字印。日付は「五月十九日」で年省略。",
     "voucher", "八百 たなか", "",
     "五月十九日", 970, [], "hold",
     "青果購入の簡易売上控。年がなく確定不能、用途も私食/業務いずれとも取れる。",
     ["購入年の確認", "用途確認", "簡易控の原本性"],
     ["handwritten style", "kanji date and total", "year omitted",
      "kraft paper", "name seal"],
     [{"name": "ごぼう2束", "gross": 300, "rate": "unknown"},
      {"name": "みょうが1袋", "gross": 150, "rate": "unknown"},
      {"name": "青梅1kg", "gross": 520, "rate": "unknown"}]),
]


def build_manifest(outdir):
    items = []
    for (cid, size, fn, title, lid, ldesc, kind, merchant, date,
         date_printed, total, acct, decision, ctx, qs, feats,
         line_items) in CASES:
        items.append({
            "case_id": cid,
            "file": "individual/%s.pdf" % cid,
            "title": title,
            "layout_id": lid,
            "layout_description": ldesc,
            "document_kind": kind,
            "merchant": merchant,
            "date": date,
            "date_printed": date_printed,
            "total": total,
            "acceptable_accounts": acct,
            "expected_decision": decision,
            "human_context": ctx,
            "review_questions": qs,
            "features": feats,
            "items": line_items,
        })
    return items


def generate_all(outdir):
    register_fonts()
    ind = os.path.join(outdir, "individual")
    os.makedirs(ind, exist_ok=True)
    for (cid, size, fn, *_rest) in CASES:
        c = canvas.Canvas(os.path.join(ind, "%s.pdf" % cid), pagesize=size)
        fn(c, size)
        c.showPage()
        c.save()
    manifest = build_manifest(outdir)
    with open(os.path.join(outdir, "manifest-fragment.json"),
              "w", encoding="utf-8") as fh:
        json.dump(manifest, fh, ensure_ascii=False, indent=2)


def main():
    ap = argparse.ArgumentParser(
        description="Kimi author: generate V001-V020 receipt PDFs")
    ap.add_argument("--output", required=True,
                    help="absolute output directory")
    args = ap.parse_args()
    outdir = os.path.abspath(args.output)
    generate_all(outdir)


if __name__ == "__main__":
    main()
