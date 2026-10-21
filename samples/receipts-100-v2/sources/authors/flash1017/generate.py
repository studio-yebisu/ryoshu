# -*- coding: utf-8 -*-
"""
Independent receipt-page author: DeepSeek V4.1 Flash 1017
Assigned cases V061-V080, layout flash1017-01..20.

Entry point:
    python generate.py --output <absolute-dir>

Writes <output>/individual/V0xx.pdf (one page each) and
<output>/manifest-fragment.json (UTF-8 JSON array).

Only reportlab + stdlib. No network, no subprocess, no writes outside output.
"""

import argparse
import json
import os
import sys

from reportlab.pdfgen import canvas
from reportlab.lib.pagesizes import A4, A5, B5
from reportlab.lib.units import mm
from reportlab.pdfbase import pdfmetrics
from reportlab.pdfbase.ttfonts import TTFont
from reportlab.lib.colors import Color, black, white, HexColor

AUTHOR = "flash1017"
FONT_DIR = "C:/Windows/Fonts"

# ---------------------------------------------------------------- fonts ----

_FONTS = {}


def _candidate_files():
    # (family key, filename, subfont)
    return [
        ("gothic", "msgothic.ttc", 0),
        ("gothic_b", "msgothic.ttc", 0),
        ("mincho", "msmincho.ttc", 0),
        ("meiryo", "meiryo.ttc", 0),
        ("yu_g", "YuGothM.ttc", 0),
        ("yu_gb", "YuGothB.ttc", 0),
        ("yu_m", "YuMincho.ttc", 0),
    ]


def register_fonts():
    """Register available Japanese fonts; remember which ones worked."""
    for key, fname, idx in _candidate_files():
        path = os.path.join(FONT_DIR, fname)
        regname = "JP_" + key
        if regname in pdfmetrics.getRegisteredFontNames():
            _FONTS[key] = regname
            continue
        if not os.path.isfile(path):
            continue
        try:
            pdfmetrics.registerFont(TTFont(regname, path, subfontIndex=idx))
            _FONTS[key] = regname
        except Exception:
            continue
    # Fallbacks so nothing breaks if a font is missing.
    gothic_fb = _FONTS.get("gothic") or _FONTS.get("meiryo") or _FONTS.get("yu_g")
    if gothic_fb is None:
        raise RuntimeError("No Japanese font could be registered from " + FONT_DIR)
    for key in ("gothic", "gothic_b", "mincho", "meiryo", "yu_g", "yu_gb", "yu_m"):
        _FONTS.setdefault(key, gothic_fb)


def F(key):
    return _FONTS[key]


# ------------------------------------------------------------- helpers ----

def tw(c, text, font, size):
    """Text width in points."""
    return c.stringWidth(text, font, size)


def draw_text(c, x, y, text, font, size, color=black, align="left"):
    """Draw single line text. align: left|center|right. Baseline y."""
    c.setFont(font, size)
    c.setFillColor(color)
    w = tw(c, text, font, size)
    if align == "center":
        x -= w / 2.0
    elif align == "right":
        x -= w
    c.drawString(x, y, text)
    return w


def wrap_text(c, text, font, size, max_w):
    """Simple char-based wrapping (Japanese safe). Returns list of lines."""
    lines = []
    cur = ""
    for ch in text:
        if ch == "\n":
            lines.append(cur)
            cur = ""
            continue
        trial = cur + ch
        if tw(c, trial, font, size) <= max_w:
            cur = trial
        else:
            if cur == "":
                cur = ch
            else:
                lines.append(cur)
                cur = ch
    if cur or not lines:
        lines.append(cur)
    return lines


def draw_paragraph(c, x, y, text, font, size, max_w, leading,
                   color=black, align="left", max_lines=None):
    """Draw wrapped text downward from baseline y. Returns new baseline y
    (pointing at last drawn line)."""
    lines = wrap_text(c, text, font, size, max_w)
    if max_lines is not None and len(lines) > max_lines:
        lines = lines[:max_lines]
        if lines:
            lines[-1] = lines[-1][:-1] + "…"
    yy = y
    for ln in lines:
        draw_text(c, x, yy, ln, font, size, color, align)
        yy -= leading
    return yy + leading  # baseline of last line


def draw_h_line(c, x1, y, x2, width=1.0, color=black, dash=None):
    c.setStrokeColor(color)
    c.setLineWidth(width)
    if dash:
        c.setDash(dash)
        c.line(x1, y, x2, y)
        c.setDash()
    else:
        c.line(x1, y, x2, y)
    c.setLineWidth(1)


def draw_v_line(c, x, y1, y2, width=1.0, color=black, dash=None):
    c.setStrokeColor(color)
    c.setLineWidth(width)
    if dash:
        c.setDash(dash)
        c.line(x, y1, x, y2)
        c.setDash()
    else:
        c.line(x, y1, x, y2)
    c.setLineWidth(1)


def draw_rect(c, x, y, w, h, stroke=black, fill=None, width=1.0, radius=None):
    c.setLineWidth(width)
    c.setStrokeColor(stroke if stroke is not None else None)
    if fill is not None:
        c.setFillColor(fill)
    if radius:
        c.roundRect(x, y, w, h, radius, stroke=1 if stroke is not None else 0,
                    fill=1 if fill is not None else 0)
    else:
        c.rect(x, y, w, h, stroke=1 if stroke is not None else 0,
               fill=1 if fill is not None else 0)
    c.setLineWidth(1)


def draw_box(c, x, y, w, h, fill_color, stroke_color=None, width=1.0, radius=4):
    c.saveState()
    c.setFillColor(fill_color)
    if stroke_color is not None:
        c.setStrokeColor(stroke_color)
        c.setLineWidth(width)
        if radius:
            c.roundRect(x, y, w, h, radius, stroke=1, fill=1)
        else:
            c.rect(x, y, w, h, stroke=1, fill=1)
    else:
        if radius:
            c.roundRect(x, y, w, h, radius, stroke=0, fill=1)
        else:
            c.rect(x, y, w, h, stroke=0, fill=1)
    c.restoreState()


def draw_seal(c, cx, cy, r, text, color, font_size=5.0,
              double_ring=True, text_color=None):
    """Circular seal. Text distributed around ring or center."""
    c.saveState()
    c.setStrokeColor(color)
    c.setFillColor(color)
    c.setLineWidth(max(1.2, r * 0.10))
    c.circle(cx, cy, r, stroke=1, fill=0)
    if double_ring:
        c.setLineWidth(max(0.4, r * 0.04))
        c.circle(cx, cy, r * 0.86, stroke=1, fill=0)
    if text:
        tc = text_color if text_color else color
        # place characters in two vertical columns for 4-6 chars, else center
        chars = list(text)
        n = len(chars)
        fnt = F("mincho")
        if n <= 2:
            c.setFont(fnt, r * 0.55)
            c.setFillColor(tc)
            if n == 1:
                c.drawCentredString(cx, cy - r * 0.22, text)
            else:
                w = tw(c, text, fnt, r * 0.55)
                c.drawString(cx - w / 2, cy - r * 0.2, text)
        else:
            # two columns right-to-left
            half = (n + 1) // 2
            cols = [chars[:half], chars[half:]]
            col_x = [cx + r * 0.30, cx - r * 0.36]
            for ci, col in enumerate(cols):
                if not col:
                    continue
                fs = min(font_size + r * 0.12, r * 0.5)
                line_h = fs * 1.02
                total = line_h * len(col)
                y0 = cy + total / 2 - fs * 0.85
                for i, ch in enumerate(col):
                    c.setFont(fnt, fs)
                    c.setFillColor(tc)
                    c.drawCentredString(col_x[ci] + fs / 2 - 1, y0 - i * line_h, ch)
    c.restoreState()


def draw_stamp_rect(c, x, y, w, h, text1, text2, color, size=7):
    """Rectangular stamp (角印風)."""
    c.saveState()
    c.setStrokeColor(color)
    c.setFillColor(color)
    c.setLineWidth(1.6)
    c.rect(x, y, w, h, stroke=1, fill=0)
    c.setLineWidth(0.6)
    c.rect(x + 2.2, y + 2.2, w - 4.4, h - 4.4, stroke=1, fill=0)
    fnt = F("mincho")
    if text2:
        draw_text(c, x + w / 2, y + h * 0.68, text1, fnt, size, color, "center")
        draw_text(c, x + w / 2, y + h * 0.24, text2, fnt, size * 0.85, color, "center")
    else:
        lines = text1.split("\n")
        n = len(lines)
        lh = (h - 8) / max(n, 1)
        fs = min(size, lh * 0.8)
        for i, ln in enumerate(lines):
            draw_text(c, x + w / 2, y + h - 6 - (i + 0.2) * lh, ln, fnt, fs,
                      color, "center")
    c.restoreState()


def fake_hand_line(c, x, y, text, font_size=12, color=black, slant=0.15,
                   jitter_seed=1):
    """Draw a pseudo-handwritten line by per-char horizontal jitter and slant."""
    import math
    c.saveState()
    c.setFillColor(color)
    xpos = x
    seed = jitter_seed
    for i, ch in enumerate(text):
        seed = (seed * 1103515245 + 12345) & 0x7FFFFFFF
        jy = ((seed >> 16) % 7 - 3) * 0.35
        jx = ((seed >> 8) % 5 - 2) * 0.25
        fs = font_size * (1.0 + ((seed >> 12) % 5 - 2) * 0.015)
        c.saveState()
        c.translate(xpos + jx, y + jy)
        c.rotate((seed % 9) - 4)
        c.setFont(F("yu_g"), fs)
        c.drawString(0, 0, ch)
        c.restoreState()
        xpos += tw(c, ch, F("yu_g"), fs) + slant
    c.restoreState()
    return xpos


def money(n):
    return "{:,}".format(int(n))


def yen(n):
    return "¥" + money(n)


class Layout:
    """Convenience container for one page with collision tracking."""

    def __init__(self, c, w, h):
        self.c = c
        self.w = w
        self.h = h
        self.boxes = []  # (x0,y0,x1,y1,label)

    def claim(self, x0, y0, x1, y1, label):
        self.boxes.append((min(x0, x1), min(y0, y1), max(x0, x1), max(y0, y1), label))

    def text(self, x, y, s, font, size, color=black, align="left", label=""):
        wtxt = tw(self.c, s, font, size)
        if align == "center":
            cx0 = x - wtxt / 2
        elif align == "right":
            cx0 = x - wtxt
        else:
            cx0 = x
        draw_text(self.c, x, y, s, font, size, color, align)
        if label:
            self.claim(cx0, y - 1, cx0 + wtxt, y + size, label)
        return wtxt

    def para(self, x, y, s, font, size, max_w, leading, color=black,
             align="left", label=""):
        endy = draw_paragraph(self.c, x, y, s, font, size, max_w, leading,
                              color, align)
        if label:
            lines = wrap_text(self.c, s, font, size, max_w)
            for i, ln in enumerate(lines):
                wl = tw(self.c, ln, font, size)
                xx = x
                if align == "center":
                    xx = x - max_w / 2
                elif align == "right":
                    xx = x - wl
                yy = y - i * leading
                self.claim(xx, yy - 1, xx + wl, yy + size, label)
        return endy

    def collision_report(self):
        bad = []
        for i in range(len(self.boxes)):
            for j in range(i + 1, len(self.boxes)):
                a = self.boxes[i]
                b = self.boxes[j]
                ox = min(a[2], b[2]) - max(a[0], b[0])
                oy = min(a[3], b[3]) - max(a[1], b[1])
                if ox > 1.0 and oy > 1.0:
                    bad.append((a[4], b[4], round(ox, 1), round(oy, 1)))
        return bad


# ---------------------------------------------------------------- cases ----

def case_v061(c):
    """Print-shop detailed invoice, PAID, A5 landscape-ish custom size."""
    _register_fonts_done()
    W, H = 210 * mm, 160 * mm
    c.setPageSize((W, H))
    L = Layout(c, W, H)
    M = 12 * mm
    ink = HexColor("#1a2a3a")
    accent = HexColor("#0f5f8a")
    pale = HexColor("#eef3f7")

    # left vertical stripe
    draw_box(c, 0, 0, 9 * mm, H, accent, radius=0)
    c.saveState()
    c.translate(4.5 * mm, H * 0.62)
    c.rotate(90)
    draw_text(c, 0, 0, "印刷 請求書 / INVOICE", F("yu_g"), 8, white, "center")
    c.restoreState()

    # Title top-right big
    L.text(W - M, H - 20 * mm, "請求明細書", F("gothic"), 26, ink,
           "right", "title")
    L.text(W - M, H - 28 * mm, "No. PS-2601-118", F("yu_g"), 8.5, ink, "right",
           "invoice-no")
    L.text(W - M, H - 34 * mm, "発行日　2026年1月16日", F("yu_g"), 8, ink,
           "right", "issue-date")

    # Issuer block left
    L.text(M + 4 * mm, H - 16 * mm, "イラスト印刷工房 こもれび", F("gothic"), 13,
           ink, "left", "issuer")
    L.text(M + 4 * mm, H - 21 * mm, "架空町2-8 テスト市 印刷事業部", F("yu_g"), 7,
           HexColor("#555555"), "left")
    L.text(M + 4 * mm, H - 25.5 * mm, "登録番号 T0000000000000", F("yu_g"), 7,
           HexColor("#555555"), "left")

    # Recipient
    rx = M + 4 * mm
    ry = H - 42 * mm
    L.text(rx, ry, "テスト株式会社 営業部 御中", F("gothic"), 11, ink, "left",
           "recipient")
    L.text(rx, ry - 6 * mm, "下記のとおりご請求申し上げます。", F("mincho"), 7.5,
           ink, "left")
    L.text(rx, ry - 13 * mm, "ご請求金額", F("yu_g"), 8, ink, "left")
    L.text(rx + 20 * mm, ry - 14.5 * mm, "¥ 187,330 -", F("gothic"), 19, accent,
           "left", "grand-total")
    L.text(rx, ry - 20 * mm,
           "支払期限 2026年2月15日 / お支払済み（2026-02-03 着金）",
           F("yu_g"), 7, ink, "left")
    draw_box(c, rx, ry - 27 * mm, 92 * mm, 5 * mm, pale, accent, 0.8, 0)
    L.text(rx + 2 * mm, ry - 25.5 * mm, "入金確認済", F("yu_gb"), 8, accent,
           "left")

    # Table
    tx = M + 4 * mm
    ty = H - 74 * mm
    colx = [tx, tx + 8 * mm, tx + 70 * mm, tx + 84 * mm, tx + 100 * mm,
            tx + 121 * mm, tx + 141 * mm, W - M]
    heads = ["品目・内容 / 仕様", "数量", "単価", "金額（税抜）", "税区分"]
    draw_box(c, tx, ty - 1 * mm, W - M - tx, 6.5 * mm, accent, None, radius=0)
    # Five physical columns; specifications wrap below each item name.
    cols = [tx, tx + 62 * mm, tx + 75 * mm, tx + 92 * mm, tx + 128 * mm,
            W - M]
    for i, h in enumerate(heads):
        L.text(cols[i] + 1.5 * mm,
               ty + 1.1 * mm, h, F("yu_gb"), 7.2, white,
               "left")

    rows = [
        ("会社案内パンフレット", "A4 16P / マット紙 / 4色両面", 500, 168, 84000, "10%"),
        ("名刺（社員用）", "白ケント220kg / 片面 / 100枚×12名", 12, 2600, 31200, "10%"),
        ("封筒 角2", "長3色上質 / 名入れ印刷", 300, 55, 16500, "10%"),
        ("クリアファイル", "A4 / UV印刷", 200, 190, 38000, "10%"),
        ("納品書（複写）", "2枚複写 / 100組", 5, 720, 3600, "10%"),
    ]
    ry0 = ty - 7 * mm
    rh = 8 * mm
    for i, (name, spec, qty, unit, amt, taxc) in enumerate(rows):
        yy = ry0 - i * rh
        if i % 2 == 1:
            draw_box(c, tx, yy - 4.5 * mm, W - M - tx, rh, HexColor("#f7f9fb"),
                     None, radius=0)
        L.text(cols[0] + 1.5 * mm, yy, name, F("mincho"), 8, ink, "left")
        L.text(cols[0] + 1.5 * mm, yy - 3.4 * mm, spec, F("yu_g"), 6.3,
               HexColor("#666666"), "left")
        L.text(cols[1] + 1.5 * mm, yy, str(qty), F("yu_g"), 7.5, ink, "left")
        L.text(cols[2] + 1.5 * mm, yy, money(unit), F("yu_g"), 7.5, ink, "left")
        L.text(cols[4] - 1.5 * mm, yy, money(amt), F("yu_g"), 7.5, ink, "right")
        L.text(cols[4] + 1.5 * mm, yy, taxc, F("yu_g"), 7.2, ink)
    draw_h_line(c, tx, ry0 - len(rows) * rh + 2.2 * mm, W - M, 0.7, accent)

    # column separators
    for xx in cols[1:-1]:
        draw_v_line(c, xx, ty + 5.5 * mm, ry0 - len(rows) * rh + 2.2 * mm,
                    0.4, HexColor("#9bb5c6"))
    # first column vertical line between name/spec and qty stays; ok

    # Totals block bottom right
    bx = W - M - 78 * mm
    by = ry0 - len(rows) * rh - 3 * mm
    sums = [
        ("小計（10%対象）", 173, 300),
        ("値引（税抜）", -3, 000),
        ("消費税（値引後10%）", 17, 30),
        ("合計", 187, 330),
    ]
    yy = by
    for i, (lab, hi, lo) in enumerate(sums):
        val = hi * 1000 + lo
        bold = (i == 3)
        L.text(bx, yy, lab, F("yu_gb") if bold else F("yu_g"), 8 if not bold else 9,
               ink, "left")
        L.text(W - M, yy, money(val), F("gothic") if bold else F("yu_g"),
               10 if bold else 8, ink, "right")
        if bold:
            draw_h_line(c, bx, yy + 3.2 * mm, W - M, 1.2, accent)
        yy -= 6 * mm
    L.text(W - M, yy + 0.5 * mm,
           "（内 消費税 ¥17,030）", F("yu_g"), 6.5, HexColor("#777777"),
           "right")

    # Seal bottom-left
    draw_seal(c, M + 22 * mm, 26 * mm, 12 * mm, "こもれび印", HexColor("#c0392b"),
              font_size=7.2)
    draw_text(c, M + 4 * mm, 8 * mm, "架空テスト資料 / 振込口座：記録用ダミー",
              F("yu_g"), 6, HexColor("#888888"))

    # reconcile claim: grand total box vs table area
    L.claim(tx, ry0 - len(rows) * rh + 2.2 * mm, W - M, ty + 5.5 * mm,
            "table-region")
    rep = L.collision_report()
    _soft_check(rep, "V061")


