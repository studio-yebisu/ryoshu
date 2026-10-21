#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""独立設計の架空日本語領収書20パターン (V041-V060) 生成スクリプト。

DeepSeek V4.1 Flash (unversioned) による authoring。
reportlab / Pillow / stdlib のみ使用。ネットワーク・サブプロセスなし。
"""
import argparse
import json
import os
import sys

from reportlab.lib.pagesizes import A4, A5, B5
from reportlab.pdfbase import pdfmetrics
from reportlab.pdfbase.ttfonts import TTFont
from reportlab.pdfgen import canvas

# --------------------------------------------------------------------------
# フォント登録（C:/Windows/Fonts の実在フォントのみ。失敗時は候補を順に試す）
# --------------------------------------------------------------------------
FONT_DIRS = [
    "C:/Windows/Fonts",
    "C:\\Windows\\Fonts",
]

_SPECS = [
    # (alias, filename, subfontIndex)
    ("Goth", "msgothic.ttc", 0),
    ("Min", "msmincho.ttc", 0),
    ("Meiryo", "meiryo.ttc", 0),
    ("YuGothB", "YuGothB.ttc", 0),
    ("YuMin", "YuMincho.ttc", 0),
]

REGISTERED = {}


def register_fonts():
    for alias, fname, idx in _SPECS:
        for d in FONT_DIRS:
            p = os.path.join(d, fname)
            if os.path.isfile(p):
                try:
                    pdfmetrics.registerFont(TTFont(alias, p, subfontIndex=idx))
                    REGISTERED[alias] = p
                    break
                except Exception:
                    continue
    # 最低2書体を保証
    if len(REGISTERED) < 2:
        raise RuntimeError("必要な日本語フォントを登録できません: %r" % REGISTERED)


def F(alias):
    if alias in REGISTERED:
        return alias
    return sorted(REGISTERED.keys())[0]


# --------------------------------------------------------------------------
# 汎用プリミティブ（文字幅計測・折返し・行描画など）
# --------------------------------------------------------------------------
def tw(text, font, size):
    return pdfmetrics.stringWidth(text, F(font), size)


def wrap(text, font, size, max_w):
    """日本語/英数字混在テキストを幅 max_w で折り返す。"""
    out = []
    cur = ""
    for ch in text:
        if ch == "\n":
            out.append(cur)
            cur = ""
            continue
        trial = cur + ch
        if tw(trial, font, size) > max_w and cur:
            out.append(cur)
            cur = ch
        else:
            cur = trial
    if cur:
        out.append(cur)
    return out or [""]


def text_at(c, x, y, s, font="Goth", size=9, align="l", color=None):
    """1行文字。align: l/c/r."""
    if color:
        c.setFillColorRGB(*color)
    c.setFont(F(font), size)
    if align == "l":
        c.drawString(x, y, s)
    elif align == "c":
        c.drawCentredString(x, y, s)
    elif align == "r":
        c.drawRightString(x, y, s)
    c.setFillColorRGB(0, 0, 0)


def para(c, x, y, w, s, font="Goth", size=8.5, leading=None, color=None):
    """折返し描画。描画した最終ベースラインの下(=次の行位置)を返す。"""
    lead = leading if leading else size + 2.6
    lines = wrap(s, font, size, w)
    cy = y
    for ln in lines:
        text_at(c, x, cy, ln, font, size, "l", color)
        cy -= lead
    return cy


def para_center(c, x, y, w, s, font="Goth", size=8.5, leading=None, color=None):
    lead = leading if leading else size + 2.6
    lines = wrap(s, font, size, w)
    cy = y
    for ln in lines:
        text_at(c, x + w / 2.0, cy, ln, font, size, "c", color)
        cy -= lead
    return cy


def rule(c, x1, y1, x2, y2, width=0.6, dash=None, color=(0, 0, 0)):
    c.setStrokeColorRGB(*color)
    c.setLineWidth(width)
    if dash:
        c.setDash(*dash)
    c.line(x1, y1, x2, y2)
    c.setDash()


def rect(c, x, y, w, h, width=0.6, fill=None, stroke=(0, 0, 0), color=None, dash=None):
    if fill is not None:
        c.setFillColorRGB(*fill)
        c.rect(x, y, w, h, stroke=0, fill=1)
    c.setStrokeColorRGB(*(color if color is not None else stroke))
    c.setLineWidth(width)
    if dash:
        c.setDash(*dash)
    c.rect(x, y, w, h, stroke=1, fill=0)
    c.setDash()
    c.setFillColorRGB(0, 0, 0)


def big_red_seal(c, cx, cy, r, label="領収印", sub="テスト商事", double=True):
    """二重円の赤い角印風シール。"""
    c.setStrokeColorRGB(0.75, 0.05, 0.05)
    c.setLineWidth(1.8)
    c.circle(cx, cy, r, stroke=1, fill=0)
    if double:
        c.setLineWidth(0.7)
        c.circle(cx, cy, r - 4.5, stroke=1, fill=0)
    c.setFillColorRGB(0.75, 0.05, 0.05)
    c.setFont(F("Min"), r * 0.42)
    c.drawCentredString(cx, cy + r * 0.08, label)
    c.setFont(F("Min"), r * 0.2)
    c.drawCentredString(cx, cy - r * 0.42, sub)
    c.setFillColorRGB(0, 0, 0)


def square_seal(c, x, y, size, text2x2, color=(0.72, 0.06, 0.10)):
    """角印風（4文字2x2）。x,yは左下。"""
    c.setStrokeColorRGB(*color)
    c.setLineWidth(1.4)
    c.rect(x, y, size, size, stroke=1, fill=0)
    c.setLineWidth(0.5)
    c.rect(x + 3, y + 3, size - 6, size - 6, stroke=1, fill=0)
    c.setFillColorRGB(*color)
    c.setFont(F("Min"), size * 0.32)
    c.drawCentredString(x + size * 0.5, y + size * 0.58, text2x2[:2])
    c.drawCentredString(x + size * 0.5, y + size * 0.20, text2x2[2:4])
    c.setFillColorRGB(0, 0, 0)


def jpy(n):
    return "{:,}".format(int(n))


def micro_label(c, x, y, s="架空テスト", size=5.2, rot=0):
    """極小の架空表示。"""
    c.saveState()
    c.translate(x, y)
    if rot:
        c.rotate(rot)
    c.setFillColorRGB(0.35, 0.35, 0.35)
    c.setFont(F("Goth"), size)
    c.drawString(0, 0, s)
    c.setFillColorRGB(0, 0, 0)
    c.restoreState()


# 簡易コリジョンチェック（主要テキストブロック矩形の重なり確認）
class LayoutGuard:
    def __init__(self, W, H, tag):
        self.W = W
        self.H = H
        self.tag = tag
        self.blocks = []

    def block(self, name, x, y, w, h, contains_existing=False):
        x2, y2 = x + w, y + h
        # ページ内チェック
        if x < -0.5 or y < -0.5 or x2 > self.W + 0.5 or y2 > self.H + 0.5:
            raise AssertionError("out of page %s/%s: %s" % (self.tag, name, (x, y, w, h)))
        for (n2, a, b, cw, ch) in self.blocks:
            if contains_existing and a >= x and b >= y and a + cw <= x2 and b + ch <= y2:
                continue
            ox = min(x2, a + cw) - max(x, a)
            oy = min(y2, b + ch) - max(y, b)
            if ox > 1.0 and oy > 1.0:
                raise AssertionError("collision %s: %s vs %s" % (self.tag, name, n2))
        self.blocks.append((name, x, y, w, h))

    def textline(self, name, x, y, s, font, size, align="l"):
        w = tw(s, font, size)
        if align == "c":
            x -= w / 2.0
        elif align == "r":
            x -= w
        self.block(name, x, y - size * 0.25, w, size * 1.15)


# --------------------------------------------------------------------------
# V041 電力メータ検針詳細（請求内訳票） A4縦 4分割メータ欄
# --------------------------------------------------------------------------
def draw_v041(c, W, H, g):
    c.setFillColorRGB(0.97, 0.97, 0.95)
    c.rect(0, 0, W, H, stroke=0, fill=1)
    c.setFillColorRGB(0, 0, 0)

    # 右側の縦帯（受給番号）
    band_w = 34
    c.setFillColorRGB(0.16, 0.28, 0.42)
    c.rect(W - band_w, 0, band_w, H, stroke=0, fill=1)
    c.setFillColorRGB(1, 1, 1)
    c.saveState()
    c.translate(W - band_w / 2 + 3, 40)
    c.rotate(90)
    c.setFont(F("Goth"), 8)
    c.drawString(0, 0, "お客さま番号")
    c.setFont(F("Goth"), 11)
    c.drawString(70, 0, "TEST-8841-2077")
    c.restoreState()
    c.setFillColorRGB(0, 0, 0)

    L = 42
    text_at(c, L, H - 60, "電力使用量のお知らせ（ご請求内訳明細）", "Min", 19)
    text_at(c, L, H - 78, "検針・ご使用期間内訳", "Goth", 9.5, color=(0.25, 0.25, 0.25))
    rule(c, L, H - 88, W - band_w - 30, H - 88, 1.6, color=(0.16, 0.28, 0.42))

    # 差出人（右上ブロック）
    ix, iy = W - band_w - 300, H - 130
    text_at(c, ix, iy, "テスト電力株式会社", "Goth", 13, color=(0.16, 0.28, 0.42))
    para(c, ix, iy - 16, 260,
         "架空町電力センター ／ テスト市架空町1-2-3\nお問い合わせ（架空）03-0000-0000", "Goth", 8)
    text_at(c, ix, iy - 56, "発行日：2024年6月3日", "Goth", 8)
    micro_label(c, W - band_w - 30, 22, "架空テスト", 5.0)

    # 宛先
    text_at(c, L, H - 150, "テスト市架空町9-9-9 テスト荘 3号室", "Goth", 8.5)
    text_at(c, L, H - 172, "架空 太郎 様", "Min", 20)
    rule(c, L, H - 180, L + 180, H - 180, 0.5)
    text_at(c, L, H - 198, "ご使用期間 2024年5月1日 〜 2024年5月31日 （31日間）",
            "Goth", 8.5)
    text_at(c, L, H - 212, "ご検針日：2024年5月31日（月末検針）", "Goth", 8.5)

    # 4分割メータ欄
    gx, gy, gw, gh = L, H - 400, W - band_w - L - 40, 160
    cells = [
        ("検針時指示", "8,421 kWh", "前月 7,860 kWh"),
        ("当月使用", "561 kWh", "前月 612 kWh"),
        ("ご使用期間", "5/1 - 5/31", "月末検針"),
        ("料金 (税込)", "¥25,968", "内消費税 ¥2,360"),
    ]
    cw = gw / 2.0
    chh = gh / 2.0
    c.setLineWidth(0.9)
    c.setStrokeColorRGB(0.16, 0.28, 0.42)
    c.rect(gx, gy, gw, gh, stroke=1, fill=0)
    c.line(gx + cw, gy, gx + cw, gy + gh)
    c.line(gx, gy + chh, gx + gw, gy + chh)
    for i, (lbl, main, sub) in enumerate(cells):
        cx = gx + (i % 2) * cw
        cy = gy + (1 - i // 2) * chh
        text_at(c, cx + 12, cy + chh - 18, lbl, "Goth", 9, color=(0.16, 0.28, 0.42))
        text_at(c, cx + 12, cy + chh - 44, main, "Goth", 16)
        text_at(c, cx + 12, cy + chh - 60, sub, "Goth", 7.5, color=(0.3, 0.3, 0.3))
        g.block("cell%d" % i, cx + 8, cy + 6, cw - 16, chh - 12)
    c.setStrokeColorRGB(0, 0, 0)

    # 内訳明細表
    ty = gy - 30
    cols = [L, L + 230, L + 310, L + 390, L + 425]
    hdr = ["料金区分", "数量", "単価（円）", "金額（円）", "備考"]
    c.setFillColorRGB(0.88, 0.92, 0.96)
    c.rect(L, ty - 6, gw, 18, stroke=0, fill=1)
    c.setFillColorRGB(0, 0, 0)
    for i, h in enumerate(hdr):
        text_at(c, cols[i] + 4, ty, h, "Goth", 7.5)
    rows = [
        ("基本料金（30Aまで）", "1", "2,860", "2,860", "税込"),
        ("電力量料金（第1段階 120kWhまで）", "120", "29.30", "3,516", "税込"),
        ("電力量料金（第2段階 120-300kWh）", "180", "35.20", "6,336", "税込"),
        ("電力量料金（第3段階 300kWh超）", "261", "42.10", "10,988", "税込"),
        ("燃料費調整額", "561", "1.20", "673", "税込"),
        ("再生可能エネルギー発電促進賦課金", "561", "3.00", "1,683", "税込"),
        ("口座振替割引", "1", "-", "-88", "税込"),
        ("合計（税込）", "", "", "25,968", "内消費税 ¥2,360"),
    ]
    ry = ty - 16
    for r in rows:
        text_at(c, cols[0] + 4, ry, r[0], "Goth", 7.5)
        text_at(c, cols[1] + 4, ry, r[1], "Goth", 7.5)
        text_at(c, cols[2] + 4, ry, r[2], "Goth", 7.5, align="r")
        text_at(c, cols[3] + 4, ry, r[3], "Goth", 7.5, align="r")
        text_at(c, cols[4] + 4, ry, r[4], "Goth", 7.5)
        g.textline("row-%s" % r[0][:6], cols[0] + 4, ry, r[0], "Goth", 7.5)
        g.block("rowR-" + r[0][:6], cols[3], ry, 60, 10)
        ry -= 17
    rule(c, L, ty + 14, L + gw, ty + 14, 0.7)
    rule(c, L, ry + 14, L + gw, ry + 14, 1.0)

    # 総額大文字部
    text_at(c, L, ry - 20, "ご請求金額", "Goth", 10)
    text_at(c, L, ry - 62, "25,968", "Goth", 32)
    text_at(c, L + tw("25,968", "Goth", 32) + 8, ry - 58, "円", "Goth", 14)
    text_at(c, L, ry - 80, "（口座振替日 2024年6月25日）", "Goth", 8)
    big_red_seal(c, W - band_w - 110, ry - 42, 40, "料金確認", "テスト電力")
    text_at(c, L, 26, "※お支払い済みの場合があります。ご確認ください。", "Goth", 7)


# --------------------------------------------------------------------------
# V042 ガス検針票兼領収書 A5横 左領収/右明細 縦罫
# --------------------------------------------------------------------------
def draw_v042(c, W, H, g):
    pad = 18
    mid = W * 0.62
    rect(c, pad, pad, mid - pad * 1.5, H - pad * 2, 1.0, color=(0.2, 0.35, 0.25))
    rule(c, mid - pad * 0.25, pad + 10, mid - pad * 0.25, H - pad - 10, 1.2,
         dash=(3, 2), color=(0.2, 0.35, 0.25))
    text_at(c, pad + 10, H - pad - 20, "領 収 書", "Min", 22, color=(0.2, 0.35, 0.25))
    text_at(c, pad + 10, H - pad - 36, "ガス料金（2024年4月分）", "Goth", 9)
    text_at(c, pad + 10, H - pad - 58, "受領日：2024年5月14日", "Goth", 9)
    text_at(c, pad + 10, H - pad - 80, "ご住所：テスト市架空町9-9-9", "Goth", 8)
    text_at(c, pad + 10, H - pad - 94, "ご氏名：架空 花子 様", "Goth", 8)
    rule(c, pad + 10, H - pad - 104, mid - pad * 0.8, H - pad - 104, 0.6)
    text_at(c, pad + 10, H - pad - 124, "領収金額", "Goth", 10)
    text_at(c, pad + 10, H - pad - 166, "4,437", "Goth", 30, color=(0.2, 0.35, 0.25))
    text_at(c, pad + 10 + tw("4,437", "Goth", 30) + 6, H - pad - 160, "円（税込）",
            "Goth", 11)
    text_at(c, pad + 10, H - pad - 184, "上記領収いたしました。", "Goth", 8)
    text_at(c, pad + 10, H - pad - 206, "支払方法：現金", "Goth", 8)
    text_at(c, pad + 10, H - pad - 220, "検針日：2024年5月10日", "Goth", 8)
    text_at(c, pad + 10, pad + 44, "テスト都市ガス株式会社", "Goth", 9, color=(0.2, 0.35, 0.25))
    text_at(c, pad + 10, pad + 32, "テスト市架空町2-4-6 架空町営業所", "Goth", 7.5)
    square_seal(c, mid - pad * 1.6, pad + 16, 44, "領収済", (0.2, 0.35, 0.25))
    micro_label(c, pad + 10, pad + 8)

    # 右明細縦罫
    table_l = mid + 14
    table_w = W - pad - table_l
    cols = [table_l, table_l + table_w * 0.42, table_l + table_w * 0.66,
            table_l + table_w * 0.86]
    top = H - pad - 14
    nrows = 8
    rh = 22
    hh = 20
    hdr = ["項目（税抜）", "数量", "単価", "金額"]
    c.setFillColorRGB(0.9, 0.95, 0.9)
    c.rect(table_l, top - hh, table_w, hh, stroke=0, fill=1)
    c.setFillColorRGB(0, 0, 0)
    for i, h in enumerate(hdr):
        text_at(c, cols[i] + 3, top - hh + 6, h, "Goth", 7.5)
    rows = [
        ("基本料金（20m³まで）", "1", "1,056", "1,056"),
        ("従量料金（0〜20m³）", "18", "168.2", "3,028"),
        ("従量料金（21m³〜）", "0", "205.0", "0"),
        ("メーター使用料", "1", "150", "150"),
        ("お得な料金プラン割引", "1", "", "-200"),
        ("消費税等（10%）", "", "", "403"),
        ("ご請求分", "", "", "4,437"),
        ("本日ご入金", "", "", "-4,437"),
    ]
    y = top - hh
    for i, r in enumerate(rows):
        rule(c, cols[0], y, cols[-1] + table_w * 0.14, y, 0.4, color=(0.6, 0.65, 0.6))
        text_at(c, cols[0] + 3, y - 14, r[0], "Goth", 7.3)
        text_at(c, cols[1] + 3, y - 14, r[1], "Goth", 7.3)
        text_at(c, cols[3] - 5, y - 14, r[2], "Goth", 7.3, align="r")
        text_at(c, table_l + table_w - 5, y - 14, r[3], "Goth", 7.3, align="r")
        g.textline("gas-row%d" % i, cols[0] + 3, y - 14, r[0], "Goth", 7.3)
        y -= rh
    row_r = table_w * 0.14
    for cx in cols:
        rule(c, cx, top, cx, y, 0.5, color=(0.6, 0.65, 0.6))
    rule(c, cols[-1] + row_r, top, cols[-1] + row_r, y, 0.5, color=(0.6, 0.65, 0.6))

    text_at(c, table_l, y - 26, "今回検針", "Goth", 8)
    text_at(c, table_l + table_w * 0.42, y - 26, "184 m³", "Goth", 10)
    text_at(c, table_l, y - 42, "前回検針", "Goth", 8)
    text_at(c, table_l + table_w * 0.42, y - 42, "166 m³", "Goth", 10)
    text_at(c, table_l, y - 58, "使用量", "Goth", 8)
    text_at(c, table_l + table_w * 0.42, y - 58, "18 m³", "Goth", 10)
    para(c, table_l, y - 82, table_w,
         "お支払いにより、お客さまコード TEST-5520 の4月分ガス料金は完納となりました。",
         "Goth", 7.5)
    micro_label(c, W - pad - 46, pad + 6, "架空テスト", 4.8)


# --------------------------------------------------------------------------
# V043 上下水道料金領収証 A4縦 二枚一組（上下） 左右分離
# --------------------------------------------------------------------------
def draw_v043(c, W, H, g):
    c.setFillColorRGB(0.92, 0.95, 0.98)
    c.rect(0, 0, W, H, stroke=0, fill=1)
    c.setFillColorRGB(0, 0, 0)
    # 上下2つ折り
    midy = H / 2.0
    rule(c, 0, midy, W, midy, 1.0, dash=(4, 3), color=(0.35, 0.45, 0.6))
    text_at(c, W - 60, midy - 10, "きりとり線", "Goth", 6, color=(0.5, 0.5, 0.5), align="r")

    def half(y0, is_copy):
        L = 50
        R = W - 50
        top = y0 + midy - 40
        text_at(c, L, top, "上下水道料金領収証", "Min", 18, color=(0.18, 0.35, 0.55))
        if is_copy:
            text_at(c, R, top, "控 え", "Goth", 10, align="r", color=(0.18, 0.35, 0.55))
        else:
            text_at(c, R, top, "お客さま控え", "Goth", 10, align="r", color=(0.18, 0.35, 0.55))
        rule(c, L, top - 8, R, top - 8, 1.2, color=(0.18, 0.35, 0.55))

        # 左：領収内容
        text_at(c, L, top - 30, "領収金額", "Goth", 8.5)
        text_at(c, L, top - 54, "3,410", "Goth", 22, color=(0.18, 0.35, 0.55))
        text_at(c, L + tw("3,410", "Goth", 22) + 4, top - 50, "円", "Goth", 10)
        text_at(c, L, top - 72, "（水道 1,980円 / 下水道 1,430円 税込）", "Goth", 7.5)
        text_at(c, L, top - 90, "領収日：2024年6月18日", "Goth", 8)
        text_at(c, L, top - 104, "請求番号：TEST-W042-0618", "Goth", 8)
        text_at(c, L, top - 122, "テスト市水道局 様より", "Goth", 8)
        text_at(c, L, top - 136, "架空 一郎 様（架空町8-8）", "Goth", 8)
        micro_label(c, L, y0 + 8, "架空テスト", 4.8)
        stamp = square_seal if not is_copy else None
        if stamp:
            stamp(c, R - 90, top - 88, 50, "領収証", (0.18, 0.35, 0.55))

        # 右：使用量メータ表（縦罫5列）
        tx = L + 300
        colw = (R - tx) / 4.0
        heads = ["口径", "今回指針", "前回指針", "使用水量"]
        vals = ["13mm", "1,284", "1,272", "12m³"]
        top_r = top - 26
        for i in range(5):
            rule(c, tx + i * colw, top_r, tx + i * colw, top_r - 50, 0.5,
                 color=(0.6, 0.7, 0.8))
        for i, h in enumerate(heads):
            text_at(c, tx + i * colw + 4, top_r - 12, h, "Goth", 6.8)
            text_at(c, tx + i * colw + colw / 2.0, top_r - 38, vals[i], "Goth", 9, "c")
            g.textline("wc%d%d" % (is_copy, i), tx + i * colw + 4, top_r - 12, h, "Goth", 6.8)
        rule(c, tx, top_r, tx + 4 * colw, top_r, 0.7, color=(0.6, 0.7, 0.8))
        rule(c, tx, top_r - 24, tx + 4 * colw, top_r - 24, 0.4, color=(0.6, 0.7, 0.8))
        rule(c, tx, top_r - 50, tx + 4 * colw, top_r - 50, 0.7, color=(0.6, 0.7, 0.8))

        text_at(c, tx, top_r - 68, "使用期間：2024年4月1日〜2024年4月30日", "Goth", 7.5)
        text_at(c, tx, top_r - 82, "水道基本料金1,210＋従量770（いずれも税込）", "Goth", 7.3)
        text_at(c, tx, top_r - 96, "下水道基本1,100＋従量330（いずれも税込）", "Goth", 7.3)

    half(0, False)
    half(midy, True)


# --------------------------------------------------------------------------
# V044 携帯2回線の合計請求・領収書 A4縦 左回線/右回線ツリー
# --------------------------------------------------------------------------
def draw_v044(c, W, H, g):
    text_at(c, 30, H - 40, "架空モバイル通信 ご利用料金 領収書（2回線合算）",
            "Goth", 13, color=(0.45, 0.12, 0.35))
    text_at(c, 30, H - 54,
            "2024年5月ご利用分（6月請求） / 発行日 2024-06-27 / お客さま番号 TEST-77120",
            "Goth", 7.8)
    rule(c, 30, H - 62, W - 30, H - 62, 1.2, color=(0.45, 0.12, 0.35))

    # 支払済スタンプ
    c.saveState()
    c.translate(W - 110, H - 118)
    c.rotate(-12)
    c.setStrokeColorRGB(0.75, 0.15, 0.3)
    c.setLineWidth(2.2)
    c.rect(-58, -20, 116, 40, stroke=1, fill=0)
    text_at(c, 0, -6, "領 収 済", "Goth", 18, "c", (0.75, 0.15, 0.3))
    c.restoreState()

    text_at(c, 30, H - 90, "架空 事業部 御中 / 請求書払い（法人カード払い）", "Goth", 8.5)
    text_at(c, 30, H - 106, "合計ご請求額", "Goth", 8)
    text_at(c, 30, H - 140, "13,479", "Goth", 26, color=(0.45, 0.12, 0.35))
    text_at(c, 30 + tw("13,479", "Goth", 26) + 6, H - 134, "円（税込）", "Goth", 9)

    # ツリー型構造 (左回線/右回線) 中央根から分岐
    root_y = H - 185
    cx = W / 2.0
    text_at(c, 30, root_y - 4, "回線別内訳", "Goth", 9)
    box_w = (W - 100) / 2.0
    lx = 40
    rx = W - 40 - box_w
    bx_top = root_y - 26
    box_h = 300
    for cx0, cname, no, plan in [
        (lx, "回線A（本社）", "TEST-A0110", "データ多めプラン 30GB"),
        (rx, "回線B（営業）", "TEST-B0111", "かけ放題 5GB"),
    ]:
        rect(c, cx0, bx_top - box_h, box_w, box_h, 0.8, color=(0.45, 0.12, 0.35))
        text_at(c, cx0 + 8, bx_top - 18, cname, "Goth", 10, color=(0.45, 0.12, 0.35))
        text_at(c, cx0 + 8, bx_top - 32, no + " / " + plan, "Goth", 7)
        yy = bx_top - 52
        lines = [
            ("基本プラン", "5,200", 0),
            ("通話・通信量超過", "1,000", 1),
            ("オプション（留守番電話）", "330", 1),
            ("ユニバーサルサービス料", "2", 0),
            ("小計", "6,532", 0),
        ]
        for name, val, lvl in lines:
            indent = 8 + lvl * 12
            text_at(c, cx0 + indent, yy, ("└ " if lvl else "") + name, "Goth", 7.5)
            text_at(c, cx0 + box_w - 10, yy, val + "円", "Goth", 7.5, align="r")
            yy -= 16
        rule(c, cx0 + 8, yy + 6, cx0 + box_w - 8, yy + 6, 0.4, color=(0.7, 0.5, 0.65))
        yy -= 8
        for name, val in [("消費税（10%）", "653"), ("回線合計", "7,185")]:
            text_at(c, cx0 + 8, yy, name, "Goth", 7.5)
            text_at(c, cx0 + box_w - 10, yy, val + "円", "Goth", 7.5, align="r")
            yy -= 18
        # 回線別端末割賦（本体残債・合算外）
        yy -= 6
        text_at(c, cx0 + 8, yy, "端末割賦（別途請求）", "Goth", 6.8, color=(0.4, 0.4, 0.4))
        text_at(c, cx0 + box_w - 10, yy, "3,200円/月", "Goth", 6.8, align="r",
                color=(0.4, 0.4, 0.4))
        g.block("line" + no, cx0, bx_top - box_h, box_w, box_h)

    # 分岐線
    mid_x = (lx + box_w + rx) / 2.0
    rule(c, lx + box_w, bx_top + 8, mid_x, bx_top + 8, 0.8, color=(0.45, 0.12, 0.35))
    rule(c, mid_x, bx_top + 8, mid_x, root_y + 4, 0.8, color=(0.45, 0.12, 0.35))
    rule(c, lx + box_w / 2.0, bx_top + 8, lx + box_w / 2.0, bx_top, 0.8,
         color=(0.45, 0.12, 0.35))
    rule(c, rx + box_w / 2.0, bx_top + 8, rx + box_w / 2.0, bx_top, 0.8,
         color=(0.45, 0.12, 0.35))

    # 合算表
    sy = bx_top - box_h - 34
    text_at(c, 30, sy, "合算内訳", "Goth", 9)
    rows = [
        ("回線A 請求額", "7,185"),
        ("回線B 請求額", "7,185"),
        ("ファミリー割引（2回線）", "-891"),
    ]
    yy = sy - 18
    for name, val in rows:
        text_at(c, 40, yy, name, "Goth", 8.5)
        text_at(c, W - 40, yy, val + "円", "Goth", 8.5, align="r")
        yy -= 16
    rule(c, 40, yy + 6, W - 40, yy + 6, 0.7)
    text_at(c, 40, yy - 8, "合計ご請求額（税込）", "Goth", 10)
    text_at(c, W - 40, yy - 8, "13,479円", "Goth", 10, align="r")
    text_at(c, 40, yy - 26,
            "端末割賦金（回線A 3,200円・回線B 3,200円）は本領収に含まれません。",
            "Goth", 7, color=(0.4, 0.4, 0.4))
    text_at(c, 40, yy - 40,
            "2024年6月27日 架空モバイル通信株式会社 ／ 東京都テスト区架空町",
            "Goth", 7.5)
    micro_label(c, W - 70, 20, "架空テスト", 4.8)


# --------------------------------------------------------------------------
# V045 固定回線 開通工事費＋月額セット明細 B5横 横向き2段組み
# --------------------------------------------------------------------------
def draw_v045(c, W, H, g):
    # 左端の濃色ナビ帯
    c.setFillColorRGB(0.1, 0.22, 0.32)
    c.rect(0, 0, 70, H, stroke=0, fill=1)
    c.setFillColorRGB(0.65, 0.85, 0.95)
    c.saveState()
    c.translate(48, 80)
    c.rotate(90)
    c.setFont(F("Goth"), 12)
    c.drawString(0, 0, "開通工事とご利用料金のご案内")
    c.restoreState()
    c.setFillColorRGB(0, 0, 0)

    x0 = 90
    text_at(c, x0, H - 40, "テスト光ネットワーク株式会社", "Goth", 12, color=(0.1, 0.22, 0.32))
    text_at(c, x0, H - 54, "サービス契約番号：TEST-BB-44821 / お支払い：口座振替",
            "Goth", 7.5)
    text_at(c, x0, H - 68, "発行：2024年4月10日（2024年3月ご利用分）", "Goth", 7.5)

    # 上部帯：開通工事費（キャンペーン充当）
    y1 = H - 150
    rect(c, x0, y1, W - x0 - 30, 68, 0.8, color=(0.1, 0.22, 0.32))
    text_at(c, x0 + 10, y1 + 50, "開通工事費（初回のみ）", "Goth", 10)
    text_at(c, x0 + 10, y1 + 26, "工事費 44,000円（税込）", "Goth", 8)
    text_at(c, x0 + 10, y1 + 10, "分割残金はありません。", "Goth", 7)
    rect(c, W - x0 - 200, y1 + 16, 180, 36, 0.6, color=(0.1, 0.22, 0.32))
    text_at(c, W - x0 - 110, y1 + 33, "全額充当（キャンペーン）", "Goth", 9, "c")
    text_at(c, W - x0 - 110, y1 + 22, "ご請求額 0円", "Goth", 8, "c")
    g.block("kobun", x0, y1, W - x0 - 30, 68)

    # 横向き月額明細（2群）
    y2 = y1 - 24
    text_at(c, x0, y2, "月額料金の内訳（税込）", "Goth", 9)
    groups = [
        ("回線利用料", [("光回線 10ギガ", 6, "6,600"), ("標準工事費分割", 6, "0"),
                     ("回線基本料", 6, "1,100")], "回線小計 7,700"),
        ("オプション", [("Wi-Fi機器レンタル", 6, "550"), ("電話基本料", 6, "550"),
                     ("電話通話料", 4, "180")], "オプション小計 1,280"),
    ]
    gx = x0
    gw = (W - x0 - 40) / 2.0
    for gname, rows, subtotal in groups:
        c.setFillColorRGB(0.9, 0.94, 0.97)
        c.rect(gx, y2 - 6, gw, 20, stroke=0, fill=1)
        c.setFillColorRGB(0, 0, 0)
        text_at(c, gx + 6, y2 + 1, gname, "Goth", 8)
        yy = y2 - 24
        for name, cw, val in rows:
            text_at(c, gx + 12, yy, name, "Goth", 7.5)
            text_at(c, gx + gw - 8, yy, val + "円", "Goth", 7.5, align="r")
            g.textline("%s-%s" % (gname[:2], name[:4]), gx + 12, yy, name, "Goth", 7.5)
            yy -= 15
        rule(c, gx + 6, yy + 6, gx + gw - 6, yy + 6, 0.5, color=(0.5, 0.6, 0.7))
        text_at(c, gx + 12, yy, subtotal, "Goth", 8, color=(0.1, 0.22, 0.32))
        yy -= 12
        text_at(c, gx + 12, yy, "（消費税10% 内税）", "Goth", 6.5, color=(0.4, 0.4, 0.4))
        g.block("grp" + gname, gx, yy - 4, gw, y2 - yy + 18, contains_existing=True)
        gx += gw + 10

    # 合計帯（斜線ハッチの背景）
    y3 = y2 - 160
    bx1, bx2 = x0, W - 30
    c.saveState()
    p = c.beginPath()
    p.rect(bx1, y3, bx2 - bx1, 54)
    c.clipPath(p, stroke=0, fill=0)
    c.setStrokeColorRGB(0.75, 0.82, 0.88)
    c.setLineWidth(0.5)
    for hx in range(int(bx1), int(bx2) + 80, 10):
        c.line(hx, y3, hx - 54, y3 + 54)
    c.restoreState()
    rect(c, bx1, y3, bx2 - bx1, 54, 0.9, color=(0.1, 0.22, 0.32))
    text_at(c, bx1 + 12, y3 + 36, "3月分ご請求額（工事費込み・税込）", "Goth", 8.5)
    text_at(c, bx1 + 12, y3 + 14, "8,980", "Goth", 22)
    text_at(c, bx1 + 12 + tw("8,980", "Goth", 22) + 6, y3 + 18, "円", "Goth", 10)
    text_at(c, bx2 - 12, y3 + 22, "お支払い済み（口座振替済 2024/04/08）", "Goth", 8.5,
            align="r", color=(0.1, 0.22, 0.32))
    g.block("total-band", bx1, y3, bx2 - bx1, 54)

    text_at(c, x0, 30,
            "※ 工事費44,000円と月額料金8,980円のうち、ご請求額は8,980円です。工事費は全額キャンペーン充当済み。",
            "Goth", 6.8)
    micro_label(c, 30, 12, "架空テスト", 5.2)


# --------------------------------------------------------------------------
# V046 クラウドサーバー利用明細 A4縦 三列タイル（カード型）
# --------------------------------------------------------------------------
def draw_v046(c, W, H, g):
    c.setFillColorRGB(0.06, 0.08, 0.12)
    c.rect(0, H * 0.68, W, H * 0.32, stroke=0, fill=1)
    c.setFillColorRGB(0, 0, 0)
    text_at(c, 36, H - 50, "TEST CLOUD / 月次ご利用明細", "Goth", 20, color=(0.75, 0.9, 0.95))
    micro_label(c, W - 80, H - 44, "架空テスト", 5.0)
    text_at(c, 36, H - 76, "お客さま：架空 株式会社テストラボ 御中", "Goth", 8.5,
            color=(0.85, 0.85, 0.85))
    text_at(c, 36, H - 92, "アカウント TEST-ACCT-77310 / 課金期間 2024-05-01〜2024-05-31",
            "Goth", 7.5, color=(0.8, 0.8, 0.8))
    text_at(c, W - 36, H - 76, "発行 2024年6月1日", "Goth", 8, align="r",
            color=(0.85, 0.85, 0.85))
    text_at(c, W - 36, H - 92, "お支払い：カード（決済済 2024-06-01）", "Goth", 7.5,
            align="r", color=(0.85, 0.85, 0.85))

    # 三列タイル
    y_top = H * 0.62
    tile_w = (W - 72 - 24) / 3.0
    tile_h = 250
    tiles = [
        ("コンピューティング", [
            ("vCPU 時間", "620", "12.0", "7,440"),
            ("メモリ GB時間", "1,240", "7.0", "8,680"),
            ("スナップショット", "40", "5.0", "200"),
        ]),
        ("ネットワーク", [
            ("アウトバウンド転送", "120 GB", "20", "2,400"),
            ("ロードバランサー", "1台", "2,500", "2,500"),
        ]),
        ("ライセンス・その他", [
            ("管理画面利用料", "1", "1,200", "1,200"),
            ("サポート（スタンダード）", "1", "5,000", "5,000"),
        ]),
    ]
    subtotals = []
    for i, (tname, rows) in enumerate(tiles):
        tx = 36 + i * (tile_w + 12)
        c.setFillColorRGB(0.96, 0.97, 0.99)
        c.rect(tx, y_top - tile_h, tile_w, tile_h, stroke=0, fill=1)
        rect(c, tx, y_top - tile_h, tile_w, tile_h, 0.7, color=(0.4, 0.5, 0.6))
        c.setFillColorRGB(0.06, 0.08, 0.12)
        c.rect(tx, y_top - 26, tile_w, 26, stroke=0, fill=1)
        c.setFillColorRGB(0, 0, 0)
        text_at(c, tx + 8, y_top - 18, tname, "Goth", 8.5, color=(0.75, 0.9, 0.95))
        yy = y_top - 46
        text_at(c, tx + 8, yy, "品目", "Goth", 6.5, color=(0.4, 0.4, 0.4))
        text_at(c, tx + tile_w - 8, yy, "金額", "Goth", 6.5, align="r", color=(0.4, 0.4, 0.4))
        yy -= 16
        sub = 0
        for name, qty, unit, amt in rows:
            text_at(c, tx + 8, yy, name, "Goth", 7.5)
            text_at(c, tx + 8, yy - 10, "  %s × %s" % (qty, unit), "Goth", 6.5,
                    color=(0.45, 0.45, 0.45))
            text_at(c, tx + tile_w - 8, yy, amt, "Goth", 8, align="r")
            g.textline("tile%d-%s" % (i, name[:4]), tx + 8, yy, name, "Goth", 7.5)
            sub += int(amt.replace(",", ""))
            yy -= 32
        rule(c, tx + 8, yy + 6, tx + tile_w - 8, yy + 6, 0.4, color=(0.7, 0.75, 0.8))
        text_at(c, tx + 8, yy - 4, "小計", "Goth", 7.5)
        text_at(c, tx + tile_w - 8, yy - 4, "{:,}".format(sub), "Goth", 7.5, align="r")
        subtotals.append(sub)
        g.block("tile%d" % i, tx, y_top - tile_h, tile_w, tile_h, contains_existing=True)

    # 集計行
    ay = y_top - tile_h - 36
    rule(c, 36, ay, W - 36, ay, 1.0, color=(0.4, 0.5, 0.6))
    text_at(c, 36, ay - 18, "税抜小計", "Goth", 8.5)
    text_at(c, W - 36, ay - 18, jpy(sum(subtotals)) + "円", "Goth", 8.5, align="r")
    text_at(c, 36, ay - 34, "消費税 10%", "Goth", 8.5)
    tax = round(sum(subtotals) * 0.10)
    text_at(c, W - 36, ay - 34, jpy(tax) + "円", "Goth", 8.5, align="r")
    text_at(c, 36, ay - 54, "ご請求総額（税込・決済済）", "Goth", 10)
    text_at(c, W - 36, ay - 54, jpy(sum(subtotals) + tax) + "円", "Goth", 12, align="r")
    text_at(c, 36, 30, "支払い済み。お支払い日：2024年6月1日（カード決済）", "Goth", 7)


# --------------------------------------------------------------------------
# V047 ドメイン更新料 領収書 A5縦 中央寄せレシート風
# --------------------------------------------------------------------------
def draw_v047(c, W, H, g):
    pad = 20
    rect(c, pad, pad, W - pad * 2, H - pad * 2, 1.0, dash=(5, 2), color=(0.25, 0.25, 0.25))
    cx = W / 2.0
    y = H - pad - 30
    text_at(c, cx, y, "ご利用料金 領収書", "Min", 17, "c")
    text_at(c, cx, y - 18, "ドメイン更新料金のお支払い", "Goth", 8, "c")
    text_at(c, cx, y - 34, "発行日 2024/03/18", "Goth", 7.5, "c")
    rule(c, pad + 20, y - 44, W - pad - 20, y - 44, 0.6, dash=(4, 2))
    y -= 64
    rows = [
        ("ご依頼者様", "架空 株式会社テスト商店 様"),
        ("請求書番号", "TEST-INV-33092"),
        ("ドメイン名", "kakuu-test-shop.example"),
        ("更新期間", "2024-03-20 〜 2025-03-19（1年）"),
    ]
    for k, v in rows:
        text_at(c, pad + 32, y, k, "Goth", 7.5, color=(0.45, 0.45, 0.45))
        text_at(c, W - pad - 32, y, v, "Goth", 8, align="r")
        g.textline("d-" + k, pad + 32, y, k, "Goth", 7.5)
        y -= 16
    text_at(c, cx, y + 2, "────────────────────────", "Goth", 8, "c", (0.5, 0.5, 0.5))
    y -= 16
    text_at(c, cx, y, "請求金額内訳", "Goth", 8.5, "c")
    y -= 20
    items = [
        ("ドメイン更新料（.example）", 1, "1,000 + 税"),
        ("プライバシー保護オプション", 1, "0"),
        ("ICANN 手数料", 1, "20"),
    ]
    for name, q, note in items:
        text_at(c, pad + 32, y, name, "Goth", 8)
        text_at(c, W - pad - 32, y, note, "Goth", 8, align="r")
        y -= 16
    rule(c, pad + 32, y + 6, W - pad - 32, y + 6, 0.5)
    text_at(c, pad + 32, y - 4, "合計（税込）", "Goth", 9)
    text_at(c, W - pad - 32, y - 4, "1,122円", "Goth", 9, align="r")
    text_at(c, pad + 32, y - 20, "支払い方法：クレジットカード（一括）", "Goth", 7.5)
    text_at(c, pad + 32, y - 34, "決済日：2024/03/18", "Goth", 7.5)
    text_at(c, pad + 32, y - 48, "領収済み（カード決済）— 全額", "Goth", 8, color=(0.2, 0.45, 0.2))

    text_at(c, cx, pad + 70, "テストネームサービス株式会社", "Goth", 9, "c")
    text_at(c, cx, pad + 56, "架空テスト用登録サービス / 登録者情報は架空", "Goth", 6.5, "c",
            color=(0.45, 0.45, 0.45))
    big_red_seal(c, W - pad - 50, pad + 50, 34, "領収", "TNS", False)
    micro_label(c, pad + 24, pad + 12, "架空テスト", 5.0)


# --------------------------------------------------------------------------
# V048 年次SaaS 年度跨ぎ請求書兼領収書 A4横 左注記/右明細
# --------------------------------------------------------------------------
def draw_v048(c, W, H, g):
    text_at(c, 40, H - 46, "年額サブスクリプション ご請求・領収書", "Goth", 16,
            color=(0.2, 0.3, 0.15))
    rule(c, 40, H - 56, W - 40, H - 56, 1.4, color=(0.2, 0.3, 0.15))
    # 左：サービス説明/期間帯
    rect(c, 40, H - 250, 280, 180, 0.8, color=(0.2, 0.3, 0.15))
    text_at(c, 52, H - 80, "テストERPクラウド（年額）", "Goth", 10)
    text_at(c, 52, H - 96, "契約ID TEST-ERP-2028-004", "Goth", 7.5)
    text_at(c, 52, H - 118, "サービス期間", "Goth", 7, color=(0.4, 0.4, 0.4))
    text_at(c, 52, H - 134, "2028年4月1日", "Goth", 10)
    text_at(c, 52, H - 152, "〜 2029年3月31日", "Goth", 10)
    text_at(c, 52, H - 174, "（12か月・1会計年度）", "Goth", 7)
    text_at(c, 52, H - 194, "契約者数：25名 / プラン：Business", "Goth", 7.5)
    text_at(c, 52, H - 212, "請求日 2028年3月1日 / 決済 2028年3月5日", "Goth", 7)
    text_at(c, 52, H - 228, "支払い：銀行振込（TEST銀行）", "Goth", 7)
    g.block("left-panel", 40, H - 250, 280, 180)

    # 右上：宛先ブロック
    text_at(c, W - 40, H - 82, "架空サプライチェーン株式会社 御中", "Goth", 11, align="r")
    text_at(c, W - 40, H - 98, "テスト市架空町3-3-3", "Goth", 8, align="r")
    text_at(c, W - 40, H - 114, "発行者 テストソフトウェア株式会社", "Goth", 8, align="r")
    text_at(c, W - 40, H - 130, "T0000000000000", "Goth", 7.5, align="r")

    # 右：明細表
    tx = W - 40 - 360
    heads = ["品目", "数量", "単価", "小計"]
    colx = [tx, tx + 210, tx + 270, tx + 320]
    ty = H - 180
    c.setFillColorRGB(0.88, 0.92, 0.84)
    c.rect(tx, ty, 360, 18, stroke=0, fill=1)
    c.setFillColorRGB(0, 0, 0)
    for i, h in enumerate(heads):
        text_at(c, colx[i], ty + 5, h, "Goth", 7)
    rulesrows = [
        ("ERPライセンス（年額・25名）", "1", "480,000", "480,000"),
        ("初期導入サポート", "1", "120,000", "120,000"),
        ("追加アドオン（会計連携）", "1", "60,000", "60,000"),
        ("長期契約割引（年額）", "1", "-", "-40,000"),
        ("小計（税抜）", "", "", "620,000"),
        ("消費税 10%", "", "", "62,000"),
        ("合計（税込）", "", "", "682,000"),
    ]
    ry = ty - 16
    for i, r in enumerate(rulesrows):
        if i >= 4:
            rule(c, tx, ry + 4, tx + 360, ry + 4, 0.9)
        text_at(c, colx[0], ry, r[0], "Goth", 7.8)
        text_at(c, colx[1], ry, r[1], "Goth", 7.8)
        text_at(c, colx[2], ry, r[2], "Goth", 7.8, align="r")
        text_at(c, colx[3], ry, r[3], "Goth", 7.8, align="r")
        g.textline("erp-row%d" % i, colx[0], ry, r[0], "Goth", 7.8)
        ry -= 18
    rule(c, tx, ty, tx, ry + 18, 0.5)
    for i in range(1, 4):
        rule(c, colx[i] - 4, ty, colx[i] - 4, ry + 18, 0.4)

    text_at(c, tx, ry - 8, "領収済 682,000円（税込）", "Goth", 10, color=(0.2, 0.3, 0.15))
    text_at(c, tx, ry - 24, "2028年3月5日 銀行振込にて入金確認", "Goth", 7.5)
    square_seal(c, W - 120, ry - 60, 46, "領収済", (0.2, 0.3, 0.15))
    # 会計期間跨ぎ注記（下段）
    text_at(c, 40, 44,
            "※本ライセンスの利用期間は2028/4/1〜2029/3/31です。",
            "Goth", 7.3)
    text_at(c, 40, 30, "※利用者数は25名です。", "Goth", 7.0)
    micro_label(c, W - 80, 18, "架空テスト", 4.8)


# --------------------------------------------------------------------------
# V049 ソフトウェア買切 資産検討候補票 A4縦 上段見積/下段明細
# --------------------------------------------------------------------------
def draw_v049(c, W, H, g):
    L = 44
    R = W - 44
    text_at(c, L, H - 44, "ソフトウェア永久ライセンス 購入代金 受領書", "Min", 17)
    text_at(c, L, H - 60, "（製品の利用条件と納品内容を記載しています）", "Goth", 7.5,
            color=(0.4, 0.4, 0.4))
    rect(c, L, H - 148, R - L, 70, 0.8, color=(0.22, 0.22, 0.3))
    text_at(c, L + 10, H - 96, "テスト計測ソフトウェア 設計解析エディション（買切・永久）", "Goth", 8)
    text_at(c, L + 10, H - 114, "ライセンス数 5 / サポート切れなし / 譲渡不可", "Goth", 7.5)
    text_at(c, L + 10, H - 132, "納品日 2024年2月20日 ／ 導入日 2024年2月26日", "Goth", 7.5)
    text_at(c, R - 10, H - 96, "受領 2024年2月28日", "Goth", 8, align="r")
    text_at(c, R - 10, H - 114, "払込：銀行振込 一括", "Goth", 7.5, align="r")
    text_at(c, R - 10, H - 132, "架空 技術開発部 御中", "Goth", 7.5, align="r")
    g.block("top-band", L, H - 148, R - L, 70)

    # 中段：金額と資産候補情報（左右）
    text_at(c, L, H - 180, "受領金額", "Goth", 9)
    text_at(c, L, H - 216, "550,000", "Goth", 28)
    text_at(c, L + tw("550,000", "Goth", 28) + 8, H - 210, "円（税込）", "Goth", 10)
    text_at(c, L, H - 236, "税抜 500,000円 / 消費税 50,000円", "Goth", 8)
    text_at(c, L, H - 252, "※ 本製品は買切型の永久ライセンスです", "Goth", 7, color=(0.4, 0.4, 0.4))
    g.block("amount", L, H - 252, 260, 90)

    rx = L + 300
    rect(c, rx, H - 280, R - rx, 110, 0.6, color=(0.22, 0.22, 0.3))
    text_at(c, rx + 10, H - 202, "製品・納品内容", "Goth", 8.5)
    checks = [
        "□ 物理媒体同梱あり",
        "□ 年額サブスクリプションではなく永続利用可",
        "□ 導入支援費は別途（今回は本体と一体の買切）",
        "□ 利用者数：5ユーザー",
    ]
    yy = H - 220
    for ch in checks:
        text_at(c, rx + 12, yy, ch, "Goth", 7.2)
        yy -= 17
    g.block("asset-box", rx, H - 280, R - rx, 110)

    # 下段：明細（見積→請求→領収の3段階）
    sy = H - 306
    text_at(c, L, sy, "費用内訳（税抜）", "Goth", 9)
    rows = [
        ("本体ライセンス（5ユーザー）", "450,000"),
        ("インストール媒体・マニュアル", "20,000"),
        ("初回導入サポート（1日）", "30,000"),
    ]
    yy = sy - 22
    text_at(c, L + 10, yy, "項目", "Goth", 7.5, color=(0.4, 0.4, 0.4))
    text_at(c, R - 10, yy, "金額（税抜）", "Goth", 7.5, align="r", color=(0.4, 0.4, 0.4))
    yy -= 14
    for name, amt in rows:
        text_at(c, L + 10, yy, name, "Goth", 8)
        text_at(c, R - 10, yy, amt, "Goth", 8, align="r")
        rule(c, L + 10, yy - 5, R - 10, yy - 5, 0.3, color=(0.7, 0.7, 0.7))
        g.textline("lic-" + name[:5], L + 10, yy, name, "Goth", 8)
        yy -= 20
    rule(c, L + 10, yy + 8, R - 10, yy + 8, 0.7)
    text_at(c, L + 10, yy - 6, "税抜合計", "Goth", 8.5)
    text_at(c, R - 10, yy - 6, "500,000", "Goth", 8.5, align="r")
    text_at(c, L + 10, yy - 22, "消費税（10%）", "Goth", 8.5)
    text_at(c, R - 10, yy - 22, "50,000", "Goth", 8.5, align="r")
    text_at(c, L + 10, yy - 38, "税込合計＝受領額", "Goth", 9)
    text_at(c, R - 10, yy - 38, "550,000", "Goth", 9, align="r")

    text_at(c, L, 36, "発行：テスト計測システム株式会社 ／ 架空町ソフトウェア事業部", "Goth", 7.5)
    text_at(c, L, 22, "ご不明点は納品担当まで（架空連絡先）", "Goth", 7)
    square_seal(c, R - 60, 30, 44, "受領", (0.22, 0.22, 0.3))
    micro_label(c, L, 10)


# --------------------------------------------------------------------------
# V050 事務機器リース 月額支払明細書＋領収 A5横 左リース期間/右支払内訳
# --------------------------------------------------------------------------
def draw_v050(c, W, H, g):
    text_at(c, 26, H - 36, "リース料お支払明細書", "Min", 16, color=(0.3, 0.2, 0.12))
    text_at(c, 26, H - 50, "（第18回 分割リース料 / 2024年3月度）", "Goth", 8)
    rule(c, 26, H - 58, W - 26, H - 58, 1.0, color=(0.3, 0.2, 0.12))

    # 左列：リース契約情報（縦並びラベル・値は右寄せ）
    lx = 26
    info = [
        ("契約番号", "TEST-LEASE-5512"),
        ("リース物件", "テストコピー機 型式TX-401"),
        ("契約期間", "2022年10月 〜 2027年9月（60か月）"),
        ("今回回数", "18回 / 60回"),
        ("リース料", "月額 28,000円（税込）"),
        ("物件残価", "リース終了時に無償譲渡"),
    ]
    yy = H - 88
    for k, v in info:
        text_at(c, lx, yy, k, "Goth", 7, color=(0.45, 0.45, 0.45))
        text_at(c, lx, yy - 13, v, "Goth", 8)
        yy -= 34
    g.block("lease-info", lx, yy + 20, 250, H - 88 - yy)

    # 右列：支払内訳（横向き3行の帯）
    bx = lx + 268
    bw = W - 26 - bx
    rect(c, bx, H - 200, bw, 120, 0.7, color=(0.3, 0.2, 0.12))
    lines = [
        ("リース料（税抜）", "25,455"),
        ("消費税等", "2,545"),
        ("支払済額（税込）", "28,000"),
    ]
    yy = H - 108
    for i, (k, v) in enumerate(lines):
        if i == 2:
            rule(c, bx + 8, yy + 8, bx + bw - 8, yy + 8, 0.6)
        text_at(c, bx + 12, yy, k, "Goth", 8.5)
        text_at(c, bx + bw - 12, yy, v + "円", "Goth", 8.5, align="r")
        yy -= 26
    g.block("pay-break", bx, H - 200, bw, 120)

    # 下段：支払予定/実績の横タイムライン
    text_at(c, lx, 110, "支払スケジュール", "Goth", 8.5)
    rule(c, lx, 102, lx + 360, 102, 0.6, color=(0.5, 0.4, 0.3))
    marks = [("2024/2/27", "請求", H - 160), ("2024/3/5", "振込", H - 120),
             ("2024/3/5", "領収済", H - 80)]
    for i, (d, lab, _) in enumerate(marks):
        mx = lx + i * 120
        c.setFillColorRGB(0.3, 0.2, 0.12)
        c.circle(mx, 102, 3.5, stroke=0, fill=1)
        c.setFillColorRGB(0, 0, 0)
        text_at(c, mx, 84, d, "Goth", 7)
        text_at(c, mx, 72, lab, "Goth", 7, color=(0.4, 0.4, 0.4))
    text_at(c, W - 26, 84, "テストリース株式会社", "Goth", 8.5, align="r")
    text_at(c, W - 26, 70, "架空町リース営業部", "Goth", 7, align="r")
    g.block("timeline", lx, 60, 360, 50)
    micro_label(c, lx, 24, "架空テスト", 5.0)


# --------------------------------------------------------------------------
# V051 事務所賃料 領収書 A4縦 中央封筒型（枠＋透かし大文字）
# --------------------------------------------------------------------------
def draw_v051(c, W, H, g):
    # 透かし
    c.saveState()
    c.setFillColorRGB(0.94, 0.94, 0.93)
    c.setFont(F("Min"), 120)
    c.translate(W / 2.0, H / 2.0)
    c.rotate(28)
    c.drawCentredString(0, -40, "領収")
    c.restoreState()
    text_at(c, 34, H - 45, "賃料 領収書", "Min", 18, color=(0.18, 0.18, 0.18))
    text_at(c, W - 34, H - 45, "NO. TEST-R-202406-07", "Goth", 7.5, align="r",
            color=(0.4, 0.4, 0.4))
    micro_label(c, W - 70, H - 58, "架空テスト", 4.9, rot=0)

    frame_x, frame_y = 90, H * 0.28
    frame_w, frame_h = W - 180, H * 0.52
    rect(c, frame_x, frame_y, frame_w, frame_h, 1.2, color=(0.18, 0.18, 0.18))
    # 金額行（中央の帯）
    band_y = frame_y + frame_h * 0.56
    c.setFillColorRGB(0.93, 0.9, 0.85)
    c.rect(frame_x + 8, band_y, frame_w - 16, 44, stroke=0, fill=1)
    c.setFillColorRGB(0, 0, 0)
    text_at(c, frame_x + 24, band_y + 15, "金額（税込）", "Goth", 8)
    text_at(c, frame_x + 140, band_y + 15, "165,000 円", "Goth", 20)
    text_at(c, frame_x + 24, band_y + 30, "※ 賃料 137,500円 + 共益費 12,500円 + 消費税 15,000円", "Goth", 7.2)

    text_at(c, frame_x + 24, frame_y + frame_h - 36, "宛先", "Goth", 8, color=(0.45, 0.45, 0.45))
    text_at(c, frame_x + 90, frame_y + frame_h - 40, "架空 株式会社テスト貿易 御中", "Min", 13)
    text_at(c, frame_x + 24, frame_y + frame_h - 70, "内容", "Goth", 8, color=(0.45, 0.45, 0.45))
    rowsv = [
        "2024年6月分 事務所賃料として",
        "対象物件：テスト市架空町5-5-5 架空ビル4F 区画401号室",
        "賃貸期間：2024年6月1日〜2024年6月30日",
        "支払方法：銀行振込（2024年6月20日入金確認）",
        "振込手数料は借主負担にて別途支払",
    ]
    yy = frame_y + frame_h - 86
    for r in rowsv:
        text_at(c, frame_x + 90, yy, r, "Goth", 8)
        g.textline("rent-" + r[:5], frame_x + 90, yy, r, "Goth", 8)
        yy -= 17
    # 下段：検印と内訳小表
    text_at(c, frame_x + 24, frame_y + 60, "上記正に領収いたしました。", "Goth", 8)
    text_at(c, frame_x + 24, frame_y + 40, "発行日：2024年6月20日", "Goth", 8)
    text_at(c, frame_x + 24, frame_y + 24, "テスト不動産株式会社 / 架空町管理部", "Goth", 7.5)
    ix = frame_x + frame_w - 190
    iy = frame_y + 52
    rule(c, ix, iy + 20, frame_x + frame_w - 20, iy + 20, 0.5, color=(0.5, 0.5, 0.5))
    text_at(c, ix, iy + 6, "賃料", "Goth", 7)
    text_at(c, frame_x + frame_w - 20, iy + 6, "137,500", "Goth", 7, align="r")
    text_at(c, ix, iy - 8, "共益費", "Goth", 7)
    text_at(c, frame_x + frame_w - 20, iy - 8, "12,500", "Goth", 7, align="r")
    text_at(c, ix, iy - 22, "消費税", "Goth", 7)
    text_at(c, frame_x + frame_w - 20, iy - 22, "15,000", "Goth", 7, align="r")
    big_red_seal(c, frame_x + frame_w - 70, frame_y + frame_h - 60, 38, "領収印",
                 "テスト不動産")
    text_at(c, 34, 34,
            "※ 本領収書は2024年6月分の賃料領収書です。共益費・消費税を含みます。",
            "Goth", 7)


# --------------------------------------------------------------------------
# V052 自宅兼事務所 賃料按分（業務用部分） A5縦 二重枠・上下分離
# --------------------------------------------------------------------------
def draw_v052(c, W, H, g):
    pad = 22
    rect(c, pad, pad, W - pad * 2, H - pad * 2, 1.0, color=(0.28, 0.2, 0.3))
    inner = pad + 8
    rect(c, inner, inner, W - inner * 2, H - inner * 2, 0.4, color=(0.28, 0.2, 0.3))
    text_at(c, W / 2.0, H - 52, "居住用賃貸住宅の家賃 一部支払分 領収書", "Min", 12, "c",
            (0.28, 0.2, 0.3))
    text_at(c, W / 2.0, H - 68, "（架空テスト用・一部支払の受領明細）", "Goth", 7, "c",
            color=(0.45, 0.35, 0.45))

    # 上段：物件/契約者
    topy = H - 96
    text_at(c, inner + 16, topy, "物件：テスト市架空町7-7-7 テストハイム203号室", "Goth", 7.8)
    text_at(c, inner + 16, topy - 16, "契約者：架空 二郎 様（個人契約）", "Goth", 7.8)
    text_at(c, inner + 16, topy - 32, "賃貸人：架空住宅管理サービス（架空）", "Goth", 7.8)
    rule(c, inner + 16, topy - 42, W - inner - 16, topy - 42, 0.5, dash=(2, 2))

    # 中段：按分表
    text_at(c, inner + 16, topy - 60, "● 家賃総額と今回支払分", "Goth", 8.5, color=(0.28, 0.2, 0.3))
    tx = inner + 30
    ty = topy - 78
    heads = ["項目", "総額", "支払比率", "今回分"]
    colx = [tx, tx + 120, tx + 200, tx + 270]
    for i, h in enumerate(heads):
        text_at(c, colx[i], ty, h, "Goth", 7, color=(0.45, 0.45, 0.45))
    rows = [
        ("家賃（2024年6月分）", "78,000", "20%", "15,600"),
        ("管理費・共益費", "6,000", "20%", "1,200"),
        ("駐車場使用料", "5,000", "0%", "0"),
        ("光熱水費（概算）", "—", "—", "含まず"),
    ]
    yy = ty - 15
    for name, tot, ratio, biz in rows:
        text_at(c, colx[0], yy, name, "Goth", 7.5)
        text_at(c, colx[1], yy, tot, "Goth", 7.5, align="r")
        text_at(c, colx[2], yy, ratio, "Goth", 7.5, align="r")
        text_at(c, colx[3], yy, biz, "Goth", 7.5, align="r")
        rule(c, tx, yy - 6, colx[3] + 30, yy - 6, 0.3, color=(0.75, 0.7, 0.75))
        yy -= 19
    text_at(c, colx[0], yy, "今回分負担分合計（税込）", "Goth", 8)
    text_at(c, colx[3], yy, "16,800", "Goth", 8, align="r")

    # 下段：領収済み帯
    by = inner + 150
    rect(c, inner + 16, by, W - inner * 2 - 32, 46, 0.7, color=(0.28, 0.2, 0.3))
    text_at(c, inner + 28, by + 28, "今回分負担分として 16,800円 を受領した旨、証明します。", "Goth", 8)
    text_at(c, inner + 28, by + 12, "領収日 2024年6月27日 ／ 賃貸人代理人 架空住宅管理サービス",
            "Goth", 7.3)
    g.block("biz-total", inner + 16, by, W - inner * 2 - 32, 46)
    square_seal(c, W - inner - 70, by + 4, 38, "領収", (0.28, 0.2, 0.3))
    text_at(c, inner + 16, inner + 32,
            "※本書は記載の一部支払額のみの領収証です。総額を再度領収したものではありません。",
            "Goth", 6.8)
    text_at(c, inner + 16, inner + 20, "※家賃総額 78,000円の領収書は別途発行済み。", "Goth", 6.8)
    micro_label(c, W - inner - 50, inner + 20, "架空テスト", 4.6)


# --------------------------------------------------------------------------
# V053 法人向け賠償責任保険 保険料領収書 B5縦 左表紙風縦書きブロック
# --------------------------------------------------------------------------
def draw_v053(c, W, H, g):
    c.setFillColorRGB(0.1, 0.25, 0.28)
    c.rect(0, 0, 110, H, stroke=0, fill=1)
    c.saveState()
    c.setFillColorRGB(0.9, 0.97, 0.97)
    c.translate(72, 120)
    c.rotate(90)
    c.setFont(F("Min"), 16)
    c.drawString(0, 0, "法人向け賠償責任保険")
    c.setFont(F("Goth"), 9)
    c.drawString(0, -22, "保険料 領収書")
    c.restoreState()

    x0 = 132
    text_at(c, x0, H - 46, "保険料領収証", "Min", 18, color=(0.1, 0.25, 0.28))
    text_at(c, x0, H - 62, "証券番号 TEST-PL-88012 / 契約者 架空 株式会社テスト機材", "Goth", 7.8)
    text_at(c, x0, H - 76, "保険種類：請負業者賠償責任保険（業務中・施設内/外）", "Goth", 7.8)
    text_at(c, x0, H - 90, "保険期間：2024年4月1日 〜 2025年3月31日（12か月）", "Goth", 7.8)
    rule(c, x0, H - 100, W - 40, H - 100, 0.9, color=(0.1, 0.25, 0.28))

    # 保険金・保険料の対照表（左上に引受内容、右に料金）
    text_at(c, x0, H - 126, "補償内容と保険料（非課税）", "Goth", 9, color=(0.1, 0.25, 0.28))
    ty = H - 146
    colx = [x0, x0 + 180, x0 + 280]
    for i, h in enumerate(["区分", "保険金額", "保険料"]):
        text_at(c, colx[i], ty, h, "Goth", 7, color=(0.45, 0.45, 0.45))
    ins_rows = [
        ("身体賠償（1名）", "1億円", "—"),
        ("身体賠償（1事故）", "5億円", "—"),
        ("財物賠償（1事故）", "1,000万円", "—"),
        ("基本保険料", "", "62,000"),
        ("割増・割引（長期割引）", "", "-8,000"),
        ("合計保険料（非課税）", "", "54,000"),
    ]
    yy = ty - 16
    for i, (a, b, d) in enumerate(ins_rows):
        if "合計" in a:
            rule(c, x0, yy + 6, W - 40, yy + 6, 0.6)
        text_at(c, colx[0], yy, a, "Goth", 7.6)
        text_at(c, colx[1], yy, b, "Goth", 7.6)
        text_at(c, colx[2], yy, d, "Goth", 7.6, align="r")
        yy -= 17

    # 保険金の支払時に関する説明ボックス（網掛け）
    by = yy - 16
    rect(c, x0, by - 70, W - 40 - x0, 70, 0.6, color=(0.1, 0.25, 0.28))
    text_at(c, x0 + 10, by - 16, "免責・特約事項（概要）", "Goth", 7.5, color=(0.1, 0.25, 0.28))
    para(c, x0 + 10, by - 32, W - 40 - x0 - 20,
         "故意・重過失、地震、戦争等による損害は補償対象外。詳細は普通保険約款・特約をご確認ください。",
         "Goth", 7)
    g.block("hoken-box", x0, by - 70, W - 40 - x0, 70)

    text_at(c, x0, 96, "領収金額：54,000円", "Goth", 11)
    text_at(c, x0, 80, "領収日：2024年3月25日（口座振替にて全額収納）", "Goth", 7.8)
    text_at(c, x0, 66, "上記保険料を領収しました。", "Goth", 7.5)
    text_at(c, W - 40, 52, "テスト損害保険株式会社", "Goth", 9, align="r")
    text_at(c, W - 40, 38, "架空町保険センター", "Goth", 7, align="r")
    big_red_seal(c, W - 90, 150, 34, "収納済", "テスト損保")
    micro_label(c, W - 90, 20, "架空テスト", 4.7)


# --------------------------------------------------------------------------
# V054 社用車 自動車任意保険 領収書 A4縦 上部横長グリッド/下部明細
# --------------------------------------------------------------------------
def draw_v054(c, W, H, g):
    L = 40
    text_at(c, L, H - 40, "自動車保険 保険料 領収書（事業用車両）", "Min", 16,
            color=(0.15, 0.25, 0.5))
    text_at(c, L, H - 56, "テストモーター保険株式会社 ／ 2024年5月分", "Goth", 8)
    text_at(c, W - L, H - 44, "証明書番号 TEST-CAR-2024-0551", "Goth", 7.5, align="r")

    # 上部：車両情報グリッド（4x2）
    gx, gy = L, H - 200
    gw, gh = W - L * 2, 120
    cols = 4
    rws = 2
    cw = gw / cols
    ch = gh / rws
    cells = [
        ("保険契約者", "架空 株式会社テスト建設"),
        ("被保険自動車", "テスト商用バン 型式TX-V210（架空）"),
        ("登録番号", "テスト 330 あ 00-00（架空登録）"),
        ("初度登録", "2021年4月"),
        ("保険種類", "任意保険（対人・対物・車両・人身傷害）"),
        ("運転者限定", "35歳以上・業務限定"),
        ("保険期間", "2024年6月1日 〜 2025年5月31日"),
        ("等級・事故有無", "17等級 / 事故有り（前年1回）"),
    ]
    c.setStrokeColorRGB(0.15, 0.25, 0.5)
    c.setLineWidth(0.6)
    for i in range(cols + 1):
        c.line(gx + i * cw, gy, gx + i * cw, gy + gh)
    for j in range(rws + 1):
        c.line(gx, gy + j * ch, gx + gw, gy + j * ch)
    c.setStrokeColorRGB(0, 0, 0)
    for i, (k, v) in enumerate(cells):
        cx = gx + (i % cols) * cw
        cy = gy + (rws - 1 - i // cols) * ch
        text_at(c, cx + 6, cy + ch - 14, k, "Goth", 6.5, color=(0.45, 0.45, 0.45))
        para(c, cx + 6, cy + ch - 30, cw - 12, v, "Goth", 7.4, 10.5)
        g.block("car-grid%d" % i, cx + 2, cy + 2, cw - 4, ch - 4)

    # 中部：料金内訳（2列）
    ty = gy - 34
    text_at(c, L, ty, "保険料内訳（非課税）", "Goth", 9)
    items = [
        ("対人賠償保険", "9,800"),
        ("対物賠償保険", "12,400"),
        ("人身傷害保険", "6,300"),
        ("車両保険（一般型）", "28,500"),
        ("運転者限定特約", "1,200"),
        ("ノンフリート等級別割増", "8,000"),
    ]
    colw = (W - L * 2) / 2.0
    for i, (k, v) in enumerate(items):
        cx = L + (i % 2) * colw
        yy = ty - 20 - (i // 2) * 20
        text_at(c, cx + 8, yy, k, "Goth", 7.8)
        text_at(c, cx + colw - 12, yy, v + "円", "Goth", 7.8, align="r")
        rule(c, cx + 8, yy - 5, cx + colw - 12, yy - 5, 0.3, color=(0.7, 0.75, 0.85))
        g.textline("car-item%d" % i, cx + 8, yy, k, "Goth", 7.8)
    yy = ty - 20 - 3 * 20 - 8
    text_at(c, L + 8, yy, "保険料合計（非課税）", "Goth", 9)
    text_at(c, W - L - 12, yy, "66,200円", "Goth", 9, align="r")

    # 下部：領収帯
    by = yy - 56
    rect(c, L, by, W - L * 2, 46, 1.0, color=(0.15, 0.25, 0.5))
    text_at(c, L + 10, by + 28, "領収日：2024年5月28日 ／ 金額：66,200円", "Goth", 9)
    text_at(c, L + 10, by + 12, "上記金額を領収いたしました（クレジットカード決済済）", "Goth", 7.5)
    text_at(c, W - L - 10, by + 20, "テストモーター保険株式会社", "Goth", 7.5, align="r")
    g.block("car-receipt", L, by, W - L * 2, 46)

    text_at(c, L, 40, "※ 自賠責保険料（24,830円）は別途・別途領収済み。本領収書は任意保険料のみです。",
            "Goth", 7.2)
    micro_label(c, L, 26, "架空テスト", 5.0)


# --------------------------------------------------------------------------
# V055 銀行振込手数料 受付票 B5横 2段（上受付/下明細） モノスペース風
# --------------------------------------------------------------------------
def draw_v055(c, W, H, g):
    rect(c, 20, 20, W - 40, H - 40, 1.2, color=(0.25, 0.25, 0.25))
    text_at(c, 36, H - 48, "振込手数料 受付票（領収書兼用）", "Goth", 14)
    text_at(c, 36, H - 64, "TEST BANK 架空支店", "Goth", 8, color=(0.4, 0.4, 0.4))
    text_at(c, W - 36, H - 48, "受付日：2024年7月1日", "Goth", 7.5, align="r")
    text_at(c, W - 36, H - 64, "受付番号 TEST-FEE-884-07", "Goth", 7.5, align="r")
    rule(c, 36, H - 74, W - 36, H - 74, 0.8)
    micro_label(c, W - 76, H - 84, "架空テスト", 4.8)

    # 上段：振込依頼人情報（2列）
    ty = H - 92
    lx = 36
    rx = W / 2.0 + 10
    pairs = [
        ("依頼人", "架空 株式会社テスト商事"),
        ("依頼人口座", "架空支店 普通 0000000（架空）"),
        ("振込先", "テスト工業株式会社 様"),
        ("振込先口座", "架空支店 当座 1111111（架空）"),
    ]
    for i, (k, v) in enumerate(pairs):
        cx = lx if i % 2 == 0 else rx
        yy = ty - (i // 2) * 40
        text_at(c, cx, yy, k, "Goth", 6.5, color=(0.45, 0.45, 0.45))
        text_at(c, cx, yy - 13, v, "Goth", 8)
        rule(c, cx, yy - 17, cx + 220, yy - 17, 0.3, color=(0.8, 0.8, 0.8))

    # 下段：明細（モノスペース欄の桁そろえ）
    dy = H - 180
    text_at(c, lx, dy, "【明細】", "Goth", 8)
    mon = "Courier"
    try:
        pdfmetrics.getFont(mon)
    except Exception:
        mon = "Goth"
    mon = "Courier"
    rows = [
        ("振込金額", "250,000", "円"),
        ("振込手数料（他行向け）", "660", "円"),
        ("うち消費税（手数料税込660円の内数）", "60", "円"),
        ("合計請求（振込金額＋手数料）", "250,660", "円"),
    ]
    yy = dy - 22
    for k, v, u in rows:
        c.setFont(F("Goth"), 8)
        c.drawString(lx + 20, yy, k)
        c.drawRightString(W - 80, yy, v.rjust(10))
        c.setFont(F("Goth"), 8)
        c.drawString(W - 70, yy, u)
        yy -= 20
    text_at(c, lx + 20, yy, "※ 手数料は振込とは別に現金で支払済み（領収済）。", "Goth", 7,
            color=(0.4, 0.4, 0.4))
    text_at(c, lx + 20, yy - 14, "本受付票は振込手数料の支払証明です。振込金額の領収書ではありません。",
            "Goth", 6.8, color=(0.4, 0.4, 0.4))

    # 右下の角印
    square_seal(c, W - 100, 42, 48, "受付印", (0.25, 0.25, 0.25))
    text_at(c, 36, 60, "お客さまの資金移動に係る受付票です（テスト発行・架空口座）", "Goth", 6.5)
    text_at(c, 36, 46, "2024/07/01 14:32", "Goth", 6.5)


# --------------------------------------------------------------------------
# V056 元利均等返済 返済予定表兼領収書 A4横 縦罫表＋右側サマリ
# --------------------------------------------------------------------------
def draw_v056(c, W, H, g):
    text_at(c, 36, H - 44, "ローンご返済予定表兼 領収書", "Min", 16, color=(0.12, 0.32, 0.22))
    text_at(c, 36, H - 60,
            "テストローン 契約番号 TEST-LN-2022-009 / 返済方法 元利均等返済 / 元金・利息内訳",
            "Goth", 7.8)
    text_at(c, W - 36, H - 44, "発行 2024年6月25日", "Goth", 7.5, align="r")
    rule(c, 36, H - 70, W - 36, H - 70, 1.0, color=(0.12, 0.32, 0.22))

    # 左表：返済1回分の縦罫詳細
    tx, ty = 36, H - 90
    tw_ = W * 0.62 - 36
    heads = ["回", "返済日", "返済額", "うち元金", "うち利息", "残元金"]
    colw = [tw_ * 0.06, tw_ * 0.2, tw_ * 0.19, tw_ * 0.19, tw_ * 0.17, tw_ * 0.19]
    x = tx
    xs = []
    for cw in colw:
        xs.append(x)
        x += cw
    c.setFillColorRGB(0.85, 0.92, 0.87)
    c.rect(tx, ty - 4, tw_, 20, stroke=0, fill=1)
    c.setFillColorRGB(0, 0, 0)
    for i, h in enumerate(heads):
        text_at(c, xs[i] + 4, ty + 2, h, "Goth", 7)
    rows = [
        ("24", "2024/05/27", "83,900", "68,320", "15,580", "1,402,680"),
        ("25", "2024/06/27", "83,900", "68,760", "15,140", "1,333,920"),
        ("26", "2024/07/27", "83,900", "69,201", "14,699", "1,264,719"),
    ]
    yy = ty - 20
    for r in rows:
        for i, v in enumerate(r):
            text_at(c, xs[i] + 4, yy - 12, v, "Goth", 7.3)
        g.textline("ln-row-%s" % r[0], xs[0] + 4, yy - 12, r[0], "Goth", 7.3)
        yy -= 22
    for i, cx in enumerate(xs + [tx + tw_]):
        rule(c, cx, ty + 16, cx, yy + 10, 0.4, color=(0.5, 0.65, 0.55))
    rule(c, tx, ty - 4, tx + tw_, ty - 4, 0.5, color=(0.5, 0.65, 0.55))

    # 中央注記
    text_at(c, tx, yy - 4, "※ 元利均等返済のため、返済額固定・元金/利息の比率が毎月変化します。", 
            "Goth", 6.8, color=(0.4, 0.4, 0.4))

    # 右サマリ（箱型）
    bx = W * 0.62 + 16
    bw = W - 36 - bx
    rect(c, bx, H - 330, bw, 240, 0.9, color=(0.12, 0.32, 0.22))
    text_at(c, bx + 12, H - 104, "第25回 ご返済分（2024年6月）", "Goth", 8)
    text_at(c, bx + 12, H - 128, "返済額", "Goth", 7, color=(0.4, 0.4, 0.4))
    text_at(c, bx + 12, H - 156, "83,900", "Goth", 22, color=(0.12, 0.32, 0.22))
    text_at(c, bx + 12 + tw("83,900", "Goth", 22) + 4, H - 150, "円", "Goth", 9)
    rule(c, bx + 12, H - 166, bx + bw - 12, H - 166, 0.4)
    det = [
        ("元金部分", "68,760"),
        ("利息部分", "15,140"),
        ("完済までの残回数", "11回"),
        ("返済口座（架空）", "普通 0000000"),
    ]
    yy = H - 184
    for k, v in det:
        text_at(c, bx + 12, yy, k, "Goth", 7)
        text_at(c, bx + bw - 12, yy, v, "Goth", 7, align="r")
        yy -= 15
    text_at(c, bx + 12, yy - 4, "2024年6月27日 口座振替にて領収。", "Goth", 7)
    big_red_seal(c, bx + bw / 2.0, H - 300, 28, "領収済", "テストローン", False)
    g.block("right-summary", bx, H - 330, bw, 240)

    text_at(c, 36, 36,
            "元金・利息の内訳は上記のとおりです。次回返済日は予定です。",
            "Goth", 6.8)
    micro_label(c, W - 80, 22, "架空テスト", 4.8)


# --------------------------------------------------------------------------
# V057 収入印紙購入 領収証 A5横 左券面/右切手風ブロック
# --------------------------------------------------------------------------
def draw_v057(c, W, H, g):
    text_at(c, 26, H - 38, "収入印紙 購入代金 領収証", "Min", 15, color=(0.4, 0.15, 0.15))
    text_at(c, 26, H - 52, "テスト郵便局（架空） ／ 受付番号 TEST-Y-2024-0334", "Goth", 7.5)
    rule(c, 26, H - 60, W - 26, H - 60, 1.0, color=(0.4, 0.15, 0.15))

    # 左：領収内容（縦積み）
    text_at(c, 26, H - 86, "領収日：2024年6月10日", "Goth", 8)
    text_at(c, 26, H - 102, "購入者：架空 株式会社テスト総務", "Goth", 8)
    text_at(c, 26, H - 126, "購入印紙", "Goth", 8, color=(0.5, 0.5, 0.5))
    para(c, 26, H - 146, 230, "200円券 10枚 ／ 1,000円券 5枚", "Goth", 8.5)
    text_at(c, 26, H - 176, "合計", "Goth", 8, color=(0.5, 0.5, 0.5))
    text_at(c, 26, H - 210, "7,000", "Goth", 24, color=(0.4, 0.15, 0.15))
    text_at(c, 26 + tw("7,000", "Goth", 24) + 4, H - 204, "円（税込・非課税取扱）", "Goth", 8)
    text_at(c, 26, H - 228, "支払方法：現金", "Goth", 7.5)
    text_at(c, 26, H - 242, "※ 印紙税は消費税の対象外です。", "Goth", 7, color=(0.45, 0.45, 0.45))
    g.block("left-yush", 26, H - 250, 240, 170)

    # 右：切手風ブロック（4x2）
    bx = W * 0.58
    text_at(c, bx, H - 86, "購入明細（印紙の券種）", "Goth", 7.5, color=(0.4, 0.4, 0.4))
    stamps = [("200円", "×5", "1,000"), ("200円", "×5", "1,000"),
              ("1,000円", "×3", "3,000"), ("1,000円", "×2", "2,000")]
    sw = 86
    sh = 58
    for i, (v, q, a) in enumerate(stamps):
        sx = bx + (i % 2) * (sw + 16)
        sy = H - 110 - (i // 2) * (sh + 14)
        # 切手風のギザギザ枠
        c.setDash(1.2, 1.2)
        rect(c, sx, sy - sh, sw, sh, 0.8, color=(0.4, 0.15, 0.15))
        c.setDash()
        text_at(c, sx + sw / 2.0, sy - 22, v, "Goth", 12, "c", (0.4, 0.15, 0.15))
        text_at(c, sx + sw / 2.0, sy - 36, q, "Goth", 8, "c")
        text_at(c, sx + sw - 6, sy - 50, a, "Goth", 6.5, "r")
        g.block("stamp%d" % i, sx, sy - sh, sw, sh)
    text_at(c, bx, 52, "合計枚数 15枚 / 合計 7,000円", "Goth", 7.5)
    text_at(c, W - 26, 36, "領収印", "Goth", 6.5, align="r", color=(0.45, 0.45, 0.45))
    square_seal(c, W - 84, 44, 46, "領収済", (0.4, 0.15, 0.15))
    micro_label(c, 26, 8, "架空テスト", 4.8)


# --------------------------------------------------------------------------
# V058 自治体証明書手数料 領収書 A5縦 窓口形式（左窓口/右領収書）
# --------------------------------------------------------------------------
def draw_v058(c, W, H, g):
    pad = 20
    rect(c, pad, pad, W - pad * 2, H - pad * 2, 0.8, color=(0.15, 0.3, 0.5))
    text_at(c, W / 2.0, H - 48, "テスト市役所 架空町支所", "Goth", 12, "c", (0.15, 0.3, 0.5))
    text_at(c, W / 2.0, H - 62, "証明書発行手数料 領収書", "Min", 13, "c", (0.15, 0.3, 0.5))
    rule(c, pad + 16, H - 72, W - pad - 16, H - 72, 0.7, color=(0.15, 0.3, 0.5))

    # 窓口欄（左）
    text_at(c, pad + 16, H - 96, "窓口", "Goth", 7, color=(0.5, 0.5, 0.5))
    text_at(c, pad + 70, H - 96, "04", "Goth", 8)
    text_at(c, pad + 16, H - 114, "受付日", "Goth", 7, color=(0.5, 0.5, 0.5))
    text_at(c, pad + 70, H - 114, "2024年7月3日（水）", "Goth", 8)
    text_at(c, pad + 16, H - 132, "申請者", "Goth", 7, color=(0.5, 0.5, 0.5))
    text_at(c, pad + 70, H - 132, "架空 三郎", "Goth", 8)
    text_at(c, pad + 16, H - 150, "証明書", "Goth", 7, color=(0.5, 0.5, 0.5))
    text_at(c, pad + 70, H - 150, "住民票の写し 1通", "Goth", 8)
    text_at(c, pad + 16, H - 168, "手数料", "Goth", 7, color=(0.5, 0.5, 0.5))
    text_at(c, pad + 70, H - 168, "300円", "Goth", 8)
    text_at(c, pad + 16, H - 186, "支払方法", "Goth", 7, color=(0.5, 0.5, 0.5))
    text_at(c, pad + 70, H - 186, "現金", "Goth", 8)
    rule(c, pad + 16, H - 196, W / 2.0 - 10, H - 196, 0.4, dash=(2, 2))

    # 右：領収書面（金額枠）
    bx = W / 2.0 + 6
    bxx = pad + (W - pad * 2) * 0.5 + 10
    bx = bxx
    rect(c, bx, H - 190, W - pad - 14 - bx, 80, 0.6, color=(0.15, 0.3, 0.5))
    text_at(c, bx + 8, H - 124, "領収金額", "Goth", 7, color=(0.5, 0.5, 0.5))
    text_at(c, bx + 8, H - 160, "300", "Goth", 24)
    text_at(c, bx + 8 + tw("300", "Goth", 24) + 4, H - 154, "円", "Goth", 9)
    text_at(c, bx + 8, H - 180, "住民票の写し 1通分", "Goth", 7)
    g.block("jichitai-amount", bx, H - 190, W - pad - 14 - bx, 80)

    # 下段：備考と発行元
    text_at(c, pad + 16, H - 224, "備考", "Goth", 7, color=(0.5, 0.5, 0.5))
    para(c, pad + 70, H - 224, W - pad * 2 - 86,
         "手数料はテスト市条例に基づく定額です。※架空自治体・架空手続きのテスト発行物です。",
         "Goth", 7)
    rule(c, pad + 16, 120, W - pad - 16, 120, 0.5, color=(0.5, 0.5, 0.5))
    text_at(c, pad + 16, 100, "2024年7月3日 テスト市役所架空町支所 収納係", "Goth", 8)
    text_at(c, pad + 16, 84, "担当：架空 係長", "Goth", 7)
    square_seal(c, W - 96, 60, 48, "収納済", (0.15, 0.3, 0.5))
    micro_label(c, pad + 16, pad + 8, "架空テスト", 4.7)


# --------------------------------------------------------------------------
# V059 国税 個人事業主の予定納税（納付書＋領収証印） A4縦 2つの領収証（左右）
# --------------------------------------------------------------------------
def draw_v059(c, W, H, g):
    text_at(c, W / 2.0, H - 42, "所得税・予定納税（第1期分）の納付を証明する領収証",
            "Min", 13, "c")
    text_at(c, W / 2.0, H - 58, "テスト税務署（架空）発行／架空町税務部", "Goth", 7.5, "c",
            color=(0.45, 0.45, 0.45))
    rule(c, 36, H - 68, W - 36, H - 68, 0.9, color=(0.3, 0.15, 0.15))

    # 2枚の領収証 左/右（領収証A: 納付書添付用、B: 納税者控え）
    cw = (W - 36 * 2 - 24) / 2.0
    top = H - 84
    ch = H - 84 - 120
    labels = [("領収証（納税者控え）", "A"), ("領収証（納付書使用用）", "B")]
    for i, (lab, letter) in enumerate(labels):
        x = 36 + i * (cw + 24)
        rect(c, x, 120, cw, ch, 0.6, color=(0.3, 0.15, 0.15))
        text_at(c, x + 10, 120 + ch - 20, lab, "Goth", 8, color=(0.3, 0.15, 0.15))
        text_at(c, x + cw - 10, 120 + ch - 20, letter, "Goth", 14, align="r",
                color=(0.3, 0.15, 0.15))
        rows = [
            ("納税者", "架空 商店（個人事業主）"),
            ("納税者番号", "TEST-NO-0000-0000"),
            ("税目", "所得税"),
            ("納期", "2024年7月1日 〜 2024年7月31日"),
            ("納付区分", "予定納税 第1期分"),
            ("本税", "92,400円"),
            ("延滞税・加算税", "0円"),
            ("納付年月日", "2024年7月25日"),
        ]
        yy = 120 + ch - 42
        for k, v in rows:
            text_at(c, x + 10, yy, k, "Goth", 6.5, color=(0.5, 0.5, 0.5))
            text_at(c, x + 10, yy - 12, v, "Goth", 7.6)
            rule(c, x + 10, yy - 16, x + cw - 10, yy - 16, 0.3, color=(0.8, 0.75, 0.75))
            yy -= 28
        text_at(c, x + 10, yy + 4, "納付税額（納付額）", "Goth", 6.5, color=(0.5, 0.5, 0.5))
        text_at(c, x + 10, yy - 18, "92,400", "Goth", 20, color=(0.3, 0.15, 0.15))
        text_at(c, x + 10 + tw("92,400", "Goth", 20) + 4, yy - 14, "円", "Goth", 8)
        g.block("tax-receipt%d" % i, x, 120, cw, ch)
        if letter == "A":
            square_seal(c, x + cw - 72, 130, 44, "税務署", (0.3, 0.15, 0.15))
        else:
            square_seal(c, x + cw - 72, 130, 44, "収納済", (0.3, 0.15, 0.15))

    text_at(c, 36, 84,
            "上記納付額は税目・納期区分に対する予定納税です。確定申告により精算されます。",
            "Goth", 7.2)
    text_at(c, 36, 70,
            "※ 本領収証は架空テスト用の見本であり、実際の国税納付証明には使用できません。",
            "Goth", 7.0, color=(0.45, 0.45, 0.45))
    micro_label(c, W - 80, 22, "架空テスト", 4.8)


# --------------------------------------------------------------------------
# V060 自治体の違反金/過料 納付通知兼領収証（支払済） A5横 表＋下部帯
# --------------------------------------------------------------------------
def draw_v060(c, W, H, g):
    text_at(c, 26, H - 38, "過料（違反金） 納付通知兼 領収証", "Min", 14, color=(0.45, 0.2, 0.05))
    text_at(c, 26, H - 52, "テスト市架空町 環境美化条例に基づく過料", "Goth", 7.8)
    text_at(c, W - 26, H - 38, "納付番号 TEST-PEN-2024-0512", "Goth", 7.5, align="r")
    rule(c, 26, H - 60, W - 26, H - 60, 1.0, color=(0.45, 0.2, 0.05))

    # 上段：通知内容表（横罫のみ）
    rows = [
        ("対象者", "架空 株式会社テスト工業（架空町工場）"),
        ("違反日時", "2024年5月12日 14:20"),
        ("違反場所", "テスト市架空町10丁目 架空公園周辺"),
        ("違反内容", "条例第12条に基づく指定場所外投棄（架空の事例）"),
        ("納付期限", "2024年5月31日"),
    ]
    yy = H - 82
    for k, v in rows:
        text_at(c, 26, yy, k, "Goth", 7, color=(0.5, 0.5, 0.5))
        text_at(c, 110, yy, v, "Goth", 8)
        rule(c, 26, yy - 6, W - 26, yy - 6, 0.3, color=(0.8, 0.75, 0.7))
        yy -= 20
    text_at(c, 26, yy - 2, "納付金額", "Goth", 7, color=(0.5, 0.5, 0.5))
    text_at(c, 110, yy - 2, "12,000円", "Goth", 8)

    # 中段：納付実績の帯（太枠）
    by = H - 236
    rect(c, 26, by, W - 52, 46, 1.4, color=(0.45, 0.2, 0.05))
    text_at(c, 36, by + 28, "納付日：2024年5月20日 ／ 納付金額：12,000円（現金）", "Goth", 8.5)
    text_at(c, 36, by + 12, "上記金額を過料として領収しました（領収済）。", "Goth", 7.5)
    text_at(c, W - 36, by + 20, "テスト市架空町 会計課", "Goth", 7.5, align="r")
    g.block("penalty-band", 26, by, W - 52, 46)

    # 下段：説明＋角印
    para(c, 26, by - 22, W - 130,
         "※ 本書は記載の納付番号に対する収納を証するものです。",
         "Goth", 7)
    text_at(c, 26, by - 56, "※ 納付期限までに納付済みのため、延滞金は発生していません。", "Goth", 7)
    square_seal(c, W - 96, by - 64, 46, "納付済", (0.45, 0.2, 0.05))
    micro_label(c, 26, 12, "架空テスト", 4.8)


# --------------------------------------------------------------------------
# メタ情報（マニフェスト）
# --------------------------------------------------------------------------
CASES = [
    {
        "case_id": "V041", "title": "電力メータ検針詳細と請求内訳（A4縦・4分割メータ欄）",
        "layout_id": "flashnext-41", "layout_description":
            "A4縦。右端に縦書きの濃紺帯でお客さま番号、上部に発行者ブロック、中央に4分割のメータ指示値セル、その下に料金区分の横罫表、最下部に大字請求額と赤い料金確認印。",
        "document_kind": "statement", "merchant": "テスト電力株式会社",
        "date": "2024-06-03", "date_printed": "2024年6月3日", "total": 25968,
        "acceptable_accounts": ["水道光熱費", "旅費交通費"],
        "expected_decision": "propose",
        "human_context": "架空荘3号室の電力請求明細。宛名は架空 太郎。2024年6月25日口座振替予定と印刷されており、実際の入金・引き落としの有無は外部情報にない。",
        "review_questions": ["口座振替が実際に完了したか確認が必要か", "業務用比率はあるか（自宅兼事務所か）"],
        "features": ["A4縦", "4分割メータ欄", "料金区分表", "縦帯お客さま番号", "赤い丸印"],
        "items": [
            {"name": "基本料金（30Aまで）", "gross": 2860, "rate": "10"},
            {"name": "電力量料金（第1段階）", "gross": 3516, "rate": "10"},
            {"name": "電力量料金（第2段階）", "gross": 6336, "rate": "10"},
            {"name": "電力量料金（第3段階）", "gross": 10988, "rate": "10"},
            {"name": "燃料費調整額", "gross": 673, "rate": "10"},
            {"name": "再生可能エネルギー賦課金", "gross": 1683, "rate": "10"},
            {"name": "口座振替割引", "gross": -88, "rate": "10"},
        ],
    },
    {
        "case_id": "V042", "title": "ガス料金 領収書＋検針明細（A5横・左右分割）",
        "layout_id": "flashnext-42", "layout_description":
            "A5横。左パネルに二重枠の領収書（金額大字・現金受領・角印）、右パネルに縦罫・横罫の検針明細表と使用量サマリ。中央にダッシュ線の切り取り線。",
        "document_kind": "voucher", "merchant": "テスト都市ガス株式会社",
        "date": "2024-05-14", "date_printed": "2024年5月14日", "total": 4437,
        "acceptable_accounts": ["水道光熱費"],
        "expected_decision": "propose",
        "human_context": "2024年4月分ガス料金を現金で受領。18m³使用。宛名は架空 花子。",
        "review_questions": ["現金支払いの裏付け（領収印・領収書）は十分か"],
        "features": ["A5横", "左右分割", "縦罫横罫明細", "角印", "使用量m³"],
        "items": [
            {"name": "基本料金（20m³まで）", "gross": 1056, "rate": "10"},
            {"name": "従量料金（0〜20m³）", "gross": 3028, "rate": "10"},
            {"name": "メーター使用料", "gross": 150, "rate": "10"},
            {"name": "お得な料金プラン割引", "gross": -200, "rate": "10"},
            {"name": "消費税等", "gross": 403, "rate": "10"},
        ],
    },
    {
        "case_id": "V043", "title": "上下水道料金 領収書（A4縦・上下2面・メータ縦罫）",
        "layout_id": "flashnext-43", "layout_description":
            "A4縦を上下に折り目線で二分し、上はお客さま控え・下は控えの二枚組。各半面は左に領収金額、右上に口径・指針の縦罫4列表、右下に使用期間と課金内訳。",
        "document_kind": "voucher", "merchant": "テスト市水道局（架空）",
        "date": "2024-06-18", "date_printed": "2024年6月18日", "total": 3410,
        "acceptable_accounts": ["水道光熱費"],
        "expected_decision": "propose",
        "human_context": "2024年4月分の上下水道料金を領収。水道1,980円・下水道1,430円。宛名は架空 一郎。",
        "review_questions": ["二枚組の控えはいずれも同一取引か"],
        "features": ["A4縦", "上下二枚組", "折り目線", "メータ縦罫表", "二重角印"],
        "items": [
            {"name": "水道料金", "gross": 1980, "rate": "10"},
            {"name": "下水道料金", "gross": 1430, "rate": "10"},
        ],
    },
    {
        "case_id": "V044", "title": "携帯2回線 合算領収書（A4縦・回線ツリー）",
        "layout_id": "flashnext-44", "layout_description":
            "A4縦。2つの回線カードを左右に並べ、中央の分岐線で根（合算内訳）に接続するツリー構造。回線ごとに小計・税・合計を独立表示し、下部に割引・合算請求額を表示。",
        "document_kind": "invoice_receipt", "merchant": "架空モバイル通信株式会社",
        "date": "2024-06-27", "date_printed": "2024-06-27", "total": 13479,
        "acceptable_accounts": ["通信費"],
        "expected_decision": "propose",
        "human_context": "事業部の2回線（本社・営業）の法人カード払い。端末割賦は別途請求で合算外。",
        "review_questions": ["私用通話が含まれていないか", "端末割賦の取扱い"],
        "features": ["A4縦", "回線ツリー", "回線別内訳", "傾けた領収済角印", "端末割賦別建て"],
        "items": [
            {"name": "回線A 基本プラン", "gross": 5200, "rate": "10"},
            {"name": "回線A 通話・通信量超過", "gross": 1000, "rate": "10"},
            {"name": "回線A オプション", "gross": 330, "rate": "10"},
            {"name": "回線A ユニバーサルサービス料", "gross": 2, "rate": "10"},
            {"name": "回線A 消費税", "gross": 653, "rate": "10"},
            {"name": "回線B 基本プラン", "gross": 5200, "rate": "10"},
            {"name": "回線B オプション", "gross": 330, "rate": "10"},
            {"name": "回線B ユニバーサルサービス料", "gross": 2, "rate": "10"},
            {"name": "ファミリー割引", "gross": -891, "rate": "10"},
        ],
    },
    {
        "case_id": "V045", "title": "固定回線 開通工事費＋月額 明細（B5横・左ナビ帯）",
        "layout_id": "flashnext-45", "layout_description":
            "B5横。左端に濃紺の縦書きナビ帯。上部に開通工事費帯（全額キャンペーン充当・請求0円）、その下に2群の月額明細、最下部に斜線ハッチ背景の合計帯と領収注記。",
        "document_kind": "invoice_receipt", "merchant": "テスト光ネットワーク株式会社",
        "date": "2024-04-10", "date_printed": "2024年4月10日", "total": 8980,
        "acceptable_accounts": ["通信費"],
        "expected_decision": "propose",
        "human_context": "2024年3月の月額利用分と開通工事費の案内。工事費44,000円は全額キャンペーン充当。口座振替済みと印刷。",
        "review_questions": ["キャンペーン充当は費用計上の対象外でよいか", "私用回線か事業用回線か"],
        "features": ["B5横", "縦ナビ帯", "工事費キャンペーン充当", "斜線ハッチ合計帯", "2群明細"],
        "items": [
            {"name": "光回線 10ギガ", "gross": 6600, "rate": "10"},
            {"name": "回線基本料", "gross": 1100, "rate": "10"},
            {"name": "Wi-Fi機器レンタル", "gross": 550, "rate": "10"},
            {"name": "電話基本料", "gross": 550, "rate": "10"},
            {"name": "電話通話料", "gross": 180, "rate": "10"},
        ],
    },
    {
        "case_id": "V046", "title": "クラウドサーバー 月次利用明細（A4縦・三列タイル）",
        "layout_id": "flashnext-46", "layout_description":
            "A4縦。上部40%弱を濃色ヘッダー帯にし、下にコンピューティング／ネットワーク／ライセンスの3枚のカード型タイルを横並び。下部に税抜小計・消費税・決済済総額の集計行。",
        "document_kind": "statement", "merchant": "TEST CLOUD（架空クラウド事業者）",
        "date": "2024-06-01", "date_printed": "2024年6月1日", "total": 30162,
        "acceptable_accounts": ["通信費", "支払手数料"],
        "expected_decision": "propose",
        "human_context": "2024年5月分。カード決済済み。事業利用（テストラボ）。",
        "review_questions": ["個人課金アカウントか法人か"],
        "features": ["A4縦", "三列カードタイル", "濃色ヘッダー", "税区分明記", "カード決済済"],
        "items": [
            {"name": "vCPU 時間", "gross": 7440, "rate": "10"},
            {"name": "メモリ GB時間", "gross": 8680, "rate": "10"},
            {"name": "スナップショット", "gross": 200, "rate": "10"},
            {"name": "アウトバウンド転送", "gross": 2400, "rate": "10"},
            {"name": "ロードバランサー", "gross": 2500, "rate": "10"},
            {"name": "管理画面利用料", "gross": 1200, "rate": "10"},
            {"name": "サポート（スタンダード）", "gross": 5000, "rate": "10"},
            {"name": "消費税10%", "gross": 2742, "rate": "10"},
        ],
    },
    {
        "case_id": "V047", "title": "ドメイン更新料 領収書（A5縦・中央レシート風）",
        "layout_id": "flashnext-47", "layout_description":
            "A5縦。全体を中央寄せの1枚のレシート風に構成。ダッシュ枠、見出し、右寄せの項目値、中央に項目内訳、下部に発行者名と丸印。",
        "document_kind": "voucher", "merchant": "テストネームサービス株式会社",
        "date": "2024-03-18", "date_printed": "2024/03/18", "total": 1122,
        "acceptable_accounts": ["通信費", "租税公課"],
        "expected_decision": "propose",
        "human_context": "ドメイン1年の更新料をカードで決済済み。ICANN手数料を含む。",
        "review_questions": ["ドメイン名の登録者と請求先が同一か"],
        "features": ["A5縦", "ダッシュ枠", "中央寄せレシート", "カード決済", "ドメイン名明記"],
        "items": [
            {"name": "ドメイン更新料（.example）", "gross": 1000, "rate": "10"},
            {"name": "プライバシー保護オプション", "gross": 0, "rate": "10"},
            {"name": "ICANN手数料", "gross": 20, "rate": "10"},
            {"name": "消費税", "gross": 102, "rate": "10"},
        ],
    },
    {
        "case_id": "V048", "title": "年次SaaS 年度跨ぎライセンス 領収書（A4横・左期間パネル）",
        "layout_id": "flashnext-48", "layout_description":
            "A4横。左に契約期間（2028/4〜2029/3）を明示する濃色パネル、右に宛先、その下に品目・数量・単価・小計の縦罫明細表。表下に領収注記と角印。年度跨ぎ注記を最下段に独立配置。",
        "document_kind": "invoice_receipt", "merchant": "テストソフトウェア株式会社",
        "date": "2028-03-05", "date_printed": "2028年3月5日", "total": 682000,
        "acceptable_accounts": ["通信費", "支払手数料"],
        "expected_decision": "propose",
        "human_context": "25名分の年額ERPライセンス。サービス期間は2028年4月〜2029年3月で会計年度を跨ぐ。銀行振込済み。期間按分が必要か要検討。",
        "review_questions": ["サービス期間が会計期間を跨ぐため期間按分の要否を確認"],
        "features": ["A4横", "左期間パネル", "年度跨ぎ", "縦罫明細", "銀行振込領収"],
        "items": [
            {"name": "ERPライセンス（年額・25名）", "gross": 480000, "rate": "10"},
            {"name": "初期導入サポート", "gross": 120000, "rate": "10"},
            {"name": "追加アドオン（会計連携）", "gross": 60000, "rate": "10"},
            {"name": "長期契約割引（年額）", "gross": -40000, "rate": "10"},
            {"name": "消費税10%", "gross": 62000, "rate": "10"},
        ],
    },
    {
        "case_id": "V049", "title": "ソフトウェア買切ライセンス 資産候補票（A4縦・三段）",
        "layout_id": "flashnext-49", "layout_description":
            "A4縦。上段は物件概要の横帯、中段は左に受領金額・右に資産候補チェックの箱、下段は項目・金額（税抜）の横罫表と税込合計。資産判定を促す概要票として構成。",
        "document_kind": "invoice_receipt", "merchant": "テスト計測システム株式会社",
        "date": "2024-02-28", "date_printed": "2024年2月28日", "total": 550000,
        "acceptable_accounts": ["消耗品費", "工具器具備品", "長期前払費用"],
        "expected_decision": "asset_review",
        "human_context": "永久（買切）ライセンス5本、導入サポート込み。金額が大きく資産計上か費用化かの判断が必要。税務上の判定は断定していない。",
        "review_questions": ["無形固定資産として資産計上すべきか、費用処理か", "減価償却/償却方法は経理方針か", "サポート部分の按分要否"],
        "features": ["A4縦", "三段構成", "資産候補チェック", "買切ライセンス", "税抜/税込併記"],
        "items": [
            {"name": "本体ライセンス（5ユーザー）", "gross": 450000, "rate": "10"},
            {"name": "インストール媒体・マニュアル", "gross": 20000, "rate": "10"},
            {"name": "初回導入サポート（1日）", "gross": 30000, "rate": "10"},
            {"name": "消費税10%", "gross": 50000, "rate": "10"},
        ],
    },
    {
        "case_id": "V050", "title": "事務機器リース 月額支払明細（A5横・左契約右内訳）",
        "layout_id": "flashnext-50", "layout_description":
            "A5横。左列に契約情報のラベル・値ペアを縦積みし、右列に税抜リース料・消費税・支払済額の内訳箱。下段に請求→振込→領収の横タイムライン。",
        "document_kind": "statement", "merchant": "テストリース株式会社",
        "date": "2024-03-05", "date_printed": "2024/3/5", "total": 28000,
        "acceptable_accounts": ["リース料", "地代家賃", "消耗品費"],
        "expected_decision": "propose",
        "human_context": "60回分割の18回目。月額28,000円税込を振込済み。物件はコピー機。",
        "review_questions": ["リース契約かレンタルか", "少額リース特例の適用有無"],
        "features": ["A5横", "契約情報と内訳の左右分割", "支払タイムライン", "60回分割", "無償譲渡"],
        "items": [
            {"name": "リース料（税抜）", "gross": 25455, "rate": "10"},
            {"name": "消費税等", "gross": 2545, "rate": "10"},
        ],
    },
    {
        "case_id": "V051", "title": "事務所賃料 領収書（A4縦・封筒型枠＋透かし）",
        "layout_id": "flashnext-51", "layout_description":
            "A4縦。背景に回転した「領収」の透かし。中央に太枠を置き、上段に宛先・内容行、中段に色付き金額帯（賃料+共益費+税）、下段に検印と内訳小表。",
        "document_kind": "voucher", "merchant": "テスト不動産株式会社",
        "date": "2024-06-20", "date_printed": "2024年6月20日", "total": 165000,
        "acceptable_accounts": ["地代家賃"],
        "expected_decision": "propose",
        "human_context": "2024年6月分事務所賃料（賃料+共益費+消費税）。銀行振込。物件は架空ビル4F。",
        "review_questions": ["共益費の按分対象か", "振込手数料は別途か"],
        "features": ["A4縦", "封筒型枠", "透かし文字", "帯型金額", "赤い丸印"],
        "items": [
            {"name": "賃料", "gross": 137500, "rate": "10"},
            {"name": "共益費", "gross": 12500, "rate": "10"},
            {"name": "消費税", "gross": 15000, "rate": "10"},
        ],
    },
    {
        "case_id": "V052", "title": "自宅兼事務所 家賃の業務用按分 領収書（A5縦・二重枠按分表）",
        "layout_id": "flashnext-52", "layout_description":
            "A5縦。二重の細枠内に上段：物件・契約者、中段：家賃総額・面積比・業務用負担分の4列按分表、下段：業務用負担分の受領帯。総家賃領収とは異なる按分証明であることを明記。",
        "document_kind": "other", "merchant": "架空住宅管理サービス（架空）",
        "date": "2024-06-27", "date_printed": "2024年6月27日", "total": 16800,
        "acceptable_accounts": ["地代家賃"],
        "expected_decision": "propose",
        "human_context": "個人契約の自宅兼事務所。家賃総額78,000円のうち面積比20%の業務用負担分（家賃15,600+管理費1,200）のみの領収書。光熱水費は含まず。",
        "review_questions": ["面積比20%の妥当性の根拠資料はあるか", "総家賃の領収書は別途あるか"],
        "features": ["A5縦", "二重枠", "面積比按分", "業務用部分のみ", "総家賃との差額が居住用"],
        "items": [
            {"name": "家賃1か月分の業務用按分（20%）", "gross": 15600, "rate": "unknown"},
            {"name": "管理費・共益費の業務用按分（20%）", "gross": 1200, "rate": "unknown"},
        ],
    },
    {
        "case_id": "V053", "title": "法人向け賠償責任保険 保険料領収書（B5縦・左縦書き帯）",
        "layout_id": "flashnext-53", "layout_description":
            "B5縦。左に濃い青緑の縦書き帯、右に保険内容・保険金額・保険料の対照表、網掛けの免責概説ボックス、下部に領収金額と発行者の丸印。",
        "document_kind": "invoice_receipt", "merchant": "テスト損害保険株式会社",
        "date": "2024-03-25", "date_printed": "2024年3月25日", "total": 54000,
        "acceptable_accounts": ["保険料", "損害保険料"],
        "expected_decision": "propose",
        "human_context": "請負業者賠償責任保険。保険期間2024/4〜2025/3。口座振替で全額領収。保険金額は架空の値。",
        "review_questions": ["保険期間の按分が必要か", "損害保険料控除の対象か"],
        "features": ["B5縦", "縦書き帯", "保険金対照表", "免責概要網掛け", "月払いか年払いか"],
        "items": [
            {"name": "基本保険料", "gross": 62000, "rate": "10"},
            {"name": "長期割引", "gross": -8000, "rate": "10"},
            {"name": "消費税等", "gross": 0, "rate": "10"},
        ],
    },
    {
        "case_id": "V054", "title": "社用車任意保険 領収書（A4縦・車両情報グリッド）",
        "layout_id": "flashnext-54", "layout_description":
            "A4縦。上部に4列×2行の車両情報グリッド（罫線なし外枠）、中段に保険種目を2列に配置した料金内訳、下部に太枠の領収帯。自賠責は別途と注記。",
        "document_kind": "invoice_receipt", "merchant": "テストモーター保険株式会社",
        "date": "2024-05-28", "date_printed": "2024年5月28日", "total": 66200,
        "acceptable_accounts": ["保険料", "損害保険料", "車両費"],
        "expected_decision": "propose",
        "human_context": "事業用バンの任意保険料。カード決済済み。自賠責は別途。運転者限定35歳以上・業務限定。",
        "review_questions": ["私用運転分の按分要否", "自賠責と任意の区分"],
        "features": ["A4縦", "車両情報グリッド", "2列内訳", "太枠領収帯", "等級17"],
        "items": [
            {"name": "対人賠償保険", "gross": 9800, "rate": "10"},
            {"name": "対物賠償保険", "gross": 12400, "rate": "10"},
            {"name": "人身傷害保険", "gross": 6300, "rate": "10"},
            {"name": "車両保険（一般型）", "gross": 28500, "rate": "10"},
            {"name": "運転者限定特約", "gross": 1200, "rate": "10"},
            {"name": "ノンフリート等級別割増", "gross": 8000, "rate": "10"},
        ],
    },
    {
        "case_id": "V055", "title": "銀行振込手数料 受付票兼領収（B5横・等幅モノスペース明細）",
        "layout_id": "flashnext-55", "layout_description":
            "B5横。外枠太罫。上段に依頼人・振込先の2列情報、下段に等幅フォントで桁を揃えた手数料明細。振込金額と手数料の合計を分離し、本票が手数料の支払証明であることを明示。",
        "document_kind": "voucher", "merchant": "TEST BANK 架空支店（架空銀行）",
        "date": "2024-07-01", "date_printed": "2024年7月1日", "total": 660,
        "acceptable_accounts": ["支払手数料"],
        "expected_decision": "propose",
        "human_context": "他行向け振込（250,000円）の手数料660円を現金で支払い。領収書記載額は手数料660円であるが、振込金額250,000円とは別で合計250,660円の支払。本受付票は手数料部分の証明。",
        "review_questions": ["手数料660円だけが費用計上対象か（振込元本は貸借・仕入の対象）", "振込金額の行先・領収書と混同していないか"],
        "features": ["B5横", "等幅フォント", "手数料のみ領収", "振込元本の別建て", "受付印"],
        "items": [
            {"name": "振込手数料（他行向け）", "gross": 600, "rate": "10"},
            {"name": "消費税（手数料に対し10%）", "gross": 60, "rate": "10"},
        ],
    },
    {
        "case_id": "V056", "title": "ローン元利均等返済 予定表兼領収（A4横・左縦罫表/右箱サマリ）",
        "layout_id": "flashnext-56", "layout_description":
            "A4横。左に回・日付・返済額・元金・利息・残元金の6列縦罫表（3期分）、右に大きな返済額とその元金/利息の内訳を箱型で表示。元金と利息を明確に分離。",
        "document_kind": "statement", "merchant": "テストローン株式会社（架空金融）",
        "date": "2024-06-27", "date_printed": "2024年6月25日", "total": 83900,
        "acceptable_accounts": ["支払利息", "長期借入金"],
        "expected_decision": "propose",
        "human_context": "法人の借入金元利均等返済の内訳。返済額83,900円のうち元金68,760円は負債返済、利息15,140円が費用（支払利息）。口座振替済み。",
        "review_questions": ["元金部分を支払利息と混同していないか（元金は負債返済）", "借入用途（運転資金か設備資金か）"],
        "features": ["A4横", "6列縦罫表", "元利均等返済", "元金/利息分離", "右箱サマリ"],
        "items": [
            {"name": "返済額（第25回）", "gross": 83900, "rate": "exempt"},
            {"name": "うち元金", "gross": 68760, "rate": "exempt"},
            {"name": "うち利息", "gross": 15140, "rate": "exempt"},
        ],
    },
    {
        "case_id": "V057", "title": "収入印紙 購入代金領収証（A5横・切手風券種ブロック）",
        "layout_id": "flashnext-57", "layout_description":
            "A5横。左に領収内容（購入者・券種内訳・現金支払）、右にギザギザのダッシュ枠で切手風の券種ブロックを2列×2行に配置。下部に領収印。",
        "document_kind": "voucher", "merchant": "テスト郵便局（架空）",
        "date": "2024-06-10", "date_printed": "2024年6月10日", "total": 7000,
        "acceptable_accounts": ["租税公課", "印刷費"],
        "expected_decision": "propose",
        "human_context": "経理用に印紙7,000円分（200円券5枚・1,000円券5枚）を現金購入。架空郵便局発行。",
        "review_questions": ["購入目的（受領印紙か契約印紙か）"],
        "features": ["A5横", "切手風ブロック", "ギザギザ枠", "現金支払", "券種別数量"],
        "items": [
            {"name": "200円券×5枚", "gross": 1000, "rate": "exempt"},
            {"name": "1,000円券×5枚", "gross": 5000, "rate": "exempt"},
            {"name": "200円券×5枚（別組）", "gross": 1000, "rate": "exempt"},
        ],
    },
    {
        "case_id": "V058", "title": "自治体証明書手数料 領収書（A5縦・左窓口右金額枠）",
        "layout_id": "flashnext-58", "layout_description":
            "A5縦。外枠の下に市役所見出し。左に窓口番号・受付日・申請者・証明書種別のラベル列、右に金額枠、下部に備考と収納印の角印。",
        "document_kind": "voucher", "merchant": "テスト市役所架空町支所（架空）",
        "date": "2024-07-03", "date_printed": "2024年7月3日", "total": 300,
        "acceptable_accounts": ["租税公課", "事務用品費"],
        "expected_decision": "hold",
        "human_context": "個人の住民票の写し1通分の手数料300円。経費目的（業務上必要か）が領収書からは不明。架空の自治体・手続き。",
        "review_questions": ["業務目的で取得した証明書か（私的な取得でないか）", "法人・個人事業主としての必要経費か"],
        "features": ["A5縦", "窓口形式", "非課税手数料", "少額", "目的不明"],
        "items": [
            {"name": "住民票の写し 1通", "gross": 300, "rate": "exempt"},
        ],
    },
    {
        "case_id": "V059", "title": "所得税 予定納税の納付証明（A4縦・左右2枚組領収証）",
        "layout_id": "flashnext-59", "layout_description":
            "A4縦。太枠の領収証を左右2枚（納税者控え・納付書使用用）に並べ、各々に項目列・納付税額の大字・角印。下に所得税・預定納税の区分注記と架空テスト注記。",
        "document_kind": "voucher", "merchant": "テスト税務署（架空）",
        "date": "2024-07-25", "date_printed": "2024年7月25日", "total": 92400,
        "acceptable_accounts": ["租税公課"],
        "expected_decision": "propose",
        "human_context": "個人事業主の予定納税第1期分92,400円を現金で納付。確定申告で精算。",
        "review_questions": ["事業主個人の税金で、事業の必要経費にならないことを確認", "予定納税であり精算前であること"],
        "features": ["A4縦", "左右2枚組", "予定納税", "所得税", "税務署角印"],
        "items": [
            {"name": "所得税（予定納税第1期）", "gross": 92400, "rate": "exempt"},
        ],
    },
    {
        "case_id": "V060", "title": "自治体の過料 納付通知兼領収（A5横・横罫表＋太枠納付帯）",
        "layout_id": "flashnext-60", "layout_description":
            "A5横。上段は対象者・違反日時・内容・納付期限の横罫のみの表、中段に太枠の納付実績帯（現金納付・領収済）、下段に費用性の注意注記と納付済角印。",
        "document_kind": "other", "merchant": "テスト市架空町 会計課（架空）",
        "date": "2024-05-20", "date_printed": "2024年5月20日", "total": 12000,
        "acceptable_accounts": [],
        "expected_decision": "hold",
        "human_context": "法人に対する行政上の過料（違反金）12,000円を現金納付。法人税申告の必要経費に原則ならないため会計判断が必要。架空の自治体条例。",
        "review_questions": ["過料は損金不算入か（税務署確認）", "会計処理区分（租税公課/雑損失）", "対象者と支払主体が同じか（法人か個人か）"],
        "features": ["A5横", "行政過料", "現金納付済", "横罫のみ表", "太枠納付帯"],
        "items": [
            {"name": "過料（条例違反）", "gross": 12000, "rate": "exempt"},
        ],
    },
]

DRAWERS = {
    "V041": draw_v041, "V042": draw_v042, "V043": draw_v043, "V044": draw_v044,
    "V045": draw_v045, "V046": draw_v046, "V047": draw_v047, "V048": draw_v048,
    "V049": draw_v049, "V050": draw_v050, "V051": draw_v051, "V052": draw_v052,
    "V053": draw_v053, "V054": draw_v054, "V055": draw_v055, "V056": draw_v056,
    "V057": draw_v057, "V058": draw_v058, "V059": draw_v059, "V060": draw_v060,
}

SIZES = {
    "V041": (A4, "a4-portrait-210x297"),
    "V042": ((A5[1], A5[0]), "a5-landscape-210x148"),
    "V043": (A4, "a4-portrait-210x297-dual"),
    "V044": (A4, "a4-portrait-210x297-tree"),
    "V045": ((B5[1], B5[0]), "b5-landscape-257x182"),
    "V046": (A4, "a4-portrait-210x297-tiles"),
    "V047": (A5, "a5-portrait-148x210"),
    "V048": ((A4[1], A4[0]), "a4-landscape-297x210"),
    "V049": (A4, "a4-portrait-210x297-3stage"),
    "V050": ((A5[1], A5[0]), "a5-landscape-210x148-pairs"),
    "V051": (A4, "a4-portrait-210x297-envelope"),
    "V052": (A5, "a5-portrait-148x210-prorate"),
    "V053": (B5, "b5-portrait-182x257"),
    "V054": (A4, "a4-portrait-210x297-grid"),
    "V055": ((B5[1], B5[0]), "b5-landscape-257x182-mono"),
    "V056": ((A4[1], A4[0]), "a4-landscape-297x210-table"),
    "V057": ((A5[1], A5[0]), "a5-landscape-210x148-stamps"),
    "V058": (A5, "a5-portrait-148x210-window"),
    "V059": (A4, "a4-portrait-210x297-two"),
    "V060": ((A5[1], A5[0]), "a5-landscape-210x148-band"),
}


def build_one(case, out_dir):
    cid = case["case_id"]
    size, ratio_name = SIZES[cid]
    W, H = size
    g = LayoutGuard(W, H, cid)
    pdf_path = os.path.join(out_dir, cid + ".pdf")
    c = canvas.Canvas(pdf_path, pagesize=size,
                      pageCompression=1, invariant=1)
    c.setTitle("%s 架空テスト" % cid)
    c.setAuthor("flashnext")
    c.setSubject("fictional Japanese receipt test document")
    DRAWERS[cid](c, W, H, g)
    c.setFillColorRGB(0, 0, 0)
    c.showPage()
    c.save()
    item = dict(case)
    if cid == "V041":
        item.update(document_kind="invoice_unpaid", acceptable_accounts=[], expected_decision="block")
    if cid == "V042":
        item["items"] = [dict(name="基本料金", net=1056, gross=1161, rate="10"), dict(name="従量料金", net=3028, gross=3331, rate="10"), dict(name="メーター使用料", net=150, gross=165, rate="10"), dict(name="割引", net=-200, gross=-220, rate="10")]
        item["tax_allocation_note"] = "Invoice tax403 is allocated to item gross values; first line absorbs rounding difference."
    if cid == "V044":
        item["items"] = [dict(name="回線A 税込料金", gross=7185, rate="10"), dict(name="回線B 税込料金", gross=7185, rate="10"), dict(name="ファミリー割引", gross=-891, rate="10")]
    if cid in ("V042", "V046", "V048", "V049"):
        # Printed line values are net; make manifest gross values genuinely gross.
        net_rows = [v for v in item["items"] if not v["name"].startswith("消費税")]
        if cid != "V042":
            item["items"] = [dict(v, net=v["gross"], gross=round(v["gross"] * 1.1)) for v in net_rows]
    if cid == "V048":
        item.update(expected_decision="hold", acceptable_accounts=[])
    if cid == "V045":
        item.update(date="2024-04-08", date_printed="2024/04/08")
    if cid == "V052":
        item.update(expected_decision="hold", acceptable_accounts=[])
    if cid == "V056":
        item.update(expected_decision="hold", acceptable_accounts=[], date_printed="2024年6月27日")
        item["items"] = [v for v in item["items"] if v["name"] != "返済額（第25回）"]
    if cid == "V059":
        item.update(expected_decision="hold", acceptable_accounts=[])
    if cid == "V055":
        item["human_context"] = item["human_context"].replace("250,720", "250,660")
        item["items"] = [dict(name="振込手数料（税込・うち税60円）", gross=660, rate="10")]
    item["file"] = "individual/%s.pdf" % cid
    item["aspect_ratio"] = ratio_name
    item["features"] = list(case["features"]) + ["font: msgothic/msmincho embedded", "架空テスト"]
    return item


def validate(items):
    ids = [it["case_id"] for it in items]
    assert len(ids) == len(set(ids)) == 20, ids
    assert ids == ["V%03d" % i for i in range(41, 61)], ids
    layouts = [it["layout_id"] for it in items]
    assert len(set(layouts)) == 20, layouts
    for it in items:
        if it["expected_decision"] == "block":
            assert it["total"] is None or it["total"] < 0 or it["document_kind"] in ("invoice_unpaid",)
        if it["total"] is None:
            assert it["expected_decision"] == "hold"
        if "private" in it["human_context"] or it["acceptable_accounts"] is None:
            pass
    kinds = set(it.get("aspect_ratio", "") for it in items)
    assert len(kinds) >= 4, kinds


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--output", required=True)
    args = ap.parse_args()
    out_dir = os.path.abspath(args.output)
    ind_dir = os.path.join(out_dir, "individual")
    os.makedirs(ind_dir, exist_ok=True)
    register_fonts()
    items = []
    for case in CASES:
        items.append(build_one(case, ind_dir))
    validate(items)
    man_path = os.path.join(out_dir, "manifest-fragment.json")
    with open(man_path, "w", encoding="utf-8") as f:
        json.dump(items, f, ensure_ascii=False, indent=2)
    print("generated %d pages -> %s" % (len(items), out_dir))


if __name__ == "__main__":
    main()