def case_v062(c):
    """Business-card print quotation (見積書) NOT paid -> block. B5 portrait."""
    W, H = B5
    c.setPageSize(B5)
    L = Layout(c, W, H)
    M = 15 * mm
    green = HexColor("#2e5d3a")
    light = HexColor("#edf4ee")

    # top band
    draw_box(c, 0, H - 26 * mm, W, 26 * mm, green, radius=0)
    L.text(M, H - 15 * mm, "見 積 書", F("mincho"), 25, white, "left", "title")
    L.text(W - M, H - 11 * mm, "No. MJ-27-0441", F("yu_g"), 8.5, white, "right")
    L.text(W - M, H - 17 * mm, "見積番号 / 有効期限 2027年6月30日",
           F("yu_g"), 7, white, "right")
    L.text(W - M, H - 22 * mm, "見積日 2027年5月28日", F("yu_g"), 7.5, white,
           "right")

    # recipient box left
    draw_rect(c, M, H - 56 * mm, 78 * mm, 22 * mm, green, None, 0.8)
    L.text(M + 4 * mm, H - 40 * mm, "テスト商事株式会社", F("gothic"), 12, green,
           "left", "recipient")
    L.text(M + 4 * mm, H - 46 * mm, "総務部 御中", F("mincho"), 9, black,
           "left")
    L.text(M + 4 * mm, H - 52 * mm, "ご担当：山吹様", F("yu_g"), 7.5, black,
           "left")

    # issuer right
    ix = W - M
    L.text(ix, H - 36 * mm, "さくら製版印刷 株式会社", F("gothic"), 10.5, black,
           "right", "issuer")
    L.text(ix, H - 41 * mm, "テスト市架空町5-3-2 第2工場", F("yu_g"), 7,
           HexColor("#555555"), "right")
    L.text(ix, H - 45.5 * mm, "TEL 000-0000-0000（ダミー）", F("yu_g"), 7,
           HexColor("#555555"), "right")
    L.text(ix, H - 50 * mm, "登録番号 T0000000000000", F("yu_g"), 7,
           HexColor("#555555"), "right")

    # big estimate amount
    L.text(M, H - 68 * mm, "お見積金額（税込）", F("yu_g"), 8.5, black, "left")
    L.text(M, H - 80 * mm, "¥ 64,900", F("gothic"), 26, green, "left",
           "estimate-total")
    L.text(M + 60 * mm, H - 76 * mm, "（税抜 ¥59,000 + 税 ¥5,900）",
           F("yu_g"), 7.5, HexColor("#555555"), "left")
    draw_box(c, M, H - 89 * mm, 120 * mm, 6 * mm, HexColor("#fdecec"),
             HexColor("#b33a3a"), 1.0, 0)
    L.text(M + 2 * mm, H - 87.3 * mm,
           "※ 本見積書は金額の提示であり、領収書ではありません。",
           F("yu_gb"), 7, HexColor("#b33a3a"), "left")

    # table
    ty = H - 100 * mm
    tx = M
    tw_ = W - 2 * M
    colw = [46, 22, 15, 18, 23, (tw_ / mm) - 124]  # exactly fits B5 margins
    xs = [tx]
    for cw in colw:
        xs.append(xs[-1] + cw * mm)
    heads = ["品名 / 仕様", "用紙", "数量", "単価", "金額", "備考"]
    draw_box(c, tx, ty, tw_, 7 * mm, green, None, radius=0)
    for i, hd in enumerate(heads):
        L.text(xs[i] + 1.5 * mm, ty + 2.2 * mm, hd, F("yu_gb"), 7.2, white)
    items = [
        ("名刺 片面フルカラー", "ケント220", 1200, 38, 45600, "10%"),
        ("名刺 両面フルカラー", "ケント220", 300, 52, 15600, "10%"),
        ("箔押し加工（金）", "-", 300, 15, 4500, "10%"),
        ("データ校正費", "-", 1, 5000, 5000, "10%"),
        ("送料", "-", 1, 800, 800, "10%"),
    ]
    # adjust so subtotal = 59,000? make arithmetic right:
    # 45600+15600+4500+5000+800 = 71500 -> wrong. fix values:
    items = [
        ("名刺 片面フルカラー", "ケント220", 1200, 33, 39600, "10%"),
        ("名刺 両面フルカラー", "ケント220", 300, 45, 13500, "10%"),
        ("箔押し加工（金）", "-", 300, 10, 3000, "10%"),
        ("データ校正費", "-", 1, 2100, 2100, "10%"),
        ("送料", "-", 1, 800, 800, "10%"),
    ]  # 39600+13500+3000+2100+800 = 59000
    ry = ty - 8 * mm
    rh = 10.5 * mm
    for i, it in enumerate(items):
        yy = ry - i * rh
        if i % 2 == 1:
            draw_box(c, tx, yy - 2 * mm, tw_, rh, light, None, radius=0)
        L.text(xs[0] + 1.5 * mm, yy, it[0], F("mincho"), 8.2, black)
        L.text(xs[1] + 1.5 * mm, yy, it[1], F("yu_g"), 7.2, black)
        L.text(xs[2] + 1.5 * mm, yy, str(it[2]), F("yu_g"), 7.2, black)
        L.text(xs[3] + 1.5 * mm, yy, money(it[3]), F("yu_g"), 7.2, black)
        L.text(xs[5] - 1.5 * mm, yy, money(it[4]), F("yu_g"), 7.4, black,
               "right")
        L.text(xs[5] + 1.0 * mm, yy, it[5], F("yu_g"), 7, black)
        draw_h_line(c, tx, yy - 3.6 * mm, tx + tw_, 0.4,
                    HexColor("#b8ccbc"))
    for xx in xs[1:-1]:
        draw_v_line(c, xx, ty, ry - len(items) * rh + 1.6 * mm, 0.4,
                    HexColor("#b8ccbc"))

    # totals right below table
    by = ry - len(items) * rh - 6 * mm
    L.text(xs[4] - 1.5 * mm, by, "小計", F("yu_g"), 8, black, "right")
    L.text(xs[5] - 1.5 * mm, by, "59,000", F("yu_g"), 8, black, "right")
    L.text(xs[4] - 1.5 * mm, by - 6 * mm, "消費税（10%）", F("yu_g"), 8, black,
           "right")
    L.text(xs[5] - 1.5 * mm, by - 6 * mm, "5,900", F("yu_g"), 8, black, "right")
    draw_h_line(c, xs[4] + 2 * mm, by - 9 * mm, xs[5] - 1.5 * mm, 1, green)
    L.text(xs[4] - 1.5 * mm, by - 14 * mm, "合計（税込）", F("yu_gb"), 9.5,
           green, "right", "total")
    L.text(xs[5] - 1.5 * mm, by - 14 * mm, "¥64,900", F("gothic"), 12, green,
           "right", "total-val")

    # conditions
    L.text(M, by - 26 * mm, "納期：ご発注後 約10営業日 / 納入場所：貴社ご指定",
           F("yu_g"), 7.5, black)
    L.text(M, by - 32 * mm, "お支払条件：納品月末締め 翌月末払い（予定）",
           F("yu_g"), 7.5, black)
    L.text(M, by - 38 * mm,
           "備考：紙質・数量変更で金額が変動します。版下データはAI/PDF・CMYK。",
           F("yu_g"), 7, HexColor("#555555"))
    draw_seal(c, W - M - 16 * mm, by - 30 * mm, 13 * mm, "さくら製版",
              HexColor("#c04040"), font_size=6.5)
    draw_text(c, W - 30 * mm, 8 * mm, "架空テスト", F("yu_g"), 6,
              HexColor("#999999"))


def case_v063(c):
    """Monitor repair job card receipt. Narrow thermal width 80mm x 190mm."""
    W, H = 80 * mm, 190 * mm
    c.setPageSize((W, H))
    L = Layout(c, W, H)
    dark = HexColor("#222222")
    accent = HexColor("#d35400")
    M = 6 * mm

    # header
    L.text(W / 2, H - 14 * mm, "修理受付票・領収書", F("gothic"), 12, dark,
           "center", "title")
    L.text(W / 2, H - 19 * mm, "パソコン修理 みつばち電機", F("yu_g"), 8.5, dark,
           "center", "issuer")
    L.text(W / 2, H - 23.5 * mm, "テスト市架空町9-1-7", F("yu_g"), 6.5,
           HexColor("#666666"), "center")
    draw_h_line(c, M, H - 26 * mm, W - M, 0.8, dark)
    draw_h_line(c, M, H - 26.8 * mm, W - M, 0.4, dark)

    # job card fields
    fields = [
        ("受付番号", "R-2025-08311"),
        ("受付日", "2025年11月4日 14:20"),
        ("修理完了日", "2025年11月11日"),
        ("品名", "27インチ IPS モニター"),
        ("型番（記載）", "LCD-273WQ  ※型式は概算"),
        ("症状", "画面左半分が映らず / 点滅"),
        ("処置", "LCDパネル交換・内部清掃"),
        ("保証", "修理箇所 90日保証"),
    ]
    y = H - 32 * mm
    for k, v in fields:
        L.text(M, y, k, F("yu_gb"), 6.8, HexColor("#444444"), "left")
        L.text(M + 20 * mm, y, v, F("yu_g"), 7, dark, "left")
        y -= 6.2 * mm
    draw_h_line(c, M, y + 3 * mm, W - M, 0.6, HexColor("#aaaaaa"), dash=[1, 2])

    # cost table
    y -= 2 * mm
    L.text(M, y, "作業明細", F("yu_gb"), 8, accent, "left")
    y -= 5 * mm
    lines = [
        ("技術料", 8800),
        ("部品代 LCDパネル", 24000),
        ("部品代 バックライト", 1900),
        ("送料", 1100),
    ]
    for name, price in lines:
        L.text(M, y, name, F("yu_g"), 7.2, dark, "left")
        L.text(W - M, y, money(price), F("yu_g"), 7.2, dark, "right")
        y -= 5 * mm
    draw_h_line(c, M, y + 1.6 * mm, W - M, 0.4, dark, dash=[1, 1.5])
    y -= 1 * mm
    L.text(M, y, "小計（税抜）", F("yu_g"), 7, dark)
    L.text(W - M, y, "35,800", F("yu_g"), 7, dark, "right")
    y -= 5 * mm
    L.text(M, y, "消費税（10%）", F("yu_g"), 7, dark)
    L.text(W - M, y, "3,580", F("yu_g"), 7, dark, "right")
    y -= 4 * mm
    draw_h_line(c, M, y + 2 * mm, W - M, 1.2, dark)
    L.text(M, y - 3 * mm, "領収金額", F("yu_gb"), 9, dark)
    L.text(W - M, y - 4 * mm, "¥39,380", F("gothic"), 15, accent, "right",
           "total")

    # tender block
    y -= 18 * mm
    draw_box(c, M, y - 6 * mm, W - 2 * M, 16 * mm, HexColor("#fbf3ea"), accent,
             0.7, 2)
    L.text(M + 2 * mm, y + 6 * mm, "領収日 2025/11/11　現金", F("yu_g"), 7, dark,
           "left")
    L.text(M + 2 * mm, y + 1.5 * mm, "お預かり", F("yu_g"), 7, dark)
    L.text(W - M - 2 * mm, y + 1.5 * mm, "40,000", F("yu_g"), 7, dark, "right")
    L.text(M + 2 * mm, y - 2.5 * mm, "お釣り", F("yu_g"), 7, dark)
    L.text(W - M - 2 * mm, y - 2.5 * mm, "620", F("yu_gb"), 8.5, accent,
           "right")
    L.text(M, y - 12 * mm, "※ 再修理は保証期間内でも有償の場合があります。",
           F("yu_g"), 5.8, HexColor("#666666"))
    L.text(M, y - 16 * mm, "※ 部品の取り寄せは完了しています。",
           F("yu_g"), 5.8, HexColor("#666666"))

    # dotted tear line and copy stamp
    draw_h_line(c, M, y - 24 * mm, W - M, 0.5, HexColor("#888888"),
                dash=[2, 2])
    draw_stamp_rect(c, W - M - 22 * mm, y - 40 * mm, 20 * mm, 13 * mm,
                    "領収", "済", accent, 8)
    fake_hand_line(c, M + 2 * mm, y - 38 * mm, "受付 佐々木", 9, dark,
                   jitter_seed=7)
    draw_text(c, W / 2, 6 * mm, "架空テスト", F("yu_g"), 6,
              HexColor("#999999"), "center")


def case_v064(c):
    """Computer custom-build parts asset review. A4 portrait, two-column parts list."""
    c.setPageSize(A4)
    W, H = A4
    L = Layout(c, W, H)
    M = 16 * mm
    navy = HexColor("#1f2d4a")
    cyan = HexColor("#1b8ba8")
    strip = HexColor("#f0f4f8")

    # left gutter with vertical meta
    draw_box(c, 0, 0, 13 * mm, H, navy, radius=0)
    c.saveState()
    c.translate(6.5 * mm, H * 0.5)
    c.rotate(90)
    draw_text(c, 0, 0, "PC構成品 購入明細 / SALE DETAILS",
              F("yu_g"), 7, white, "center")
    c.restoreState()

    # top area: big title + purchase info
    L.text(M + 4 * mm, H - 18 * mm, "パーツ購入明細・領収書", F("gothic"), 19, navy,
           "left", "title")
    L.text(M + 4 * mm, H - 25 * mm, "自作PC 構成部品 一式", F("mincho"), 10,
           HexColor("#555555"), "left")
    L.text(W - M, H - 18 * mm, "購入日 2024-03-09", F("yu_g"), 8, navy,
           "right")
    L.text(W - M, H - 23 * mm, "伝票番号 PB-4402-917", F("yu_g"), 8, navy,
           "right")

    # store info row
    draw_box(c, M + 4 * mm, H - 35 * mm, 74 * mm, 8 * mm, strip, None, radius=2)
    L.text(M + 6 * mm, H - 32 * mm, "販売：架空パーツ館 テスト店", F("yu_g"), 7.5,
           navy)
    L.text(W - M - 72 * mm, H - 32 * mm, "お支払：クレジット一括", F("yu_g"), 7.5,
           navy)

    # section A: parts table (full width, tall rows)
    ty = H - 44 * mm
    tx = M + 4 * mm
    avail = W - M - tx
    c1 = tx
    c2 = tx + 40 * mm
    c3 = tx + 102 * mm
    c4 = tx + 114 * mm
    c5 = tx + 137 * mm
    c6 = W - M
    head_y = ty
    draw_box(c, c1, head_y, avail, 8 * mm, navy, radius=0)
    for x, t in [(c1, "部品カテゴリ"), (c2, "型番・仕様"), (c3, "数量"),
                 (c4, "単価"), (c5, "金額"), (c6, "備考")]:
        L.text(x + (2 * mm if x != c6 else -2 * mm), head_y + 2.6 * mm, t,
               F("yu_gb"), 7.5, white, "left" if x != c6 else "right")
    parts = [
        ("CPU", "架空CPU X9-7800 3.9GHz", 1, 62800, 62800, "10%"),
        ("マザーボード", "ATX T790 チップセット（架空）", 1, 34800, 34800, "10%"),
        ("メモリ", "DDR5-5600 32GB×2", 1, 23600, 23600, "10%"),
        ("SSD", "NVMe 2TB Gen4", 2, 17900, 35800, "10%"),
        ("電源", "850W 高効率仕様", 1, 16800, 16800, "10%"),
        ("CPUクーラー", "空冷 大型ヒートシンク", 1, 8900, 8900, "10%"),
        ("ケース", "ミドルタワー ガラス側面", 1, 12400, 12400, "10%"),
        ("ケースFAN", "120mm PWM", 3, 1800, 5400, "10%"),
        ("OS・セットアップ", "OSライセンス+初期設定", 1, 18000, 18000, "10%"),
        ("モニター", "27型 4K ディスプレイ", 1, 39800, 39800, "10%"),
    ]
    rh = 9 * mm
    ry = head_y - rh
    for i, p in enumerate(parts):
        yy = ry - i * rh + 5.6 * mm
        if i % 2 == 0:
            draw_box(c, c1, yy - 6 * mm, avail, rh, HexColor("#f7f9fb"),
                     None, radius=0)
        L.text(c1 + 2 * mm, yy, p[0], F("yu_gb"), 7.6, navy)
        L.text(c2 + 2 * mm, yy, p[1], F("yu_g"), 7.2, black)
        L.text(c3 + 2 * mm, yy, str(p[2]), F("yu_g"), 7.2, black)
        L.text(c4 + 2 * mm, yy, money(p[3]), F("yu_g"), 7.2, black)
        L.text(c5 + 2 * mm, yy, money(p[4]), F("yu_g"), 7.2, black)
        L.text(c6 - 1.5 * mm, yy, p[5], F("yu_g"), 7, black, "right")
        draw_h_line(c, c1, yy - 3.4 * mm, c6, 0.35, HexColor("#cfd9e4"))
    bottom = ry - len(parts) * rh

    # Totals strip
    sub = sum(p[4] for p in parts)
    tax = sub // 10
    L.text(c2, bottom - 8 * mm, "小計（税抜）", F("yu_g"), 8, navy)
    L.text(c6, bottom - 8 * mm, money(sub), F("yu_g"), 8, navy,
           "right")
    L.text(c2, bottom - 14 * mm, "消費税（10%）", F("yu_g"), 8, navy)
    L.text(c6, bottom - 14 * mm, "¥" + money(tax), F("yu_g"), 8, navy, "right")
    # simpler, clearer:
    draw_h_line(c, c2, bottom - 16 * mm, c6, 0.6, navy)
    L.text(c2, bottom - 21 * mm, "合計（税込）", F("yu_gb"), 9, navy)
    L.text(c6, bottom - 22 * mm, "¥" + money(sub + tax), F("gothic"), 16, cyan,
           "right", "total")

    # Asset review box lower
    box_y = bottom - 58 * mm
    draw_box(c, c1, box_y, avail, 30 * mm, HexColor("#fff8e6"),
             HexColor("#d6a300"), 0.8, 3)
    L.text(c1 + 3 * mm, box_y + 24 * mm, "【保証・動作確認】", F("yu_gb"), 8,
           HexColor("#7a5a00"))
    notes = ("各構成品の初期動作確認済み。保証書と製造番号の控えは同梱明細をご確認ください。"
             "初期不良のお問い合わせには本伝票番号をご用意ください。"
             "価格は税抜表示、クレジットカード決済は完了しています。")
    L.para(c1 + 3 * mm, box_y + 18 * mm, notes, F("yu_g"), 7.2,
           avail - 6 * mm, 4.4 * mm, HexColor("#5a4500"))

    # barcode-ish footer
    L.text(c1, 10 * mm, "架空テスト / カード決済完了",
           F("yu_g"), 6.5, HexColor("#888888"))


def case_v065(c):
    """Furniture delivery + assembly. Long narrow strip (receipt-like 100mm x 297mm)."""
    W, H = 100 * mm, 297 * mm
    c.setPageSize((W, H))
    L = Layout(c, W, H)
    M = 8 * mm
    brown = HexColor("#5b3a1e")
    cream = HexColor("#f5efe4")
    olive = HexColor("#6b7a3a")
    draw_box(c, 0, H - 46 * mm, W, 46 * mm, brown, radius=0)
    L.text(M, H - 16 * mm, "家具お届け・組立完了書", F("gothic"), 13, white)
    L.text(M, H - 23 * mm, "兼 お支払領収書", F("mincho"), 9, cream)
    L.text(W - M, H - 16 * mm, "2023/09/21", F("yu_g"), 8, cream, "right",
           "date")
    L.text(W - M, H - 21 * mm, "受付No. F-2317", F("yu_g"), 7, cream, "right")
    L.text(M, H - 36 * mm, "テスト市架空町3丁目22 テスト市家具 リビング館",
           F("yu_g"), 6.8, cream)

    # body on cream band
    draw_box(c, 0, 0, W, H - 46 * mm, cream, radius=0)
    y = H - 56 * mm
    L.text(M, y, "納品先：テスト株式会社（架空）", F("yu_g"), 7.5, brown)
    y -= 5.5 * mm
    L.text(M, y, "お届け日：2023年9月20日  組立：同日完了", F("yu_g"), 7, brown)
    y -= 6 * mm
    draw_h_line(c, M, y, W - M, 0.5, brown, dash=[2, 2])

    y -= 7 * mm
    L.text(M, y, "商品明細", F("yu_gb"), 9, olive)
    y -= 5 * mm
    goods = [
        ("会議用テーブル 1800×900", 1, 68000),
        ("ワークチェア メッシュ", 4, 24000),
        ("サイドキャビネット", 2, 31500),
        ("配達・設置・組立費", 1, 22000),
    ]
    for name, q, price in goods:
        amount = q * price
        L.text(M, y, name, F("yu_g"), 7, brown)
        L.text(M + 56 * mm, y, "×" + str(q), F("yu_g"), 7, brown)
        L.text(W - M, y, money(amount), F("yu_g"), 7, brown, "right")
        y -= 5.3 * mm
    sub = sum(q * p for _, q, p in goods)
    draw_h_line(c, M, y + 1.5 * mm, W - M, 0.4, brown)
    y -= 2 * mm
    L.text(M + 56 * mm, y, "小計（税抜）", F("yu_g"), 7, brown)
    L.text(W - M, y, money(sub), F("yu_g"), 7, brown, "right")
    y -= 5.3 * mm
    tax = sub * 10 // 100
    L.text(M + 56 * mm, y, "消費税（10%）", F("yu_g"), 7, brown)
    L.text(W - M, y, money(tax), F("yu_g"), 7, brown, "right")
    y -= 4 * mm
    draw_h_line(c, M, y, W - M, 1.4, brown)
    y -= 7 * mm
    L.text(M, y, "ご請求合計（税込）", F("yu_gb"), 9, brown)
    L.text(W - M, y - 1 * mm, "¥" + money(sub + tax), F("gothic"), 15, olive,
           "right", "total")

    # tender
    y -= 14 * mm
    L.text(M, y, "お支払：クレジットカード（1回払い）", F("yu_g"), 7, brown)
    y -= 5 * mm
    L.text(M, y, "カード下4桁 1234 / 承認番号 000000（ダミー）", F("yu_g"), 6.5,
           HexColor("#777777"))
    y -= 7 * mm
    L.text(M, y, "配送・組立の完了サイン", F("yu_gb"), 7.5, brown)
    fake_hand_line(c, M, y - 9 * mm, "設置完了 承認", 10, HexColor("#2244aa"),
                   jitter_seed=12)
    draw_seal(c, W - M - 14 * mm, y - 9 * mm, 12 * mm, "リビング館",
              HexColor("#b13a2e"), font_size=6.5)

    # footer card
    y -= 26 * mm
    draw_box(c, M, y - 20 * mm, W - 2 * M, 18 * mm, white,
             HexColor("#c8b89a"), 0.7, 2)
    L.text(M + 3 * mm, y - 7 * mm, "お問い合わせ", F("yu_gb"), 7, brown)
    L.text(M + 3 * mm, y - 12 * mm, "テスト市家具 カスタマー窓口", F("yu_g"), 6.5,
           brown)
    L.text(M + 3 * mm, y - 16 * mm, "TEL 0000-00-0000（架空）", F("yu_g"), 6.2,
           HexColor("#777777"))
    draw_text(c, W / 2, 6 * mm, "架空テスト", F("yu_g"), 6,
              HexColor("#999999"), "center")


def case_v066(c):
    """Camera equipment rental. A5 landscape, grid header cells."""
    W, H = A5[1], A5[0]
    c.setPageSize((W, H))
    L = Layout(c, W, H)
    M = 10 * mm
    charcoal = HexColor("#2a2a2a")
    amber = HexColor("#b06a00")
    cool = HexColor("#e9eef2")

    # header grid: 3 cells across top
    cell_h = 26 * mm
    draw_rect(c, M, H - M - cell_h, 60 * mm, cell_h, charcoal, charcoal, 0.8)
    L.text(M + 4 * mm, H - M - 8 * mm, "カメラ機材 貸出", F("gothic"), 12, white)
    L.text(M + 4 * mm, H - M - 14 * mm, "レンタル精算書", F("mincho"), 8,
           HexColor("#dddddd"))
    L.text(M + 4 * mm, H - M - 22 * mm, "架空撮影機材 テスト市レンタル",
           F("yu_g"), 6.5, HexColor("#bbbbbb"))

    draw_rect(c, M + 60 * mm, H - M - cell_h, 50 * mm, cell_h,
              HexColor("#aab4bd"), cool, 0.6)
    L.text(M + 63 * mm, H - M - 7 * mm, "ご契約者", F("yu_gb"), 6.5,
           HexColor("#555555"))
    L.text(M + 63 * mm, H - M - 13 * mm, "企画部 映像チーム", F("yu_g"), 8,
           charcoal)
    L.text(M + 63 * mm, H - M - 19 * mm, "発注書 No. RNT-3312", F("yu_g"), 6.5,
           charcoal)

    draw_rect(c, M + 110 * mm, H - M - cell_h, W - 2 * M - 110 * mm, cell_h,
              HexColor("#aab4bd"), cool, 0.6)
    L.text(M + 113 * mm, H - M - 7 * mm, "貸出日", F("yu_gb"), 6.5,
           HexColor("#555555"))
    L.text(M + 113 * mm, H - M - 13 * mm, "令和6年4月8日", F("yu_g"), 8, charcoal)
    L.text(M + 113 * mm, H - M - 19 * mm, "返却：4月10日 正午", F("yu_g"), 6.5,
           charcoal)

    # big total center-right
    L.text(W - M, H - M - cell_h - 14 * mm, "お支払合計", F("yu_gb"), 7.5,
           charcoal, "right")
    L.text(W - M, H - M - cell_h - 26 * mm, "¥ 73,590", F("gothic"), 22, amber,
           "right", "total")

    # items table lower half
    ty = H - M - cell_h - 36 * mm
    xs = [M, M + 46 * mm, M + 64 * mm, M + 82 * mm, M + 112 * mm, W - M]
    heads = ["機材", "品番", "数量", "日数", "金額"]
    draw_box(c, M, ty, W - 2 * M, 7 * mm, charcoal, radius=0)
    for i, h in enumerate(heads):
        L.text(xs[i] + 2 * mm if i < 4 else W - M - 2 * mm,
               ty + 2.2 * mm, h, F("yu_gb"), 7, white,
               "left" if i < 4 else "right")
    items = [
        ("シネカメラ本体", "CAM-X1(架空)", 1, 3, 36000),
        ("レンズ 24-70mm", "LNZ-2470", 1, 3, 9000),
        ("レンズ 70-200mm", "LNZ-7200", 1, 3, 7500),
        ("三脚", "TRP-HD", 1, 3, 3600),
        ("LEDパネル", "LED-200", 2, 3, 10800),
    ]
    rh = 7 * mm
    ry = ty - rh
    for i, (n, code, q, d, amt) in enumerate(items):
        ybase = ry - i * rh + 2.6 * mm
        if i % 2 == 0:
            draw_box(c, M, ybase - 2.2 * mm, W - 2 * M, rh, HexColor("#f6f8fa"),
                     None, radius=0)
        L.text(xs[0] + 2 * mm, ybase, n, F("mincho"), 7.5, charcoal)
        L.text(xs[1] + 2 * mm, ybase, code, F("yu_g"), 6.5, charcoal)
        L.text(xs[2] + 2 * mm, ybase, str(q), F("yu_g"), 7, charcoal)
        L.text(xs[3] + 2 * mm, ybase, str(d), F("yu_g"), 7, charcoal)
        L.text(xs[5] - 2 * mm, ybase, money(amt), F("yu_g"), 7, charcoal,
               "right")
        draw_h_line(c, M, ybase - 2.6 * mm, W - M, 0.35, HexColor("#c9d2d9"))
    for xx in xs[1:-1]:
        draw_v_line(c, xx, ty, ry - len(items) * rh + 6 * mm, 0.35,
                    HexColor("#c9d2d9"))
    # wait: vertical lines from ty (bottom of header) down to bottom of table

    sub = sum(it[4] for it in items)
    by = ry - len(items) * rh - 2 * mm
    L.text(xs[4] + 6 * mm, by, "小計（税抜）", F("yu_g"), 7, charcoal)
    L.text(W - M, by, money(sub), F("yu_g"), 7, charcoal, "right")
    tax = sub // 10
    by -= 5 * mm
    L.text(xs[4] + 6 * mm, by, "消費税（10%）", F("yu_g"), 7, charcoal)
    L.text(W - M, by, money(tax), F("yu_g"), 7, charcoal, "right")

    # deposit line below items (left)
    L.text(M, ry - len(items) * rh - 3 * mm,
           "予約金 ¥10,000 + 返却時精算 ¥63,590 = 領収総額 ¥73,590",
           F("yu_g"), 6.5, HexColor("#666666"))
    L.text(M, ry - len(items) * rh - 7.5 * mm,
           "支払日 2024年4月10日 / 全額領収済", F("yu_g"), 6.5,
           HexColor("#666666"))

    # foot: terms
    fy = M + 11 * mm
    L.text(M, fy, "延滞料：返却が1時間超で ¥3,000（税込）／破損は別途見積",
           F("yu_g"), 6.5, HexColor("#444444"))
    L.text(M, fy - 4.5 * mm, "機材保険：付帯あり / 免責 ¥5,000",
           F("yu_g"), 6.5, HexColor("#444444"))
    draw_stamp_rect(c, W - M - 30 * mm, fy - 10 * mm, 28 * mm, 11 * mm, "返却済",
                    "4/10 11:45", HexColor("#2763a4"), 7)
    draw_text(c, M, 4 * mm, "架空テスト", F("yu_g"), 5.5, HexColor("#999999"))


def case_v067(c):
    """Audio studio session bill, time-log style, tall A4 with clock rows."""
    c.setPageSize(A4)
    W, H = A4
    L = Layout(c, W, H)
    M = 14 * mm
    plum = HexColor("#4a2c4e")
    lilac = HexColor("#d9cfe2")
    dark = HexColor("#222222")

    # Header: centered stacked wordmark, thin rule
    L.text(W / 2, H - 22 * mm, "架空スタジオ 音色", F("mincho"), 16, plum,
           "center", "studio")
    draw_h_line(c, W * 0.25, H - 26 * mm, W * 0.75, 0.8, lilac)
    L.text(W / 2, H - 33 * mm, "レコーディング セッション請求書", F("yu_g"), 9,
           plum, "center", "title")
    L.text(M, H - 42 * mm, "請求番号：SS-2022-115", F("yu_g"), 7, dark)
    L.text(W - M, H - 42 * mm, "発行：2022.12.05", F("yu_g"), 7, dark, "right")
    L.text(M, H - 47 * mm, "ご利用：テスト市架空町1-1 音色レコーディング室A",
           F("yu_g"), 7, dark)

    # Billing period block
    L.text(M, H - 56 * mm, "ご利用期間", F("yu_gb"), 7.5, plum)
    L.text(M, H - 61 * mm, "2022年11月1日 〜 2022年11月30日", F("yu_g"), 8, dark)

    # session log grid (service-date oriented)
    ty = H - 70 * mm
    tx = M
    width = W - 2 * M
    cols = [tx, tx + 30 * mm, tx + 52 * mm, tx + 74 * mm, tx + 100 * mm,
            W - M]
    heads = ["利用日", "時間帯", "室内", "エンジニア", "利用料（税抜）"]
    draw_box(c, tx, ty, width, 7 * mm, plum, radius=0)
    for i, h in enumerate(heads):
        L.text(cols[i] + 2 * mm if i < 4 else W - M - 2 * mm,
               ty + 2.2 * mm, h, F("yu_gb"), 7, white,
               "left" if i < 4 else "right")
    logs = [
        ("11月2日(水)", "13:00-18:00", "A", "鈴木", 42000),
        ("11月4日(金)", "19:00-22:00", "A", "鈴木", 22000),
        ("11月9日(水)", "13:00-19:00", "B", "佐野", 50000),
        ("11月15日(火)", "10:00-17:00", "A", "鈴木", 63000),
        ("11月24日(木)", "18:00-23:00", "C", "佐野", 45000),
    ]
    rh = 9 * mm
    ry = ty - rh
    for i, row in enumerate(logs):
        yb = ry - i * rh + 3 * mm
        if i % 2 == 0:
            draw_box(c, M, yb - 2.6 * mm, width, rh, HexColor("#faf7fc"),
                     None, radius=0)
        L.text(cols[0] + 2 * mm, yb, row[0], F("yu_g"), 7.4, dark)
        L.text(cols[1] + 2 * mm, yb, row[1], F("yu_g"), 7.4, dark)
        L.text(cols[2] + 2 * mm, yb, row[2], F("yu_g"), 7.4, dark)
        L.text(cols[3] + 2 * mm, yb, row[3], F("yu_g"), 7.4, dark)
        L.text(cols[5] - 2 * mm, yb, money(row[4]), F("yu_g"), 7.4, dark,
               "right")
    for xx in cols[1:-1]:
        draw_v_line(c, xx, ry - len(logs) * rh + 6 * mm, ty, 0.4, lilac)
    tbl_bottom = ry - len(logs) * rh

    # Additional services
    ey = tbl_bottom - 6 * mm
    L.text(M, ey, "エンジニア・機材追加", F("yu_gb"), 7.5, plum)
    ey -= 5.5 * mm
    extra = [("ミックスダウン・エンジニア", 28000),
             ("外部マイクレンタル", 6000),
             ("HDD持ち出し（2TB, 販売）", 8800)]
    for nm, amt in extra:
        L.text(M + 3 * mm, ey, nm, F("yu_g"), 7.2, dark)
        L.text(W - M, ey, money(amt), F("yu_g"), 7.2, dark, "right")
        ey -= 5.5 * mm
    sub = sum(r[4] for r in logs) + sum(a for _, a in extra)
    tax = sub * 10 // 100
    L.text(M + 3 * mm, ey, "小計（税抜）", F("yu_g"), 7, dark)
    L.text(W - M, ey, money(sub), F("yu_g"), 7, dark, "right")
    ey -= 5 * mm
    L.text(M + 3 * mm, ey, "消費税（10%）", F("yu_g"), 7, dark)
    L.text(W - M, ey, money(tax), F("yu_g"), 7, dark, "right")

    # total bar across
    ey -= 8 * mm
    draw_box(c, M, ey - 8 * mm, W - 2 * M, 14 * mm, plum, radius=0)
    L.text(M + 4 * mm, ey + 0.5 * mm, "合計（税込）", F("yu_gb"), 10, white)
    L.text(W - M - 4 * mm, ey - 1.5 * mm, "¥" + money(sub + tax),
           F("gothic"), 17, white, "right", "total")

    # Payment terms + seal
    py = ey - 18 * mm
    L.text(M, py, "お支払方法：銀行振込（2022年12月20日 入金確認）",
           F("yu_g"), 7.5, dark)
    L.text(M, py - 5 * mm, "振込手数料は貴社負担にてお願いします。",
           F("yu_g"), 7, HexColor("#555555"))
    L.text(M, py - 10 * mm, "機材の持ち込み・変更は事前にご連絡ください。",
           F("yu_g"), 7, HexColor("#555555"))
    draw_seal(c, W - M - 16 * mm, py - 6 * mm, 12 * mm, "音色印",
              HexColor("#8e3b5b"), font_size=7)
    draw_text(c, M, 8 * mm, "架空テスト", F("yu_g"), 6, HexColor("#999999"))


def case_v068(c):
    """Advertising campaign spend summary, PAID. Letter landscape custom."""
    W, H = 279 * mm, 170 * mm
    c.setPageSize((W, H))
    L = Layout(c, W, H)
    M = 12 * mm
    orange = HexColor("#e07a1f")
    slate = HexColor("#3a424a")
    light = HexColor("#fdf4ea")

    # left header block
    draw_box(c, 0, H - 40 * mm, 110 * mm, 40 * mm, orange, radius=0)
    L.text(M, H - 16 * mm, "広告出稿 支払済報告書", F("gothic"), 16, white)
    L.text(M, H - 24 * mm, "ADVERTISING CAMPAIGN SUMMARY", F("yu_g"), 7.5,
           HexColor("#fff0e0"))
    L.text(M, H - 34 * mm, "架空広告代理店 ネクストテスト", F("yu_g"), 7,
           white)
    L.text(M + 60 * mm, H - 34 * mm, "〒000-0000 テスト市架空町6-6",
           F("yu_g"), 6.5, HexColor("#ffe9d2"))

    # right side: grand total in a rounded panel
    gx = 118 * mm
    draw_box(c, gx, H - 40 * mm, W - gx - M, 40 * mm, light, orange, 1.2, 5)
    L.text(gx + 6 * mm, H - 12 * mm, "キャンペーン合計（支払済）",
           F("yu_gb"), 8, slate)
    L.text(gx + 6 * mm, H - 26 * mm, "¥ 1,342,000", F("gothic"), 28, orange,
           "left", "grand-total")
    L.text(gx + 6 * mm, H - 34 * mm,
           "対象期間 2025年7月1日～7月31日 / 支払日 2025-08-25",
           F("yu_g"), 6.8, slate)

    # meta row
    L.text(M, H - 48 * mm, "クライアント：テスト物産 株式会社 マーケ部 御中",
           F("yu_g"), 8, slate)
    L.text(W - M, H - 48 * mm, "レポートNo. AD-2507-31", F("yu_g"), 7.5, slate,
           "right")

    # channel table with mini progress bars
    ty = H - 60 * mm
    tx = M
    cols = [tx, tx + 46 * mm, tx + 75 * mm, tx + 125 * mm, tx + 160 * mm,
            tx + 200 * mm, W - M]
    heads = ["媒体", "配信/掲載", "表示回数", "支出額", "構成比", "税区分"]
    draw_box(c, tx, ty, W - 2 * M, 7 * mm, slate, radius=0)
    for i, h in enumerate(heads):
        L.text(cols[i] + 2 * mm if i < 5 else W - M - 2 * mm,
               ty + 2.2 * mm, h, F("yu_gb"), 7, white,
               "left" if i < 5 else "right")
    rows = [
        ("SNS動画広告", "15秒 / A/B 2種", "120.4万", 420000, "10%"),
        ("検索連動型", "キーワード12件", "48.2万", 380000, "10%"),
        ("静止画バナー", "4媒体", "66.0万", 240000, "10%"),
        ("記事広告", "2社", "3.1万", 180000, "10%"),
    ]
    sub = sum(r[3] for r in rows)  # 1,220,000
    rh = 11 * mm
    ry = ty - rh
    for i, r in enumerate(rows):
        yb = ry - i * rh + 3.4 * mm
        if i % 2 == 0:
            draw_box(c, M, yb - 3.2 * mm, W - 2 * M, rh, HexColor("#f8f8f8"),
                     None, radius=0)
        L.text(cols[0] + 2 * mm, yb, r[0], F("mincho"), 8.4, slate)
        L.text(cols[1] + 2 * mm, yb, r[1], F("yu_g"), 7, slate)
        L.text(cols[2] + 2 * mm, yb, r[2], F("yu_g"), 7, slate)
        L.text(cols[3] + 2 * mm, yb, money(r[3]), F("yu_g"), 7.4, slate)
        L.text(cols[4] + 2 * mm, yb, "%.1f%%" % (r[3] / sub * 100),
               F("yu_g"), 7, slate)
        L.text(W - M - 2 * mm, yb, r[4], F("yu_g"), 7, slate, "right")
        # tiny bar
        bar_w = 18 * mm
        frac = r[3] / float(sub)
        draw_box(c, cols[4] + 12 * mm, yb + 0.5, bar_w, 2.4, HexColor("#e8e8e8"),
                 radius=1)
        draw_box(c, cols[4] + 12 * mm, yb + 0.5, bar_w * frac, 2.4, orange,
                 radius=1)
        draw_h_line(c, M, yb - 3.8 * mm, W - M, 0.35, HexColor("#d0d0d0"))
    for xx in cols[1:-1]:
        draw_v_line(c, xx, ry - len(rows) * rh + 7.2 * mm, ty, 0.35,
                    HexColor("#c0c0c0"))

    # tax strip below left
    by = ry - len(rows) * rh - 8 * mm
    tax = sub // 10
    L.text(M, by, "税抜合計", F("yu_g"), 7.5, slate)
    L.text(M + 20 * mm, by, money(sub), F("yu_g"), 7.5, slate)
    L.text(M + 50 * mm, by, "消費税（10%）", F("yu_g"), 7.5, slate)
    L.text(M + 75 * mm, by, money(tax), F("yu_g"), 7.5, slate)
    L.text(M + 100 * mm, by, "税込合計", F("yu_gb"), 7.5, slate)
    L.text(M + 120 * mm, by, money(sub + tax), F("yu_g"), 7.5, slate)

    # right block: payment status
    px = W - M - 72 * mm
    draw_box(c, px, by - 30 * mm, 72 * mm, 26 * mm, HexColor("#eaf6ec"),
             HexColor("#3c8a51"), 1.0, 3)
    L.text(px + 4 * mm, by - 10 * mm, "支払状況：全額入金済", F("yu_gb"), 8,
           HexColor("#2c6b3d"))
    L.text(px + 4 * mm, by - 16 * mm, "振込 2025-08-25 / 残高 ¥0", F("yu_g"),
           7, HexColor("#2c6b3d"))
    L.text(px + 4 * mm, by - 22 * mm, "承認番号 TRX-0000-DUMMY",
           F("yu_g"), 6.5, HexColor("#2c6b3d"))

    # bottom fine print
    L.text(M, by - 20 * mm, "備考：媒体レポートは別添。追加出稿分は翌月精算。",
           F("yu_g"), 6.8, HexColor("#666666"))
    L.text(M, by - 26 * mm, "税区分は各媒体の請求基準に準拠。",
           F("yu_g"), 6.8, HexColor("#666666"))
    draw_text(c, W - M, 7 * mm, "架空テスト", F("yu_g"), 5.5,
              HexColor("#999999"), "right")


def case_v069(c):
    """Goods resale wholesale purchase, invoice paid. Tall custom 148x210."""
    W, H = 148 * mm, 210 * mm
    c.setPageSize((W, H))
    L = Layout(c, W, H)
    M = 11 * mm
    deep = HexColor("#173a5e")
    gold = HexColor("#a8842c")
    paper = HexColor("#f7f4ec")

    draw_box(c, 0, 0, W, H, paper, radius=0)
    # border double line
    draw_rect(c, 5 * mm, 5 * mm, W - 10 * mm, H - 10 * mm, deep, None, 2.0)
    draw_rect(c, 6.5 * mm, 6.5 * mm, W - 13 * mm, H - 13 * mm, deep, None, 0.5)

    L.text(W / 2, H - 20 * mm, "卸　売　請　求　書", F("mincho"), 18, deep,
           "center", "title")
    L.text(W / 2, H - 26 * mm, "WHOLESALE INVOICE / PAID", F("yu_g"), 6.5, gold,
           "center")
    L.text(W - 12 * mm, H - 34 * mm, "請求書番号：WS-240118", F("yu_g"), 7,
           deep, "right")
    L.text(W - 12 * mm, H - 39 * mm, "発行日：令和6年1月18日", F("yu_g"), 7,
           deep, "right")

    L.text(12 * mm, H - 34 * mm, "テスト小売株式会社 仕入部", F("gothic"), 10,
           deep)
    L.text(12 * mm, H - 40 * mm, "御中", F("mincho"), 8, deep)

    L.text(12 * mm, H - 48 * mm, "発行：架空物産卸 株式会社", F("yu_g"), 8,
           deep)
    L.text(12 * mm, H - 53 * mm, "テスト市架空町8-4 センター倉庫", F("yu_g"), 6.5,
           HexColor("#555555"))
    L.text(12 * mm, H - 58 * mm, "登録番号 T0000000000000", F("yu_g"), 6.5,
           HexColor("#555555"))

    # total
    L.text(W - 12 * mm, H - 50 * mm, "ご請求額（税込）", F("yu_g"), 7, deep,
           "right")
    L.text(W - 12 * mm, H - 60 * mm, "¥230,340", F("gothic"), 20, deep,
           "right", "total")
    L.text(W - 12 * mm, H - 65 * mm, "支払期日：2024-02-28 ／ 支払済",
           F("yu_g"), 6.5, gold, "right")

    # table
    ty = H - 76 * mm
    tx = 12 * mm
    right = W - 12 * mm
    cols = [tx, tx + 66 * mm, tx + 80 * mm, tx + 96 * mm, right]
    heads = ["商品", "入数", "単価", "金額"]
    draw_box(c, tx, ty, right - tx, 7 * mm, deep, radius=0)
    for i, h in enumerate(heads):
        L.text(cols[i] + 2 * mm if i < 3 else right - 2 * mm,
               ty + 2.2 * mm, h, F("yu_gb"), 7, white,
               "left" if i < 3 else "right")
    items = [
        ("収納ボックス（中）", 48, 720, 34560),
        ("ステンレスボトル 500ml", 24, 1580, 37920),
        ("タオルセット 3枚組", 36, 920, 33120),
        ("洗剤セット A", 20, 2100, 42000),
        ("LEDデスクライト", 12, 3300, 39600),
        ("食品保存容器 4個組", 30, 740, 22200),
    ]
    sub = sum(i[3] for i in items)  # 209,400
    rh = 8 * mm
    ry = ty - rh
    for i, it in enumerate(items):
        yb = ry - i * rh + 2.8 * mm
        L.text(cols[0] + 2 * mm, yb, it[0], F("mincho"), 7.6, deep)
        L.text(cols[1] + 2 * mm, yb, str(it[1]), F("yu_g"), 7.2, deep)
        L.text(cols[2] + 2 * mm, yb, money(it[2]), F("yu_g"), 7.2, deep)
        L.text(right - 2 * mm, yb, money(it[3]), F("yu_g"), 7.4, deep, "right")
        draw_h_line(c, tx, yb - 3 * mm, right, 0.35, HexColor("#c9bea0"))
    for xx in cols[1:-1]:
        draw_v_line(c, xx, ry - len(items) * rh + 5.8 * mm, ty, 0.35,
                    HexColor("#c9bea0"))

    by = ry - len(items) * rh - 5 * mm
    tax = sub // 10  # 20,940
    L.text(cols[2], by, "小計", F("yu_g"), 7.5, deep)
    L.text(right, by, money(sub), F("yu_g"), 7.5, deep, "right")
    by -= 5.5 * mm
    L.text(cols[2], by, "消費税（10%）", F("yu_g"), 7.5, deep)
    L.text(right, by, money(tax), F("yu_g"), 7.5, deep, "right")
    by -= 4 * mm
    draw_h_line(c, cols[2], by, right, 1.0, deep)
    by -= 6 * mm
    L.text(cols[2], by, "合計", F("yu_gb"), 9, deep)
    L.text(right, by, money(sub + tax), F("gothic"), 12, deep, "right")

    # seal & notes
    draw_seal(c, right - 18 * mm, M + 26 * mm, 14 * mm, "架空物産卸",
              HexColor("#b03a2a"), font_size=6.5)
    L.text(12 * mm, M + 34 * mm, "入金：2024年2月20日 普通預金口座へ着金",
           F("yu_g"), 6.5, HexColor("#444444"))
    L.text(12 * mm, M + 29 * mm, "返品期限：納品後7日以内 / 送料は元払い",
           F("yu_g"), 6.5, HexColor("#444444"))
    L.text(12 * mm, M + 24 * mm, "本請求書は支払い済みです。", F("yu_gb"), 6.8,
           gold)
    draw_text(c, W / 2, M + 2 * mm, "架空テスト", F("yu_g"), 5.5,
              HexColor("#999999"), "center")


def case_v070(c):
    """Packaging material shop receipt. Small horizontal slip 120x80mm."""
    W, H = 120 * mm, 80 * mm
    c.setPageSize((W, H))
    L = Layout(c, W, H)
    blue = HexColor("#14456e")
    sky = HexColor("#e7f1f7")
    M = 6 * mm

    draw_box(c, 0, H - 16 * mm, W, 16 * mm, blue, radius=0)
    L.text(M, H - 8 * mm, "かんぷパッケージ テスト店", F("gothic"), 10, white)
    L.text(W - M, H - 8 * mm, "お買上レシート", F("yu_g"), 8, sky, "right")
    L.text(M, H - 13.5 * mm, "2025/06/03 11:42  店舗：架空町2号店",
           F("yu_g"), 6.3, sky)

    # two column itemised
    y = H - 22 * mm
    L.text(M, y, "品目", F("yu_gb"), 6.2, blue)
    L.text(M + 52 * mm, y, "数量", F("yu_gb"), 6.2, blue)
    L.text(W - M, y, "金額", F("yu_gb"), 6.2, blue, "right")
    y -= 1.5 * mm
    draw_h_line(c, M, y, W - M, 0.5, blue)
    items = [
        ("段ボール箱 A4（20枚組）", 2, 1680),
        ("緩衝材 プチプチ 50m", 1, 2200),
        ("クラフト紙袋 中", 50, 1600),
        ("OPP袋 100枚", 3, 840),
        ("ガムテープ 透明 50mm", 2, 380),
    ]
    y -= 4.5 * mm
    for nm, q, amt in items:
        L.text(M, y, nm, F("yu_g"), 6.4, black)
        L.text(M + 52 * mm, y, str(q), F("yu_g"), 6.4, black)
        L.text(W - M, y, money(amt), F("yu_g"), 6.4, black, "right")
        y -= 4.2 * mm
    sub = sum(a for _, _, a in items)  # 6,700? compute 1680+2200+1600+840+380=6700
    tax = sub * 8 // 100  # 536 for 8% (packaging = 8%? use 10)
    tax = sub // 10  # keep 10%
    draw_h_line(c, M, y + 2.6 * mm, W - M, 0.4, HexColor("#999999"))
    L.text(M, y, "小計", F("yu_g"), 6.4, black)
    L.text(W - M, y, money(sub), F("yu_g"), 6.4, black, "right")
    y -= 4.2 * mm
    L.text(M, y, "消費税（10%）", F("yu_g"), 6.4, black)
    L.text(W - M, y, money(tax), F("yu_g"), 6.4, black, "right")

    # total band
    y -= 3 * mm
    draw_box(c, M, y - 10 * mm, 52 * mm, 10 * mm, sky, blue, 0.8, 1)
    L.text(M + 2 * mm, y - 7.5 * mm, "合計", F("yu_gb"), 8, blue)
    L.text(M + 12 * mm, y - 8.5 * mm, "¥" + money(sub + tax), F("gothic"), 14,
           blue, "left", "total")
    L.text(M + 55 * mm, y - 5 * mm, "お預り 10,000", F("yu_g"), 6.2, black)
    L.text(M + 55 * mm, y - 9 * mm, "お釣り " + money(10000 - (sub + tax)),
           F("yu_g"), 6.2, black)
    L.text(W - M, y - 9 * mm, "現金", F("yu_gb"), 7, black, "right")

    draw_text(c, W / 2, 4 * mm, "架空テスト / 包材・資材", F("yu_g"), 5.2,
              HexColor("#999999"), "center")


def case_v071(c):
    """Electronics components, long line-items table. A4 landscape dense."""
    W, H = A4[1], A4[0]
    c.setPageSize((W, H))
    L = Layout(c, W, H)
    M = 10 * mm
    head_bg = HexColor("#243b53")
    red = HexColor("#a61b1b")

    # title small top-left, meta right in one line
    L.text(M, H - 14 * mm, "電子部品 購入・納品書（兼 受領確認）", F("gothic"),
           13, head_bg)
    L.text(W - M, H - 12 * mm, "発行 2024-06-14 / 対象仕入 2024-05-31",
           F("yu_g"), 7, head_bg, "right")
    L.text(W - M, H - 17 * mm, "架空電子 部品センター テスト支店", F("yu_g"), 7,
           HexColor("#555555"), "right")

    ty = H - 26 * mm
    tx = M
    right = W - M
    cols = [tx, tx + 10 * mm, tx + 62 * mm, tx + 96 * mm, tx + 112 * mm,
            tx + 140 * mm, tx + 176 * mm, right]
    heads = ["#", "部品名 / メーカー型式（架空）", "JAN/管理", "数量", "単価",
             "金額", "分類"]
    draw_box(c, tx, ty, right - tx, 6.5 * mm, head_bg, radius=0)
    for i, h in enumerate(heads):
        L.text(cols[6] - 1.5 * mm if i == 5 else cols[i] + 1.5 * mm,
               ty + 2.1 * mm, h, F("yu_gb"), 6.4, white,
               "left" if i < 5 or i == 6 else "right")
    rows = [
        ("積層セラミックコンデンサ", "CC-0402-100N", "0000001", 5000, 3, 15000, "電子部品"),
        ("抵抗器 1/4W 10kΩ", "RS-025-103", "0000002", 3000, 2, 6000, "電子部品"),
        ("電解コンデンサ 470µF", "EC-105-471", "0000003", 500, 48, 24000, "電子部品"),
        ("LED 赤 3mm", "LD-030-R", "0000004", 1000, 12, 12000, "電子部品"),
        ("コネクタ 40P", "CN-40P-2R", "0000005", 200, 95, 19000, "電子部品"),
        ("基板用スペーサー", "SP-05M-10", "0000006", 4000, 4, 16000, "機構部品"),
        ("スイッチ タクト", "SW-6MM-01", "0000007", 1500, 8, 12000, "電子部品"),
        ("ジャンパーワイヤ", "JW-20-100", "0000008", 50, 240, 12000, "配線材"),
        ("リレー 5V", "RL-05-DPDT", "0000009", 300, 380, 114000, "電子部品"),
        ("ヒートシンク 小型", "HS-20-30", "0000010", 800, 65, 52000, "機構部品"),
        ("テープ 絶縁 耐熱", "TP-HT-50", "0000011", 40, 980, 39200, "資材"),
        ("静電気防止袋 100枚", "ES-200-100", "0000012", 60, 620, 37200, "資材"),
        ("はんだ 鉛フリー", "SL-LF-05", "0000013", 30, 1450, 43500, "資材"),
        ("アクリルケース", "AC-150-CL", "0000014", 150, 880, 132000, "機構部品"),
    ]
    rh = 7.4 * mm
    ry = ty - rh
    for i, r in enumerate(rows):
        yb = ry - i * rh + 2.4 * mm
        if i % 2 == 0:
            draw_box(c, tx, yb - 4 * mm, right - tx, rh,
                     HexColor("#f6f8fa"), None, radius=0)
        L.text(cols[0] + 1.5 * mm, yb, str(i + 1), F("yu_g"), 6.2,
               HexColor("#777777"))
        L.text(cols[1] + 1.5 * mm, yb, r[0], F("mincho"), 6.8, black)
        L.text(cols[1] + 1.5 * mm, yb - 2.6 * mm, r[1], F("yu_g"), 5.5, black)
        L.text(cols[2] + 1.5 * mm, yb, r[2], F("yu_g"), 6.4, black)
        L.text(cols[3] + 1.5 * mm, yb, money(r[3]), F("yu_g"), 6.4, black)
        L.text(cols[4] + 1.5 * mm, yb, money(r[4]), F("yu_g"), 6.4, black)
        L.text(cols[6] - 1.5 * mm, yb, money(r[5]), F("yu_g"), 6.6, black,
               "right")
        L.text(cols[6] + 1.5 * mm, yb, r[6], F("yu_g"), 6.2, black)
        draw_h_line(c, tx, yb - 3.5 * mm, right, 0.3, HexColor("#ccd3da"))
    for xx in cols[1:-1]:
        draw_v_line(c, xx, ry - len(rows) * rh + 4.6 * mm, ty, 0.3,
                    HexColor("#ccd3da"))

    totals_y = ry - len(rows) * rh - 4 * mm
    sub = sum(r[4] * 1 for r in rows) if False else 0
    # recompute properly: qty * unit
    sub = sum(r[3] * r[4] for r in rows)
    tax = sub // 10
    # note qty/unit columns: qty=r[3], unit=r[4], amount=r[5]; amounts should match
    # ensure amount consistency in data; amounts here computed as qty*unit
    L.text(cols[3], totals_y, "税抜小計", F("yu_g"), 6.5, black)
    L.text(cols[4], totals_y, money(sub), F("yu_g"), 6.5, black)
    L.text(cols[5], totals_y + 2 * mm, "消費税（10%）", F("yu_g"), 6.5, black)
    L.text(right - 1.5 * mm, totals_y + 2 * mm, money(tax), F("yu_g"), 6.5,
           black, "right")
    L.text(cols[5], totals_y - 3.5 * mm, "合計（税込）", F("yu_gb"), 7.5, red)
    L.text(right - 1.5 * mm, totals_y - 4 * mm, "¥" + money(sub + tax),
           F("gothic"), 12, red, "right", "total")

    # left info block under table
    iy = totals_y - 12 * mm
    L.text(M, iy, "発注書 No. PO-5520 / 受領者：測試 太郎（架空）",
           F("yu_g"), 6.8, black)
    L.text(M, iy - 4.5 * mm, "納入条件：DAP・数量不足なし・外装損傷なし",
           F("yu_g"), 6.8, black)
    L.text(M, iy - 9 * mm, "この納品書は物品の受領を確認する書類であり、"
           "支払の証明ではありません。", F("yu_g"), 6.2, red)
    draw_stamp_rect(c, W - M - 26 * mm, iy - 11 * mm, 24 * mm, 10 * mm,
                    "受領確認", "2024/06/14", HexColor("#1f6f8b"), 6.5)
    draw_text(c, M, 5 * mm, "架空テスト", F("yu_g"), 5.2, HexColor("#999999"))


def case_v072(c):
    """Translation contractor with withholding review (源泉徴収検討). A4 portrait."""
    c.setPageSize(A4)
    W, H = A4
    L = Layout(c, W, H)
    M = 18 * mm
    teal = HexColor("#1f5f5b")
    sand = HexColor("#f6f1e7")
    rust = HexColor("#8a4b2a")

    draw_box(c, 0, H - 50 * mm, W, 50 * mm, sand, radius=0)
    L.text(M, H - 18 * mm, "翻訳業務 報酬計算書", F("mincho"), 20, teal,
           "left", "title")
    L.text(M, H - 25 * mm, "（源泉徴収の取り扱い検討用）", F("yu_g"), 8, rust)
    L.text(W - M, H - 18 * mm, "計算日：2025年12月18日", F("yu_g"), 7.5, teal,
           "right")
    L.text(W - M, H - 24 * mm, "お支払日：2025年12月25日（予定）", F("yu_g"), 7,
           teal, "right")
    L.text(M, H - 35 * mm, "受注者：テスト翻訳事務所（個人事業）  宛", F("yu_g"),
           8.5, teal)
    L.text(M, H - 42 * mm, "発注者：架空製作 株式会社 出版部", F("yu_g"), 7.5,
           HexColor("#555555"))

    # two-column: fee breakdown left, tax right
    y = H - 62 * mm
    L.text(M, y, "業務明細", F("yu_gb"), 9, teal)
    y -= 7 * mm
    jobs = [
        ("技術文書 英→日 翻訳", "42,000字 × ¥7", 294000),
        ("校正・用語統一", "20時間 × ¥4,000", 80000),
        ("専門用語データ整備", "1式", 26000),
    ]
    for nm, detail, amt in jobs:
        L.text(M, y, nm, F("mincho"), 8.2, teal)
        L.text(M + 60 * mm, y, detail, F("yu_g"), 7, HexColor("#555555"))
        L.text(W - M, y, money(amt), F("yu_g"), 8.2, teal, "right")
        y -= 8 * mm
    gross = sum(j[2] for j in jobs)  # 400,000
    draw_h_line(c, M, y + 3 * mm, W - M, 0.5, teal)
    y -= 4 * mm
    L.text(M, y, "報酬合計（税抜）", F("yu_gb"), 8.5, teal)
    L.text(W - M, y, money(gross), F("gothic"), 10, teal, "right")

    # withholding box
    y -= 12 * mm
    draw_box(c, M, y - 34 * mm, W - 2 * M, 36 * mm, white,
             HexColor("#b08968"), 0.9, 3)
    L.text(M + 4 * mm, y - 5 * mm, "源泉徴収の検討欄", F("yu_gb"), 8.5, rust)
    L.text(M + 4 * mm, y - 12 * mm,
           "源泉徴収税額（参考）：未計算 / 適用の有無は発注者・受注者の"
           "契約区分により異なる", F("yu_g"), 7, HexColor("#444444"))
    L.text(M + 4 * mm, y - 18 * mm,
           "確認事項：業務委託か雇用か、原稿料・翻訳料の区分、受給者区分。",
           F("yu_g"), 7, HexColor("#444444"))
    L.text(M + 4 * mm, y - 24 * mm,
           "本欄は仮の記載。確定額は税務処理の判断後に別途明記する。",
           F("yu_g"), 7, rust)
    L.text(M + 4 * mm, y - 31 * mm,
           "支払額（仮）：¥" + money(gross) + "（源泉徴収額控除前）",
           F("yu_gb"), 8, teal)

    # consumption tax section
    y -= 44 * mm
    L.text(M, y, "消費税の扱い", F("yu_gb"), 8.5, teal)
    y -= 5 * mm
    L.text(M + 3 * mm, y, "・請求書の税区分：課税（10%）", F("yu_g"), 7.2,
           HexColor("#444444"))
    y -= 4.5 * mm
    L.text(M + 3 * mm, y, "・報酬額 ¥400,000 に対する消費税：¥40,000",
           F("yu_g"), 7.2, HexColor("#444444"))
    y -= 4.5 * mm
    L.text(M + 3 * mm, y, "・税込支払額：" + yen(440000), F("yu_g"), 7.2,
           HexColor("#444444"))
    y -= 8 * mm
    L.text(M, y, "支払額（税込・源泉控除前）", F("yu_g"), 8, teal)
    L.text(W - M, y, "¥440,000", F("gothic"), 13, teal, "right", "total")

    # footer
    L.text(M, 26 * mm, "本計算書は支払い前の社内確認資料です。実際の入金は"
           "確定後に通知します。", F("yu_g"), 6.8, HexColor("#666666"))
    draw_seal(c, W - M - 14 * mm, 30 * mm, 11 * mm, "製作社印",
              HexColor("#a33"), font_size=6)
    draw_text(c, M, 10 * mm, "架空テスト", F("yu_g"), 6, HexColor("#999999"))


def case_v073(c):
    """Online training annual subscription, PAID via card. A4 wide-ish portrait
    with app-style card blocks, no table."""
    W, H = 124 * mm, 176 * mm
    c.setPageSize((W, H))
    L = Layout(c, W, H)
    M = 10 * mm
    violet = HexColor("#4b2a84")
    mint = HexColor("#dff3ea")
    text_c = HexColor("#2a2a33")

    # app header
    draw_box(c, 0, H - 20 * mm, W, 20 * mm, violet, radius=0)
    L.text(M, H - 9 * mm, "学習ラボ オンライン", F("gothic"), 11, white)
    L.text(W - M, H - 9 * mm, "領収済", F("yu_gb"), 7.5, mint, "right")
    L.text(W - M, H - 15 * mm, "2024/04/01", F("yu_g"), 7, mint, "right")

    # big center
    L.text(W / 2, H - 35 * mm, "年間サブスクリプション", F("yu_gb"), 8.5, violet,
           "center")
    L.text(W / 2, H - 50 * mm, "¥ 39,600", F("gothic"), 26, violet, "center",
           "total")
    L.text(W / 2, H - 57 * mm, "税込 / 年額 一括払い", F("yu_g"), 7,
           HexColor("#777777"), "center")

    # plan card
    card_y = H - 90 * mm
    draw_box(c, M, card_y, W - 2 * M, 28 * mm, mint, HexColor("#7cc4a4"), 0.8,
             4)
    L.text(M + 4 * mm, card_y + 21 * mm, "PRO プラン（法人向け）", F("yu_gb"),
           8.5, violet)
    L.text(M + 4 * mm, card_y + 14 * mm, "契約期間 2024-04-01 〜 2025-03-31",
           F("yu_g"), 7, text_c)
    L.text(M + 4 * mm, card_y + 8 * mm, "受講枠 20名 / 管理者アカウント 1",
           F("yu_g"), 7, text_c)
    L.text(M + 4 * mm, card_y + 3 * mm, "自動更新：なし（都度更新）",
           F("yu_g"), 7, text_c)

    # payment block
    py = card_y - 10 * mm
    L.text(M, py, "お支払い内訳", F("yu_gb"), 7.5, violet)
    py -= 5 * mm
    L.text(M + 2 * mm, py, "基本料金", F("yu_g"), 7, text_c)
    L.text(W - M, py, "¥36,000", F("yu_g"), 7, text_c, "right")
    py -= 5 * mm
    L.text(M + 2 * mm, py, "消費税（10%）", F("yu_g"), 7, text_c)
    L.text(W - M, py, "¥3,600", F("yu_g"), 7, text_c, "right")
    py -= 4 * mm
    draw_h_line(c, M + 2 * mm, py, W - M, 0.6, violet)
    py -= 6 * mm
    L.text(M + 2 * mm, py, "カード請求額", F("yu_gb"), 8, text_c)
    L.text(W - M, py, "¥39,600", F("gothic"), 10, violet, "right")

    # card info
    py -= 10 * mm
    draw_box(c, M, py - 8 * mm, W - 2 * M, 14 * mm, HexColor("#f6f6f9"),
             None, 0, 3)
    L.text(M + 3 * mm, py + 2 * mm, "カード種別", F("yu_g"), 6.3,
           HexColor("#888888"))
    L.text(M + 20 * mm, py + 2 * mm, "架空カード テスト", F("yu_g"), 6.8, text_c)
    L.text(M + 3 * mm, py - 3 * mm, "下4桁 / 承認", F("yu_g"), 6.3,
           HexColor("#888888"))
    L.text(M + 20 * mm, py - 3 * mm, "0000 / 000000", F("yu_g"), 6.8, text_c)

    # footer
    L.text(M, 18 * mm, "ご利用明細はアカウント管理画面からも確認できます。",
           F("yu_g"), 6.3, HexColor("#777777"))
    L.text(M, 13 * mm, "架空テスト", F("yu_g"), 5.8, HexColor("#999999"))


def case_v074(c):
    """Professional association annual dues, no year printed. B5 portrait."""
    W, H = B5
    c.setPageSize(B5)
    L = Layout(c, W, H)
    M = 16 * mm
    navy = HexColor("#1c3550")
    cream = HexColor("#f4f1e8")
    gold = HexColor("#a68a4c")

    draw_box(c, 0, 0, W, H, cream, radius=0)
    # emblem: concentric rings
    cx = W / 2
    cy = H - 34 * mm
    c.setStrokeColor(navy)
    c.setLineWidth(0.8)
    c.circle(cx, cy, 12 * mm, stroke=1, fill=0)
    c.circle(cx, cy, 9.5 * mm, stroke=1, fill=0)
    c.setFont(F("mincho"), 11)
    c.setFillColor(navy)
    c.drawCentredString(cx, cy - 3.5, "協会")
    L.text(cx, H - 52 * mm, "架空専門職 協会", F("mincho"), 13, navy, "center",
           "org")
    L.text(cx, H - 58 * mm, "会費 領収書", F("yu_g"), 9, gold, "center", "title")

    # receipt body
    bx = M
    by = H - 78 * mm
    L.text(bx, by, "領収金額", F("yu_g"), 8, navy)
    L.text(bx, by - 14 * mm, "¥ 18,000", F("gothic"), 26, navy, "left", "total")
    L.text(W - M, by, "発行日：　　年　　月　　日（記載なし）", F("yu_g"), 7.5,
           HexColor("#888888"), "right")
    L.text(W - M, by - 6 * mm, "会員番号：M-2201-77", F("yu_g"), 7, navy,
           "right")

    # reason block
    ry = by - 26 * mm
    items = [
        ("会費の種別", "正会員 年会費"),
        ("対象年度", "年度の記載なし（更新分）"),
        ("お支払区分", "一括"),
    ]
    for k, v in items:
        L.text(M, ry, k, F("yu_gb"), 8, navy)
        L.text(M + 34 * mm, ry, v, F("mincho"), 8, navy)
        draw_h_line(c, M, ry - 2.5 * mm, W - M, 0.4, HexColor("#b7ab8c"))
        ry -= 10 * mm

    # notes
    L.text(M, ry, "備考", F("yu_gb"), 8, navy)
    notes = ("会員情報の変更と受領証の再発行は事務局までご連絡ください。"
             "お問い合わせの際は、上記の会員番号をお知らせください。")
    L.para(M, ry - 6 * mm, notes, F("yu_g"), 7, W - 2 * M, 4.5 * mm, navy)

    # stamps
    draw_seal(c, W - M - 22 * mm, 34 * mm, 18 * mm, "協会理事長印",
              HexColor("#a33"), font_size=8)
    fake_hand_line(c, M + 6 * mm, 40 * mm, "領収 済", 14, navy, jitter_seed=91)
    draw_text(c, W / 2, 10 * mm, "架空テスト", F("yu_g"), 6,
              HexColor("#999999"), "center")


def case_v075(c):
    """Charity donation, separate from business purpose -> hold. Custom 182x257."""
    W, H = 182 * mm, 257 * mm
    c.setPageSize((W, H))
    L = Layout(c, W, H)
    M = 16 * mm
    rose = HexColor("#8e2a4a")
    blush = HexColor("#fbeef1")
    ink = HexColor("#3a2a2e")

    draw_box(c, 0, H - 62 * mm, W, 62 * mm, rose, radius=0)
    # decorative arc
    c.setStrokeColor(HexColor("#c9788f"))
    c.setLineWidth(0.8)
    c.circle(W - 28 * mm, H - 24 * mm, 22 * mm, stroke=1, fill=0)
    c.circle(W - 28 * mm, H - 24 * mm, 27 * mm, stroke=1, fill=0)
    L.text(M, H - 18 * mm, "寄附受領証", F("mincho"), 24, white)
    L.text(M, H - 26 * mm, "DONATION RECEIPT", F("yu_g"), 7.5,
           HexColor("#f2cbd6"))
    L.text(M, H - 46 * mm, "架空こども支援基金", F("gothic"), 12, white)
    L.text(M, H - 53 * mm, "テスト市架空町3-10 福祉会館内", F("yu_g"), 7,
           HexColor("#f2cbd6"))

    # amount
    L.text(M, H - 78 * mm, "受領金額", F("yu_g"), 8, ink)
    L.text(M, H - 96 * mm, "¥ 30,000", F("gothic"), 34, rose, "left",
           "total")
    L.text(M + 62 * mm, H - 92 * mm, "（一括寄附）", F("yu_g"), 8, ink)
    L.text(M, H - 106 * mm, "日付：令和7年3月2日", F("yu_g"), 8, ink)
    L.text(M, H - 113 * mm, "受付番号：DN-2503-0418", F("yu_g"), 7, ink)

    # donor
    draw_box(c, M, H - 132 * mm, W - 2 * M, 12 * mm, blush, rose, 0.6, 3)
    L.text(M + 4 * mm, H - 126 * mm, "寄附者：テスト 花子（架空） 様",
           F("mincho"), 9, ink)

    # separation note
    L.text(M, H - 146 * mm, "寄附の目的・区分", F("yu_gb"), 8.5, rose)
    notes = ("子どもの学習機会支援として受領いたしました。"
             "使途指定：学習教材・運営費（一般寄附）。"
             "受領内容の変更・再発行の際は受付番号をお知らせください。")
    L.para(M, H - 153 * mm, notes, F("yu_g"), 7.4, W - 2 * M, 5 * mm, ink)

    # fields
    fy = H - 186 * mm
    L.text(M, fy, "寄附金控除（参考）", F("yu_gb"), 8, rose)
    L.text(M + 4 * mm, fy - 7 * mm, "・特定寄附金の該当区分：未確認",
           F("yu_g"), 7, ink)
    L.text(M + 4 * mm, fy - 13 * mm, "・支払証明書としての利用は個別確認が必要",
           F("yu_g"), 7, ink)
    L.text(M + 4 * mm, fy - 19 * mm, "・領収書の発行主体は架空の組織",
           F("yu_g"), 7, ink)

    draw_seal(c, W - M - 20 * mm, M + 26 * mm, 16 * mm, "基金受付",
              HexColor("#b33a5a"), font_size=7.5)
    L.text(M, M + 20 * mm, "控除の取扱いは別途案内をご確認ください",
           F("yu_gb"), 7, rose)
    L.text(M, M + 14 * mm, "返金・振替はできません。", F("yu_g"), 6.5,
           HexColor("#777777"))
    draw_text(c, W / 2, 6 * mm, "架空テスト", F("yu_g"), 5.5,
              HexColor("#999999"), "center")


def case_v076(c):
    """Gift department store, wrapped food + wine, 8/10 mixed. Long receipt 75x230mm."""
    W, H = 75 * mm, 230 * mm
    c.setPageSize((W, H))
    L = Layout(c, W, H)
    M = 5 * mm
    maroon = HexColor("#6b1f2a")
    cream = HexColor("#fbf6ea")
    gold = HexColor("#b08a3e")

    draw_box(c, 0, 0, W, H, cream, radius=0)
    draw_box(c, 0, H - 24 * mm, W, 24 * mm, maroon, radius=0)
    L.text(W / 2, H - 8 * mm, "架空百貨店 テスト店", F("mincho"), 9.5, white,
           "center")
    L.text(W / 2, H - 13 * mm, "ギフト お買上票", F("yu_g"), 7, gold, "center")
    L.text(W / 2, H - 19 * mm, "2024年12月24日(火) 18:06", F("yu_g"), 6,
           HexColor("#f0dcc0"), "center")

    # register meta
    y = H - 30 * mm
    L.text(M, y, "レジ 03  /  担当 002", F("yu_g"), 6, maroon)
    L.text(W - M, y, "No.1024-7788", F("yu_g"), 6, maroon, "right")

    L.text(M, H - 34.5 * mm, "表示金額は税込", F("yu_g"), 5.4, maroon)

    # categories with 8/10 badges
    y -= 7 * mm
    items = [
        ("洋菓子詰合せ（ギフト）", "8%", 3200),
        ("和菓子詰合せ（ギフト）", "8%", 2800),
        ("スパークリングワイン", "10%", 4180),
        ("赤ワイン 750ml", "10%", 3520),
        ("チーズ詰合せ", "8%", 2400),
        ("ギフトラッピング", "10%", 330),
        ("のし・包装紙 手提げ", "10%", 220),
    ]
    for nm, rate, amt in items:
        L.text(M, y, nm, F("yu_g"), 6.4, maroon)
        L.text(W - M - 14 * mm, y, rate, F("yu_gb"), 5.6, gold, "right")
        L.text(W - M, y, money(amt), F("yu_g"), 6.4, maroon, "right")
        y -= 4.7 * mm

    # tax summary two rates
    y -= 2 * mm
    draw_h_line(c, M, y, W - M, 0.5, maroon, dash=[1, 1])
    y -= 4.5 * mm
    L.text(M, y, "8%対象 小計（税抜）", F("yu_g"), 6.2, maroon)
    L.text(W - M, y, "7,778", F("yu_g"), 6.2, maroon, "right")
    y -= 4.2 * mm
    L.text(M, y, "8%対象 消費税", F("yu_g"), 6.2, maroon)
    L.text(W - M, y, "622", F("yu_g"), 6.2, maroon, "right")
    y -= 4.2 * mm
    L.text(M, y, "10%対象 小計（税抜）", F("yu_g"), 6.2, maroon)
    L.text(W - M, y, "7,500", F("yu_g"), 6.2, maroon, "right")
    y -= 4.2 * mm
    L.text(M, y, "10%対象 消費税", F("yu_g"), 6.2, maroon)
    L.text(W - M, y, "750", F("yu_g"), 6.2, maroon, "right")

    # mixed totals
    y -= 4 * mm
    draw_h_line(c, M, y, W - M, 0.8, maroon)
    y -= 5 * mm
    L.text(M, y, "合計（税込）", F("yu_gb"), 7.5, maroon)
    L.text(W - M, y, "¥" + money(3200 + 2800 + 4180 + 3520 + 2400 + 330 + 220),
           F("gothic"), 12, maroon, "right", "total")

    # payment
    y -= 7 * mm
    L.text(M, y, "お支払：架空ギフトカード 10,000 + 現金 10,000", F("yu_g"), 6, maroon)
    y -= 4.5 * mm
    L.text(M, y, "お預り計 20,000 / お釣り " +
           money(20000 - (3200 + 2800 + 4180 + 3520 + 2400 + 330 + 220)),
           F("yu_g"), 6, maroon)

    # gift note
    y -= 7 * mm
    draw_box(c, M, y - 10 * mm, W - 2 * M, 11 * mm, white, gold, 0.6, 2)
    L.text(M + 2 * mm, y - 3.5 * mm, "ギフト用途：贈答用 / 送り先住所は別手配",
           F("yu_g"), 5.6, maroon)
    L.text(M + 2 * mm, y - 7.5 * mm, "のし：御歳暮 / 名入れ：なし", F("yu_g"),
           5.6, maroon)
    draw_text(c, W / 2, 4 * mm, "架空テスト", F("yu_g"), 5,
              HexColor("#999999"), "center")


def case_v077(c):
    """Catering partly business / partly private -> hold. A4 portrait, event sheet."""
    c.setPageSize(A4)
    W, H = A4
    L = Layout(c, W, H)
    M = 15 * mm
    olive = HexColor("#4f5d29")
    peach = HexColor("#fbe9dd")
    dark = HexColor("#2d2d2d")

    # header with diagonal accent
    c.saveState()
    c.setFillColor(peach)
    c.rect(0, H - 36 * mm, W, 36 * mm, stroke=0, fill=1)
    c.setFillColor(olive)
    c.rect(0, H - 36 * mm, W * 0.42, 36 * mm, stroke=0, fill=1)
    c.restoreState()
    L.text(M, H - 14 * mm, "ケータリング", F("mincho"), 18, white)
    L.text(M, H - 22 * mm, "お料理・飲料 ご精算書", F("yu_g"), 8,
           HexColor("#d8e0c4"))
    L.text(M, H - 30 * mm, "架空デリ クッキングテスト", F("yu_g"), 6.5,
           HexColor("#d8e0c4"))
    L.text(W - M, H - 14 * mm, "納品日 2023年6月17日", F("yu_g"), 8, olive,
           "right")
    L.text(W - M, H - 21 * mm, "請求日 2023年6月20日", F("yu_g"), 7.5, olive,
           "right")
    L.text(W - M, H - 28 * mm, "請求書 No. CT-2306-17", F("yu_g"), 7.5, olive,
           "right")

    # delivery info
    L.text(M, H - 46 * mm, "ご利用：テスト株式会社 創立記念パーティ（会場：架空町"
           "イベントホール）", F("yu_g"), 8, dark)
    L.text(M, H - 52 * mm, "人数：30名（内、社外のご家族：10名）", F("yu_g"), 8,
           dark)

    # dishes list
    ty = H - 62 * mm
    L.text(M, ty, "お品書きと数量", F("yu_gb"), 9, olive)
    ty -= 6 * mm
    rows = [
        ("オードブル盛合せ（1人前）", 30, 1800, 54000),
        ("メイン料理（ローストビーフ）", 30, 2600, 78000),
        ("サラダバー（1人前）", 30, 900, 27000),
        ("デザート盛合せ", 30, 800, 24000),
        ("ソフトドリンク飲放題", 30, 1200, 36000),
        ("アルコール（ビール/ワイン）", 1, 45000, 45000),
        ("会場運搬・設営費", 1, 18000, 18000),
    ]
    for i, r in enumerate(rows):
        yb = ty - i * 8 * mm
        if i % 2 == 0:
            draw_box(c, M, yb - 2.6 * mm, W - 2 * M, 8 * mm,
                     HexColor("#f8f6f2"), None, radius=0)
        L.text(M + 2 * mm, yb, r[0], F("mincho"), 8, dark)
        L.text(M + 90 * mm, yb, str(r[1]), F("yu_g"), 7.2, dark)
        L.text(M + 108 * mm, yb, money(r[2]), F("yu_g"), 7.2, dark)
        L.text(W - M - 2 * mm, yb, money(r[3]), F("yu_g"), 7.4, dark, "right")
    sub = sum(r[3] for r in rows)  # 282,000
    tax = sub // 10  # 28,200
    ty2 = ty - len(rows) * 8 * mm - 5 * mm
    L.text(M + 90 * mm, ty2, "小計（税抜）", F("yu_g"), 7.5, dark)
    L.text(W - M - 2 * mm, ty2, money(sub), F("yu_g"), 7.5, dark, "right")
    ty2 -= 5.5 * mm
    L.text(M + 90 * mm, ty2, "消費税（10%）", F("yu_g"), 7.5, dark)
    L.text(W - M - 2 * mm, ty2, money(tax), F("yu_g"), 7.5, dark, "right")
    ty2 -= 4 * mm
    draw_h_line(c, M + 80 * mm, ty2, W - M, 1.0, olive)
    ty2 -= 6 * mm
    L.text(M + 90 * mm, ty2, "ご請求合計", F("yu_gb"), 8.5, olive)
    L.text(W - M - 2 * mm, ty2, money(sub + tax), F("gothic"), 12, olive,
           "right", "total")

    # the ambiguity box
    ay = ty2 - 18 * mm
    draw_box(c, M, ay - 22 * mm, W - 2 * M, 30 * mm, HexColor("#fff6e8"),
             HexColor("#c49a3a"), 0.8, 3)
    L.text(M + 3 * mm, ay + 2 * mm, "配膳・撤収のご案内", F("yu_gb"), 8,
           HexColor("#7a5a00"))
    t = ("お料理と飲料は30名様分でご用意しました。"
         "食器・保温器具は当日20時に回収済みです。"
         "お持ち帰り品は十分に冷却し、お早めにお召し上がりください。")
    L.para(M + 3 * mm, ay - 4 * mm, t, F("yu_g"), 7, W - 2 * M - 6 * mm,
           4.4 * mm, HexColor("#5a4500"))

    # payment
    L.text(M, ay - 30 * mm, "お支払：2023年6月30日 銀行振込 入金確認済",
           F("yu_g"), 7, dark)
    L.text(M, ay - 35 * mm, "備考：残った料理はお持ち帰りいただきました。",
           F("yu_g"), 6.5, HexColor("#666666"))
    draw_seal(c, W - M - 15 * mm, ay - 33 * mm, 12 * mm, "クッキング",
              HexColor("#c0392b"), font_size=6)
    draw_text(c, M, 8 * mm, "架空テスト", F("yu_g"), 6, HexColor("#999999"))


def case_v078(c):
    """Expense reimbursement internal sheet, NOT merchant receipt -> hold.
    A4 portrait, form/sheet style with signature columns."""
    c.setPageSize(A4)
    W, H = A4
    L = Layout(c, W, H)
    M = 15 * mm
    gray = HexColor("#4a4f57")
    rule = HexColor("#9aa3ad")

    L.text(M, H - 16 * mm, "旅費交通費 精算内訳書（社内）", F("yu_gb"), 14, gray)
    L.text(M, H - 22 * mm, "申請部署：営業第二課 / 申請者：架空 太郎",
           F("yu_g"), 7.5, gray)
    L.text(W - M, H - 16 * mm, "提出日 2022/10/05", F("yu_g"), 7.5, gray,
           "right")
    L.text(W - M, H - 22 * mm, "No. EX-2210-088", F("yu_g"), 7.5, gray, "right")

    # form table
    ty = H - 36 * mm
    cols = [M, M + 22 * mm, M + 72 * mm, M + 105 * mm, M + 132 * mm, W - M]
    heads = ["日付", "内容", "領収書", "金額", "備考"]
    draw_box(c, M, ty, W - 2 * M, 7 * mm, gray, radius=0)
    for i, h in enumerate(heads):
        L.text(cols[i] + 2 * mm if i < 4 else W - M - 2 * mm,
               ty + 2.2 * mm, h, F("yu_gb"), 7, white,
               "left" if i < 4 else "right")
    exp = [
        ("10/02", "電車 テスト市→架空町", "なし", 640, "IC履歴のみ"),
        ("10/02", "タクシー（客先・緊急）", "あり（別添）", 2150, "領収書原本未提出"),
        ("10/03", "宿泊費（出張）", "あり（別添）", 8800, "宿泊先は架空ホテル"),
        ("10/04", "昼食代（社外）", "なし", 1200, "目的不明"),
        ("10/04", "電車 架空町→テスト市", "なし", 660, "IC履歴のみ"),
    ]
    rh = 9 * mm
    for i, e in enumerate(exp):
        yb = ty - 7 * mm - i * rh + 3 * mm
        if i % 2 == 0:
            draw_box(c, M, yb - 5 * mm, W - 2 * M, rh, HexColor("#f5f6f8"),
                     None, radius=0)
        L.text(cols[0] + 2 * mm, yb, e[0], F("yu_g"), 7.2, gray)
        L.text(cols[1] + 2 * mm, yb, e[1], F("yu_g"), 7.2, gray)
        L.text(cols[2] + 2 * mm, yb, e[2], F("yu_g"), 7, gray)
        L.text(cols[3] + 2 * mm, yb, money(e[3]), F("yu_g"), 7.2, gray)
        L.text(W - M - 2 * mm, yb, e[4], F("yu_g"), 6.5, gray, "right")
        draw_h_line(c, M, yb - 3.4 * mm, W - M, 0.3, rule)
    for xx in cols[1:-1]:
        draw_v_line(c, xx, ty - 7 * mm - len(exp) * rh + 3 * mm, ty, 0.3, rule)
    tot = sum(e[3] for e in exp)
    L.text(cols[3] + 2 * mm, ty - 7 * mm - len(exp) * rh - 3 * mm, "合計",
           F("yu_gb"), 8, gray)
    L.text(W - M - 2 * mm, ty - 7 * mm - len(exp) * rh - 3.5 * mm, money(tot),
           F("gothic"), 11, gray, "right", "total")

    # warning field
    wy = ty - 7 * mm - len(exp) * rh - 14 * mm
    draw_box(c, M, wy - 16 * mm, W - 2 * M, 20 * mm, HexColor("#fff4f4"),
             HexColor("#b04a4a"), 1.0, 3)
    L.text(M + 3 * mm, wy + 1 * mm, "注意：領収書未添付 3件", F("yu_gb"), 8,
           HexColor("#b04a4a"))
    L.text(M + 3 * mm, wy - 5 * mm,
           "本用紙は社内精算用の内訳書であり、外部事業者から受領した領収書では"
           "ありません。", F("yu_g"), 7, HexColor("#7a2a2a"))
    L.text(M + 3 * mm, wy - 11 * mm,
           "金額の裏付けとしての領収書原本・カード明細は別途必要です。",
           F("yu_g"), 7, HexColor("#7a2a2a"))

    # signature boxes
    sy = wy - 26 * mm
    labels = ["申請者", "課長", "経理"]
    bw = (W - 2 * M - 18 * mm) / 3.0
    for i, lab in enumerate(labels):
        bx = M + i * (bw + 9 * mm)
        draw_rect(c, bx, sy - 14 * mm, bw, 16 * mm, gray, None, 0.5)
        L.text(bx + 2 * mm, sy - 2 * mm, lab, F("yu_g"), 6.5, gray)
        fake_hand_line(c, bx + 8 * mm, sy - 11 * mm, "架空" if i == 0 else "",
                       9, HexColor("#2244aa"), jitter_seed=31 + i)
    L.text(M, 14 * mm, "架空テスト / 社内書式", F("yu_g"), 6,
           HexColor("#999999"))


def case_v079(c):
    """Cancellation fee versus originally refunded total. A4 portrait two-panel."""
    c.setPageSize(A4)
    W, H = A4
    L = Layout(c, W, H)
    M = 14 * mm
    coral = HexColor("#c0392b")
    blue = HexColor("#2c5c8f")
    pale = HexColor("#f2f4f8")

    L.text(W / 2, H - 15 * mm, "キャンセル精算のご案内", F("gothic"), 16, coral,
           "center", "title")
    L.text(W / 2, H - 21 * mm, "架空リゾート テストヴィラ 予約担当", F("yu_g"),
           7.5, HexColor("#555555"), "center")
    L.text(W / 2, H - 26 * mm, "発行 2023年8月30日", F("yu_g"), 7,
           HexColor("#555555"), "center")

    # two panels
    top = H - 38 * mm
    ph = 62 * mm
    draw_box(c, M, top - ph, W - 2 * M, ph, pale, HexColor("#c5cdd8"), 0.8, 2)
    L.text(M + 5 * mm, top - 9 * mm, "① 取消前のご予約（払戻済み）",
           F("yu_gb"), 9, blue)
    L.text(M + 8 * mm, top - 18 * mm, "ご宿泊 2023年9月16日〜18日 2泊 / 3名",
           F("yu_g"), 7.5, HexColor("#333333"))
    L.text(M + 8 * mm, top - 25 * mm, "ご予約金額（税込）", F("yu_g"), 7.5,
           HexColor("#333333"))
    L.text(W - M - 6 * mm, top - 26 * mm, "¥96,000", F("gothic"), 13, blue,
           "right")
    L.text(M + 8 * mm, top - 33 * mm, "払戻済み（2023/08/29 クレジット返金）",
           F("yu_g"), 7.5, HexColor("#333333"))
    L.text(W - M - 6 * mm, top - 34 * mm, "¥96,000", F("yu_g"), 8.5, blue,
           "right")
    L.text(M + 8 * mm, top - 41 * mm, "差引残額", F("yu_g"), 7.5,
           HexColor("#333333"))
    L.text(W - M - 6 * mm, top - 42 * mm, "¥0", F("yu_g"), 8.5, blue, "right")
    L.text(M + 8 * mm, top - 51 * mm,
           "予約番号 RSV-7702 / お支払いはカード一括（架空カード 下4桁 0000）",
           F("yu_g"), 6.5, HexColor("#666666"))

    # second panel cancellation fee
    top2 = top - ph - 8 * mm
    draw_box(c, M, top2 - ph, W - 2 * M, ph, HexColor("#fdf1ef"),
             HexColor("#e0a79e"), 0.8, 2)
    L.text(M + 5 * mm, top2 - 9 * mm, "② キャンセル料の内訳", F("yu_gb"), 9,
           coral)
    L.text(M + 8 * mm, top2 - 17 * mm, "キャンセル日 2023年8月28日", F("yu_g"),
           7.5, HexColor("#333333"))
    L.text(M + 8 * mm, top2 - 23 * mm, "取消規定：宿泊日14日前まで 無料",
           F("yu_g"), 7, HexColor("#555555"))
    L.text(M + 8 * mm, top2 - 29 * mm, "           7日前〜2日前まで 30%",
           F("yu_g"), 7, HexColor("#555555"))
    L.text(M + 8 * mm, top2 - 35 * mm, "           前日・当日 100%",
           F("yu_g"), 7, HexColor("#555555"))
    L.text(M + 8 * mm, top2 - 43 * mm, "本件：宿泊日19日前 → 無料取消", F("yu_gb"),
           7.5, coral)
    L.text(M + 8 * mm, top2 - 51 * mm, "キャンセル料（税込）", F("yu_g"), 7.5,
           HexColor("#333333"))
    L.text(W - M - 6 * mm, top2 - 52 * mm, "¥0", F("gothic"), 13, coral,
           "right", "cancel-fee")

    # bottom explanatory block
    by = top2 - ph - 12 * mm
    L.text(M, by, "ご確認ください", F("yu_gb"), 8.5, coral)
    t = ("本ページは、払戻し済みのご予約金額 ¥96,000 と、キャンセル料 ¥0 を"
         "対比して示す精算案内です。合計額はキャンセル料の ¥0 であり、"
         "¥96,000 は元の予約金額（払戻済み）です。二重請求・二重払戻はありません。")
    L.para(M, by - 7 * mm, t, F("yu_g"), 7.2, W - 2 * M, 4.6 * mm,
           HexColor("#444444"))
    draw_seal(c, W - M - 18 * mm, by - 24 * mm, 13 * mm, "テストヴィラ",
              HexColor("#b04444"), font_size=6)
    L.text(M, 14 * mm, "架空テスト / ご予約に関するご案内", F("yu_g"), 6,
           HexColor("#999999"))


def case_v080(c):
    """Refund against prior transaction (negative total). Custom 150x220."""
    W, H = 150 * mm, 220 * mm
    c.setPageSize((W, H))
    L = Layout(c, W, H)
    M = 12 * mm
    purple = HexColor("#4a3b78")
    mint = HexColor("#e8f5f1")
    ink = HexColor("#26212f")

    draw_box(c, 0, 0, W, H, mint, radius=0)
    # left thick border
    draw_box(c, 0, 0, 7 * mm, H, purple, radius=0)
    L.text(M + 4 * mm, H - 18 * mm, "返金手続き 完了のお知らせ", F("gothic"), 14,
           purple)
    L.text(M + 4 * mm, H - 25 * mm, "架空ストア テスト店 / カスタマーサポート",
           F("yu_g"), 7, HexColor("#555555"))
    L.text(W - M, H - 18 * mm, "発行 2025-02-14", F("yu_g"), 7, purple, "right")
    L.text(W - M, H - 23 * mm, "処理番号 RF-2502-1180", F("yu_g"), 7, purple,
           "right")

    # original transaction
    oy = H - 40 * mm
    L.text(M + 4 * mm, oy, "【元のご購入】", F("yu_gb"), 8, purple)
    L.text(M + 4 * mm, oy - 7 * mm, "ご購入日 2025年1月28日 / 注文番号 ORD-9901",
           F("yu_g"), 7.5, ink)
    L.text(M + 4 * mm, oy - 13 * mm, "商品：ワイヤレスイヤホン（架空ブランド）",
           F("yu_g"), 7.5, ink)
    L.text(M + 4 * mm, oy - 19 * mm, "当時のお支払額（税込）", F("yu_g"), 7.5,
           ink)
    L.text(W - M, oy - 20 * mm, "¥14,850", F("yu_g"), 9, ink, "right")
    L.text(M + 4 * mm, oy - 26 * mm, "返金理由：初期不良（返品受領 2/10）",
           F("yu_g"), 7, HexColor("#555555"))

    # refund amount big, negative
    ry = oy - 44 * mm
    draw_box(c, M + 4 * mm, ry - 20 * mm, W - 2 * M - 8 * mm, 28 * mm, white,
             purple, 1.0, 4)
    L.text(M + 8 * mm, ry + 1 * mm, "返金額", F("yu_gb"), 9, purple)
    L.text(W - M - 8 * mm, ry - 2 * mm, "-¥14,850", F("gothic"), 26, purple,
           "right", "refund-total")
    L.text(M + 8 * mm, ry - 8 * mm, "商品代金の全額返金", F("yu_g"), 7,
           HexColor("#555555"))
    L.text(W - M - 8 * mm, ry - 12 * mm, "返金方法：元のカードへ返金", F("yu_g"),
           6.5, HexColor("#555555"), "right")

    # breakdown
    by = ry - 34 * mm
    L.text(M + 4 * mm, by, "内訳", F("yu_gb"), 8, purple)
    L.text(M + 8 * mm, by - 6 * mm, "商品代金（税抜）", F("yu_g"), 7, ink)
    L.text(W - M, by - 6 * mm, "13,500", F("yu_g"), 7, ink, "right")
    L.text(M + 8 * mm, by - 12 * mm, "消費税（10%）", F("yu_g"), 7, ink)
    L.text(W - M, by - 12 * mm, "1,350", F("yu_g"), 7, ink, "right")
    draw_h_line(c, M + 8 * mm, by - 15 * mm, W - M, 0.6, purple)
    L.text(M + 8 * mm, by - 20 * mm, "返金合計", F("yu_gb"), 8, purple)
    L.text(W - M, by - 20.5 * mm, "-14,850", F("gothic"), 11, purple, "right")

    # notes
    L.text(M + 4 * mm, by - 33 * mm,
           "カード会社の処理後に明細へ反映されます（目安3〜10営業日）。",
           F("yu_g"), 6.5, HexColor("#555555"))
    L.text(M + 4 * mm, by - 38 * mm,
           "この書面は返金処理の記録であり、新たな請求書ではありません。",
           F("yu_g"), 6.5, HexColor("#555555"))
    draw_stamp_rect(c, W - M - 30 * mm, by - 56 * mm, 28 * mm, 12 * mm, "返金完了",
                    "2025/02/14", purple, 7)
    draw_text(c, M + 4 * mm, 7 * mm, "架空テスト", F("yu_g"), 5.8,
              HexColor("#888888"))


# ---------------------------------------------------------------- engine ----

_REGISTERED = False


def _register_fonts_done():
    global _REGISTERED
    if not _REGISTERED:
        register_fonts()
        _REGISTERED = True


def _soft_check(report, cid):
    if report:
        sys.stderr.write("[%s] soft collision notes: %r\n" % (cid, report[:6]))


MANIFEST = [{'case_id': 'V061',
  'file': 'individual/V061.pdf',
  'title': '印刷工房の詳細請求明細書（入金済）',
  'layout_id': 'flash1017-01',
  'layout_description': '210×160mm横長。左の縦書き帯、右上の大型タイトル、左の発行者・宛先・入金確認、中央の品目/仕様・数量・単価・金額・税率5列表、右下の税抜値引と税額を含む積み上げ、左下の角印。',
  'document_kind': 'invoice_receipt',
  'merchant': 'イラスト印刷工房 こもれび',
  'date': '2026-02-03',
  'date_printed': 'お支払済み（2026-02-03 着金）',
  'total': 187330,
  'acceptable_accounts': ['印刷費', '広告宣伝費', '販売促進費'],
  'expected_decision': 'propose',
  'human_context': 'テスト株式会社が営業部の販促物として印刷を発注し、2026-02-03に入金確認済み。値引後の税込金額が入金額と一致する。',
  'review_questions': ['値引は割引後の総額で仕入税額控除の計算を行っているか？', '名刺代は接待交際費と販売促進費のどちらで整理するか？'],
  'features': ['横長A5', '税抜小計+税+値引', '入金確認印', '発行日と入金日'],
  'items': [{'name': '会社案内パンフレット', 'gross': 92400, 'rate': '10'},
            {'name': '名刺（社員用）', 'gross': 34320, 'rate': '10'},
            {'name': '封筒 角2', 'gross': 18150, 'rate': '10'},
            {'name': 'クリアファイル', 'gross': 41800, 'rate': '10'},
            {'name': '納品書（複写）', 'gross': 3960, 'rate': '10'},
            {'name': '値引（既存顧客）', 'gross': -3300, 'rate': '10'}]},
 {'case_id': 'V062',
  'file': 'individual/V062.pdf',
  'title': '名刺印刷の見積書（未払い）',
  'layout_id': 'flash1017-02',
  'layout_description': 'B5縦。緑の太帯が上部全面のタイトル面と左の宛先枠を兼ね、右に発行者。上部に大型見積金額と赤枠の未払注意。下半分に用紙・数量・単価・備考の細かい列で構成し、合計は品目表の右下外に置く。',
  'document_kind': 'invoice_unpaid',
  'merchant': 'さくら製版印刷 株式会社',
  'date': '2027-05-28',
  'date_printed': '見積日 2027年5月28日',
  'total': 64900,
  'acceptable_accounts': [],
  'expected_decision': 'block',
  'human_context': '見積書であり領収書ではない。発注・納品・入金のいずれも未実施で支払いの証明にならない。',
  'review_questions': ['正式な発注書は存在するか？', '有効期限 2027-06-30 に発注済みか？'],
  'features': ['見積書', 'B5縦', '見積金額のみ', '有効期限'],
  'items': [{'name': '名刺 片面フルカラー', 'gross': 43560, 'rate': '10'},
            {'name': '名刺 両面フルカラー', 'gross': 14850, 'rate': '10'},
            {'name': '箔押し加工（金）', 'gross': 3300, 'rate': '10'},
            {'name': 'データ校正費', 'gross': 2310, 'rate': '10'},
            {'name': '送料', 'gross': 880, 'rate': '10'}]},
 {'case_id': 'V063',
  'file': 'individual/V063.pdf',
  'title': 'モニター修理の受付票兼領収書（80mm）',
  'layout_id': 'flash1017-03',
  'layout_description': '80mm×190mmの縦長サーマル形状。見出しと発行者を中央揃え、受付カードのフィールドを左ラベル・右値で縦に並べ、作業明細は行ごとの左右金額、下部に色付きの預り・釣銭枠と角印。',
  'document_kind': 'thermal_receipt',
  'merchant': 'パソコン修理 みつばち電機',
  'date': '2025-11-11',
  'date_printed': '領収日 2025/11/11　現金',
  'total': 39380,
  'acceptable_accounts': ['修繕費'],
  'expected_decision': 'propose',
  'human_context': '27インチモニターのパネル交換修理。2025-11-11に現金で支払い完了し、預り40,000円から釣銭620円。',
  'review_questions': ['修理対象のモニターは事業共用か私用か？', '保証明細は修繕費の証憑に添付されているか？'],
  'features': ['80mm熱', '修理受付票', '現金', 'お釣り', '作業明細'],
  'items': [{'name': '技術料', 'gross': 9680, 'rate': '10'},
            {'name': '部品代 LCDパネル', 'gross': 26400, 'rate': '10'},
            {'name': '部品代 バックライト', 'gross': 2090, 'rate': '10'},
            {'name': '送料', 'gross': 1210, 'rate': '10'}]},
 {'case_id': 'V064',
  'file': 'individual/V064.pdf',
  'title': '自作PCパーツのカード領収明細（資産確認用）',
  'layout_id': 'flash1017-04',
  'layout_description': 'A4縦。紺の縦ガター、上部の大見出しとカード支払情報、カテゴリ・仕様・数量・単価・金額・税率の6列10行、下部右の総額と黄色の保証枠。',
  'document_kind': 'invoice_receipt',
  'merchant': '架空パーツ館 テスト店',
  'date': '2024-03-09',
  'date_printed': '購入日 2024-03-09',
  'total': 284130,
  'acceptable_accounts': ['工具器具備品', '備品費'],
  'expected_decision': 'asset_review',
  'human_context': '業務用の自作PC一式とモニターをカードで購入。構成単位、付随費用、資産計上の要否を確認する必要がある。金額だけで処理方法は確定しない。',
  'review_questions': ['PC本体として固定資産に計上するか、構成部品ごとに分けるか？', 'OSライセンスとセットアップ費用の資産性はどう判断するか？'],
  'features': ['A4縦', '部品別明細', '資産確認候補', '保証・動作確認'],
  'items': [{'name': 'CPU', 'gross': 69080, 'rate': '10'},
            {'name': 'マザーボード', 'gross': 38280, 'rate': '10'},
            {'name': 'メモリ', 'gross': 25960, 'rate': '10'},
            {'name': 'SSD', 'gross': 39380, 'rate': '10'},
            {'name': '電源', 'gross': 18480, 'rate': '10'},
            {'name': 'CPUクーラー', 'gross': 9790, 'rate': '10'},
            {'name': 'ケース', 'gross': 13640, 'rate': '10'},
            {'name': 'ケースFAN', 'gross': 5940, 'rate': '10'},
            {'name': 'OS・セットアップ', 'gross': 19800, 'rate': '10'},
            {'name': 'モニター', 'gross': 43780, 'rate': '10'}]},
 {'case_id': 'V065',
  'file': 'individual/V065.pdf',
  'title': '家具の配送・組立完了書（兼領収書）',
  'layout_id': 'flash1017-05',
  'layout_description': '100mm×297mmの細長い縦帯。上部46mmの茶色帯にタイトルと発行日、クリーム色の本文に納品先・届け日、商品と数量・金額を積み上げ、右下に丸印、下部に白枠の問い合わせ欄。',
  'document_kind': 'invoice_receipt',
  'merchant': 'テスト市家具 リビング館',
  'date': '2023-09-21',
  'date_printed': '2023/09/21',
  'total': 273900,
  'acceptable_accounts': ['工具器具備品', '備品費'],
  'expected_decision': 'asset_review',
  'human_context': '会議用テーブル・チェア等の家具を法人向けに納品・組立し、カードで273,900円を一括領収。複数家具と組立費を含むため、構成単位と資産計上の要否を確認する。',
  'review_questions': ['テーブル等は備品か消耗品か？', '組立費は本体価格に含めるのか別科目か？'],
  'features': ['100mm幅縦長', '配送組立費込み', 'カード払い', '納品サイン'],
  'items': [{'name': '会議用テーブル 1800×900', 'gross': 74800, 'rate': '10'},
            {'name': 'ワークチェア メッシュ', 'gross': 105600, 'rate': '10'},
            {'name': 'サイドキャビネット', 'gross': 69300, 'rate': '10'},
            {'name': '配達・設置・組立費', 'gross': 24200, 'rate': '10'}]},
 {'case_id': 'V066',
  'file': 'individual/V066.pdf',
  'title': 'カメラ機材レンタルの精算書',
  'layout_id': 'flash1017-06',
  'layout_description': 'A5横。上部を3つの格子セル（濃紺の題字・薄灰の契約者・薄灰の貸出日）に分割し、右に大型合計。下段は品番・数量・日数の表で縞模様、最下部に延滞条件と返却済の角印。',
  'document_kind': 'voucher',
  'merchant': '架空撮影機材 テスト市レンタル',
  'date': '2024-04-10',
  'date_printed': '支払日 2024年4月10日 / 全額領収済',
  'total': 73590,
  'acceptable_accounts': ['賃借料', 'レンタル料'],
  'expected_decision': 'propose',
  'human_context': '法人の映像プロジェクト用に3日間の機材を借用。予約金10,000円と返却時精算63,590円を合わせ、2024-04-10に73,590円を全額領収済み。',
  'review_questions': ['予約金は合計に既に含まれるか？', 'レンタル保険の免責は経費計上済みか？'],
  'features': ['A5横', '機材レンタル', '予約金充当', '返却印'],
  'items': [{'name': 'シネカメラ本体', 'gross': 39600, 'rate': '10'},
            {'name': 'レンズ 24-70mm', 'gross': 9900, 'rate': '10'},
            {'name': 'レンズ 70-200mm', 'gross': 8250, 'rate': '10'},
            {'name': '三脚', 'gross': 3960, 'rate': '10'},
            {'name': 'LEDパネル', 'gross': 11880, 'rate': '10'}]},
 {'case_id': 'V067',
  'file': 'individual/V067.pdf',
  'title': 'レコーディングスタジオのセッション利用請求書',
  'layout_id': 'flash1017-07',
  'layout_description': 'A4縦。中央寄せの縦書き風ロゴと細い区切線、利用日ごとの時間帯・室内・エンジニア・金額の5列ログ、追加機材の行、下部に横長の紫の合計帯、右下に丸印。',
  'document_kind': 'invoice_receipt',
  'merchant': '架空スタジオ 音色',
  'date': '2022-12-20',
  'date_printed': 'お支払方法：銀行振込（2022年12月20日 入金確認）',
  'total': 291280,
  'acceptable_accounts': ['賃借料', '外注費'],
  'expected_decision': 'propose',
  'human_context': '11月に5回のスタジオ利用とミックス、機材レンタル、HDD購入。2022-12-20入金確認。',
  'review_questions': ['HDD購入はスタジオ料と分けるべきか？', '5回の利用はどの案件に紐づくか？'],
  'features': ['A4縦', '日付別セッションログ', '追加機材', '入金確認'],
  'items': [{'name': 'スタジオ利用 11月2日', 'gross': 46200, 'rate': '10'},
            {'name': 'スタジオ利用 11月4日', 'gross': 24200, 'rate': '10'},
            {'name': 'スタジオ利用 11月9日', 'gross': 55000, 'rate': '10'},
            {'name': 'スタジオ利用 11月15日', 'gross': 69300, 'rate': '10'},
            {'name': 'スタジオ利用 11月24日', 'gross': 49500, 'rate': '10'},
            {'name': 'ミックスダウン・エンジニア', 'gross': 30800, 'rate': '10'},
            {'name': '外部マイクレンタル', 'gross': 6600, 'rate': '10'},
            {'name': 'HDD持ち出し（2TB, 販売）', 'gross': 9680, 'rate': '10'}]},
 {'case_id': 'V068',
  'file': 'individual/V068.pdf',
  'title': '広告出稿の媒体別支払済サマリー',
  'layout_id': 'flash1017-08',
  'layout_description': '279×170mm横長。左上にオレンジの大見出しブロック、右に角丸の白パネルで合計金額を強調。中央の媒体別表には表示回数と支出、構成比のミニバー、右に緑の支払状況パネル。',
  'document_kind': 'statement',
  'merchant': '架空広告代理店 ネクストテスト',
  'date': '2025-08-25',
  'date_printed': '支払日 2025-08-25',
  'total': 1342000,
  'acceptable_accounts': ['広告宣伝費', '販売促進費', '雑費'],
  'expected_decision': 'propose',
  'human_context': '7月のSNS・検索・バナー・記事広告の支出。2025-08-25に振込完了し残高0。',
  'review_questions': ['媒体ごとの請求書も保存しているか？', 'A/Bテスト分は広告宣伝費に一括してよいか？'],
  'features': ['A4横', '媒体別サマリー', '構成比バー', '全額入金済'],
  'items': [{'name': 'SNS動画広告', 'gross': 462000, 'rate': '10'},
            {'name': '検索連動型', 'gross': 418000, 'rate': '10'},
            {'name': '静止画バナー', 'gross': 264000, 'rate': '10'},
            {'name': '記事広告', 'gross': 198000, 'rate': '10'}]},
 {'case_id': 'V069',
  'file': 'individual/V069.pdf',
  'title': '卸売商品の仕入請求書（入金済）',
  'layout_id': 'flash1017-09',
  'layout_description': '148×210mmの縦長。全面クリーム地で二重罫線枠、中央に明朝のタイトル、右上に請求書番号と発行日。入数・単価・金額の4列表、右下に積み上げ合計、左下に返品条件、右下に丸印。',
  'document_kind': 'invoice_receipt',
  'merchant': '架空物産卸 株式会社',
  'date': '2024-02-20',
  'date_printed': '入金：2024年2月20日 普通預金口座へ着金',
  'total': 230340,
  'acceptable_accounts': ['仕入高', '消耗品費'],
  'expected_decision': 'propose',
  'human_context': 'テスト小売株式会社への卸売仕入。2024-02-20に入金済み。',
  'review_questions': ['仕入先との税込/税抜契約はどちらか？', '食品保存容器の賞味/賞味期限扱いは？'],
  'features': ['縦長', '二重罫線枠', '入数表', '入金済'],
  'items': [{'name': '収納ボックス（中）', 'gross': 38016, 'rate': '10'},
            {'name': 'ステンレスボトル 500ml', 'gross': 41712, 'rate': '10'},
            {'name': 'タオルセット 3枚組', 'gross': 36432, 'rate': '10'},
            {'name': '洗剤セット A', 'gross': 46200, 'rate': '10'},
            {'name': 'LEDデスクライト', 'gross': 43560, 'rate': '10'},
            {'name': '食品保存容器 4個組', 'gross': 24420, 'rate': '10'}]},
 {'case_id': 'V070',
  'file': 'individual/V070.pdf',
  'title': '包材ショップの小型レシート',
  'layout_id': 'flash1017-10',
  'layout_description': '120×80mmの横長スリップ。上部に青の帯、品目・数量・金額の3列を左寄せ、最下部に薄青の合計枠と現金の預り・釣銭を左右に分けて配置。',
  'document_kind': 'thermal_receipt',
  'merchant': 'かんぷパッケージ テスト店',
  'date': '2025-06-03',
  'date_printed': '2025/06/03 11:42',
  'total': 7370,
  'acceptable_accounts': ['消耗品費', '荷造運賃'],
  'expected_decision': 'propose',
  'human_context': '段ボール・緩衝材・袋など梱包資材の購入。現金10,000円で支払い、釣銭2,630円。',
  'review_questions': ['段ボール代は包材費か消耗品費か？'],
  'features': ['120×80mm', '小型レシート', '現金', 'お釣り'],
  'items': [{'name': '段ボール箱 A4（20枚組）', 'gross': 1848, 'rate': '10'},
            {'name': '緩衝材 プチプチ 50m', 'gross': 2420, 'rate': '10'},
            {'name': 'クラフト紙袋 中', 'gross': 1760, 'rate': '10'},
            {'name': 'OPP袋 100枚', 'gross': 924, 'rate': '10'},
            {'name': 'ガムテープ 透明 50mm', 'gross': 418, 'rate': '10'}]},
 {'case_id': 'V071',
  'file': 'individual/V071.pdf',
  'title': '電子部品の納品書兼受領確認（高密度品目表）',
  'layout_id': 'flash1017-11',
  'layout_description': 'A4横。上部に小さめのタイトルと発行日、右詰め店名。14行の部品表を '
                        '#・品名/型式・JAN・数量・単価・金額・分類の7列で高密度に並べ、下に受領確認の角印と朱色の注意書き。',
  'document_kind': 'other',
  'merchant': '架空電子 部品センター テスト支店',
  'date': '2024-06-14',
  'date_printed': '発行 2024-06-14 / 対象仕入 2024-05-31',
  'total': 587290,
  'acceptable_accounts': [],
  'expected_decision': 'block',
  'human_context': '電子部品14点の納品書。品目・数量・金額は記載されているが、支払の証明ではない。実支払は買掛として翌月決済の想定。',
  'review_questions': ['支払日の確定情報は買掛台帳にあるか？', '材料費と消耗品費の区分基準は？'],
  'features': ['A4横', '14行高密度', '部品表', '受領確認印', '支払証明でない'],
  'items': [{'name': '積層セラミックコンデンサ', 'gross': 16500, 'rate': '10'},
            {'name': '抵抗器 1/4W 10kΩ', 'gross': 6600, 'rate': '10'},
            {'name': '電解コンデンサ 470µF', 'gross': 26400, 'rate': '10'},
            {'name': 'LED 赤 3mm', 'gross': 13200, 'rate': '10'},
            {'name': 'コネクタ 40P', 'gross': 20900, 'rate': '10'},
            {'name': '基板用スペーサー', 'gross': 17600, 'rate': '10'},
            {'name': 'スイッチ タクト', 'gross': 13200, 'rate': '10'},
            {'name': 'ジャンパーワイヤ', 'gross': 13200, 'rate': '10'},
            {'name': 'リレー 5V', 'gross': 125400, 'rate': '10'},
            {'name': 'ヒートシンク 小型', 'gross': 57200, 'rate': '10'},
            {'name': 'テープ 絶縁 耐熱', 'gross': 43120, 'rate': '10'},
            {'name': '静電気防止袋 100枚', 'gross': 40920, 'rate': '10'},
            {'name': 'はんだ 鉛フリー', 'gross': 47850, 'rate': '10'},
            {'name': 'アクリルケース', 'gross': 145200, 'rate': '10'}]},
 {'case_id': 'V072',
  'file': 'individual/V072.pdf',
  'title': '翻訳業務の報酬計算書（源泉検討）',
  'layout_id': 'flash1017-12',
  'layout_description': 'A4縦。上部に薄い砂色の帯と明朝の大タイトル、報酬明細は品名・内容・金額の3列積み上げ、中央に茶色の源泉検討ボックス、下部に消費税と支払額の帯。',
  'document_kind': 'statement',
  'merchant': '架空製作 株式会社 出版部',
  'date': '2025-12-18',
  'date_printed': '計算日：2025年12月18日',
  'total': 440000,
  'acceptable_accounts': [],
  'expected_decision': 'block',
  'human_context': '技術文書翻訳の報酬。源泉徴収の取扱いは未確定で、本計算書は支払前の社内確認資料。実際の入金は確定後。',
  'review_questions': ['業務委託契約の区分はどれか？', '源泉徴収する場合は控除後の入金額はいくらか？'],
  'features': ['A4縦', '源泉検討欄', '税込支払額', '支払前'],
  'items': [{'name': '技術文書 英→日 翻訳', 'gross': 323400, 'rate': '10'},
            {'name': '校正・用語統一', 'gross': 88000, 'rate': '10'},
            {'name': '専門用語データ整備', 'gross': 28600, 'rate': '10'}]},
 {'case_id': 'V073',
  'file': 'individual/V073.pdf',
  'title': 'オンライン研修の年間サブスク支払明細',
  'layout_id': 'flash1017-13',
  'layout_description': '124×176mmの縦長アプリ風。濃紫のヘッダー、中央に巨大な合計金額、薄緑の角丸計画カード（期間・受講枠）、税込の積み上げ、灰のカード情報帯。表の罫線なし。',
  'document_kind': 'invoice_receipt',
  'merchant': '学習ラボ オンライン',
  'date': '2024-04-01',
  'date_printed': '2024/04/01',
  'total': 39600,
  'acceptable_accounts': ['研修費', '教育研修費'],
  'expected_decision': 'propose',
  'human_context': '法人向けPROプランを年額一括払い。2024-04-01にカード決済完了、領収済み。',
  'review_questions': ['管理者アカウント費用の按分は？', '受講者20名の中に非従業員は含まれるか？'],
  'features': ['縦長アプリ風', 'カード決済', '年間契約', 'カード下4桁'],
  'items': [{'name': '年間PROプラン 基本料金（税込）', 'gross': 39600, 'rate': '10'}]},
 {'case_id': 'V074',
  'file': 'individual/V074.pdf',
  'title': '専門職協会の会費領収書（年度・日付の記載なし）',
  'layout_id': 'flash1017-14',
  'layout_description': 'B5縦の式典風デザイン。上部に円形の紋章、明朝の協会名と「会費 '
                        '領収書」、左に大きな金額、右に発行日欄（空欄）と会員番号、下に種別・対象年度・支払区分の行、最下部に赤い理事長印。',
  'document_kind': 'voucher',
  'merchant': '架空専門職 協会',
  'date': '',
  'date_printed': '発行日：\u3000\u3000年\u3000\u3000月\u3000\u3000日（記載なし）',
  'total': 18000,
  'acceptable_accounts': ['諸会費'],
  'expected_decision': 'hold',
  'human_context': '正会員年会費の領収書だが、発行日と対象年度が空欄で所属年度が特定できない。',
  'review_questions': ['この会費は何年度分か？', '領収日の記載された別案内はあるか？'],
  'features': ['B5縦', '年度空白', '日付空欄', '紋章'],
  'items': [{'name': '正会員 年会費', 'gross': 18000, 'rate': 'unknown'}]},
 {'case_id': 'V075',
  'file': 'individual/V075.pdf',
  'title': '慈善基金の寄附受領証（業務と切り離し）',
  'layout_id': 'flash1017-15',
  'layout_description': '182×257mmの大判。上62mmを濃いローズの帯、右に二重円の装飾、下に大きな受領額、寄附者を淡い帯で囲み、目的区分の説明と控除参考の行、右下に丸印。',
  'document_kind': 'voucher',
  'merchant': '架空こども支援基金',
  'date': '2025-03-02',
  'date_printed': '令和7年3月2日',
  'total': 30000,
  'acceptable_accounts': [],
  'expected_decision': 'hold',
  'human_context': '個人としての社会貢献目的の寄附。業務や取引関係と切り離して扱う必要があり、事業関連性は受領証から確認できない。',
  'review_questions': ['事業との関連・取引上の関係はあるか？', '寄附者名義は個人か法人か？'],
  'features': ['大判', '寄附受領証', '業務外', '令和', '丸印'],
  'items': [{'name': '寄附金', 'gross': 30000, 'rate': 'unknown'}]},
 {'case_id': 'V076',
  'file': 'individual/V076.pdf',
  'title': '百貨店ギフトの8%・10%混在レシート',
  'layout_id': 'flash1017-16',
  'layout_description': '75×230mmの非常に細長い縦レシート。上部に深紅の帯、品目ごとに左に品名・真ん中に税率バッジ・右に金額、下に8%と10%の税抜/税の4行サマリー、さらに税込合計とギフト用途の白枠。',
  'document_kind': 'thermal_receipt',
  'merchant': '架空百貨店 テスト店',
  'date': '2024-12-24',
  'date_printed': '2024年12月24日(火) 18:06',
  'total': 16650,
  'acceptable_accounts': [],
  'expected_decision': 'hold',
  'human_context': '食品詰合せとワイン、包装のギフト購入。贈答用で私用の可能性が高く、用途の特定情報がない。税率8%と10%が混在。',
  'review_questions': ['贈答先は取引先か個人的な知人か？', 'ギフト部分を交際費にする根拠はあるか？'],
  'features': ['75mm縦極細', '8%/10%混在', '税込品目と内税', 'ギフトカード併用', '現金釣銭3,350円'],
  'items': [{'name': '洋菓子詰合せ（ギフト）', 'gross': 3200, 'rate': '8'},
            {'name': '和菓子詰合せ（ギフト）', 'gross': 2800, 'rate': '8'},
            {'name': 'スパークリングワイン', 'gross': 4180, 'rate': '10'},
            {'name': '赤ワイン 750ml', 'gross': 3520, 'rate': '10'},
            {'name': 'チーズ詰合せ', 'gross': 2400, 'rate': '8'},
            {'name': 'ギフトラッピング', 'gross': 330, 'rate': '10'},
            {'name': 'のし・包装紙 手提げ', 'gross': 220, 'rate': '10'}]},
 {'case_id': 'V077',
  'file': 'individual/V077.pdf',
  'title': 'ケータリング精算書（業務と私用の混在）',
  'layout_id': 'flash1017-17',
  'layout_description': 'A4縦。上36mmを左オリーブ・右薄桃の2色帯、オードブル等の品名・数量・単価・金額の表、下部に橙の枠で配膳・撤収の案内、右に丸印。',
  'document_kind': 'invoice_receipt',
  'merchant': '架空デリ クッキングテスト',
  'date': '2023-06-30',
  'date_printed': 'お支払：2023年6月30日 銀行振込 入金確認済',
  'total': 310200,
  'acceptable_accounts': [],
  'expected_decision': 'hold',
  'human_context': '創立記念パーティ用ケータリングを2023-06-30に全額振込。30名中に家族10名が含まれ、業務部分との按分根拠は未確認。私用分を含むか確認するまで候補を保留する。',
  'review_questions': ['社員懇親と家族招待の人数比は？', 'アルコール代の事業関連性はどう説明できるか？'],
  'features': ['A4縦', '2色帯', 'ケータリング', '業務/私用混在'],
  'items': [{'name': 'オードブル盛合せ（1人前）', 'gross': 59400, 'rate': '10'},
            {'name': 'メイン料理（ローストビーフ）', 'gross': 85800, 'rate': '10'},
            {'name': 'サラダバー（1人前）', 'gross': 29700, 'rate': '10'},
            {'name': 'デザート盛合せ', 'gross': 26400, 'rate': '10'},
            {'name': 'ソフトドリンク飲放題', 'gross': 39600, 'rate': '10'},
            {'name': 'アルコール（ビール/ワイン）', 'gross': 49500, 'rate': '10'},
            {'name': '会場運搬・設営費', 'gross': 19800, 'rate': '10'}]},
 {'case_id': 'V078',
  'file': 'individual/V078.pdf',
  'title': '社内旅費交通費の精算内訳書（事業者領収書でない）',
  'layout_id': 'flash1017-18',
  'layout_description': 'A4縦の社内帳票。上部に申請部署と提出日、日付・内容・領収書・金額・備考の表、赤い注意枠に領収書未添付の件数、下部に申請者・課長・経理の署名欄3つと青い手書き風署名。',
  'document_kind': 'statement',
  'merchant': '',
  'date': '',
  'date_printed': '提出日 2022/10/05',
  'total': 13450,
  'acceptable_accounts': [],
  'expected_decision': 'hold',
  'human_context': '社員が作成した社内精算用の内訳書。外部事業者が発行する領収書ではなく、領収書未添付3件・目的不明の昼食代を含む。支払の証明にならない。',
  'review_questions': ['未添付の領収書原本はあるか？', '昼食代の目的は何か？'],
  'features': ['A4縦帳票', '社内精算', '領収書未添付', '署名欄'],
  'items': [{'name': '電車 テスト市→架空町', 'gross': 640, 'rate': 'unknown'},
            {'name': 'タクシー（客先・緊急）', 'gross': 2150, 'rate': 'unknown'},
            {'name': '宿泊費（出張）', 'gross': 8800, 'rate': 'unknown'},
            {'name': '昼食代（社外）', 'gross': 1200, 'rate': 'unknown'},
            {'name': '電車 架空町→テスト市', 'gross': 660, 'rate': 'unknown'}]},
 {'case_id': 'V079',
  'file': 'individual/V079.pdf',
  'title': 'キャンセル料と払戻済み予約金額の対比案内',
  'layout_id': 'flash1017-19',
  'layout_description': 'A4縦の2パネル構成。上パネルは取消前の予約・払戻・残額、下パネルは取消規定とキャンセル料。下部に二重請求でないことを説明する本文と丸印。合計額はキャンセル料の0円。',
  'document_kind': 'other',
  'merchant': '架空リゾート テストヴィラ',
  'date': '2023-08-30',
  'date_printed': '発行 2023年8月30日',
  'total': 0,
  'acceptable_accounts': [],
  'expected_decision': 'block',
  'human_context': '予約金96,000円は取消日の翌日2023-08-29に払戻済み。2023-08-30付の本書はキャンセル料0円と残高0円を通知する案内で、新たな支払いの証明ではない。元の返金は別取引として扱う。',
  'review_questions': ['予約金額が本当に払戻完了しているか？', 'キャンセル料0円なのに案内書が発行された理由は？'],
  'features': ['2パネル', '払戻済み金額と取消料の対比', '取消規定', '合計0円'],
  'items': [{'name': 'キャンセル料（今回の請求）', 'gross': 0, 'rate': 'unknown'}]},
 {'case_id': 'V080',
  'file': 'individual/V080.pdf',
  'title': '返金処理完了通知（元取引に対するマイナス返金）',
  'layout_id': 'flash1017-20',
  'layout_description': '150×220mmの縦長。左端に紫の太い縦帯、上に元のご購入と注文番号、中央の白枠に -¥14,850 '
                        'の大型返金額、内訳の積み上げ、右下に返金完了の角印。',
  'document_kind': 'refund',
  'merchant': '架空ストア テスト店',
  'date': '2025-02-14',
  'date_printed': '発行 2025-02-14',
  'total': -14850,
  'acceptable_accounts': [],
  'expected_decision': 'block',
  'human_context': 'ワイヤレスイヤホンの初期不良返品に対する全額返金。元カードへ返金され、新たな請求ではない。返金額はマイナス表記。',
  'review_questions': ['返金の入金はカード明細で確認できるか？', '元取引計上の取消処理は済んでいるか？'],
  'features': ['縦長', '返金', 'マイナス合計', '元取引番号', 'カード返金'],
  'items': [{'name': 'ワイヤレスイヤホン 全額返金（税込）', 'gross': -14850, 'rate': '10'}]}]


def build(output_dir):
    _register_fonts_done()
    out = os.path.abspath(output_dir)
    ind = os.path.join(out, "individual")
    os.makedirs(ind, exist_ok=True)

    funcs = [
        case_v061, case_v062, case_v063, case_v064, case_v065,
        case_v066, case_v067, case_v068, case_v069, case_v070,
        case_v071, case_v072, case_v073, case_v074, case_v075,
        case_v076, case_v077, case_v078, case_v079, case_v080,
    ]
    # default page size gets overridden inside each case
    for fn, meta in zip(funcs, MANIFEST):
        cid = meta["case_id"]
        path = os.path.join(ind, cid + ".pdf")
        c = canvas.Canvas(path, pagesize=A4)
        c.setTitle(cid)
        c.setAuthor(AUTHOR)
        c.setSubject("架空テスト資料")
        fn(c)
        c.showPage()
        c.save()

    with open(os.path.join(out, "manifest-fragment.json"), "w",
              encoding="utf-8") as fh:
        json.dump(MANIFEST, fh, ensure_ascii=False, indent=2)
    return out


def main(argv=None):
    ap = argparse.ArgumentParser()
    ap.add_argument("--output", required=True)
    args = ap.parse_args(argv)
    build(args.output)
    return 0


if __name__ == "__main__":
    sys.exit(main())
