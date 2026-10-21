#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""generate.py -- sonnet author, cases V081-V090 (fictional Japanese edge-case documents).

Usage: python generate.py --output <absolute-dir>
Writes <dir>/individual/V0xx.pdf (one page each) and <dir>/manifest-fragment.json.
"""
import argparse
import json
import os
import random
import sys

from reportlab.pdfgen import canvas
from reportlab.pdfbase import pdfmetrics
from reportlab.pdfbase.ttfonts import TTFont
from reportlab.lib.colors import HexColor, black, white

MM = 72.0 / 25.4
G = "JG"   # MS Gothic
M = "JM"   # MS Mincho
Y = "JY"   # Meiryo (or Yu Gothic fallback)
FONT_DIR = "C:/Windows/Fonts"


def register_fonts():
    cands = {
        G: ["msgothic.ttc"],
        M: ["msmincho.ttc", "YuMincho.ttc"],
        Y: ["meiryo.ttc", "YuGothM.ttc"],
    }
    for name, files in cands.items():
        done = False
        for f in files:
            path = os.path.join(FONT_DIR, f)
            if os.path.exists(path):
                pdfmetrics.registerFont(TTFont(name, path, subfontIndex=0))
                done = True
                break
        if not done:
            raise SystemExit("font not found for " + name + ": " + ", ".join(files))


def fmt(n):
    return "{:,}".format(n)


def wrap(s, f, sz, maxw):
    out, cur = [], ""
    for tok in s.split(" "):
        trial = (cur + " " + tok) if cur else tok
        if pdfmetrics.stringWidth(trial, f, sz) <= maxw:
            cur = trial
        else:
            if cur:
                out.append(cur)
            cur = tok
    if cur:
        out.append(cur)
    return out


class P:
    """Page wrapper: all y coordinates are measured from the TOP of the page."""

    def __init__(self, c, w, h, name):
        self.c, self.w, self.h, self.name = c, w, h, name
        self.boxes = []

    def sw(self, s, f, sz, cs=0):
        return pdfmetrics.stringWidth(s, f, sz) + cs * len(s)

    def t(self, x, y, s, f=G, sz=9, al="l", col=black, bold=False, cs=0, chk=True):
        wd = self.sw(s, f, sz, cs)
        x0 = x if al == "l" else (x - wd if al == "r" else x - wd / 2.0)
        c = self.c
        c.saveState()
        to = c.beginText()
        to.setFont(f, sz)
        to.setFillColor(col)
        to.setTextOrigin(x0, self.h - y)
        if cs:
            to.setCharSpace(cs)
        if bold:
            to.setTextRenderMode(2)
            c.setStrokeColor(col)
            c.setLineWidth(max(0.2, sz * 0.035))
        to.textOut(s)
        c.drawText(to)
        c.restoreState()
        if chk:
            self.boxes.append((x0, y - 0.8 * sz, x0 + wd, y + 0.18 * sz, s))
        return wd

    def rt(self, x, y, s, f=G, sz=8, ang=0, col=black, al="l"):
        c = self.c
        c.saveState()
        c.translate(x, self.h - y)
        c.rotate(ang)
        c.setFillColor(col)
        c.setFont(f, sz)
        if al == "c":
            c.drawCentredString(0, 0, s)
        elif al == "r":
            c.drawRightString(0, 0, s)
        else:
            c.drawString(0, 0, s)
        c.restoreState()

    def ln(self, x1, y1, x2, y2, w=0.5, col=black, dash=None):
        c = self.c
        c.saveState()
        c.setStrokeColor(col)
        c.setLineWidth(w)
        if dash:
            c.setDash(list(dash))
        c.line(x1, self.h - y1, x2, self.h - y2)
        c.restoreState()

    def rc(self, x, y, w, h, lw=0.5, stroke=black, fill=None, dash=None, r=0):
        c = self.c
        c.saveState()
        if fill is not None:
            c.setFillColor(fill)
        if stroke is not None:
            c.setStrokeColor(stroke)
            c.setLineWidth(lw)
        if dash:
            c.setDash(list(dash))
        sf = 1 if fill is not None else 0
        ss = 1 if stroke is not None else 0
        if r:
            c.roundRect(x, self.h - y - h, w, h, r, stroke=ss, fill=sf)
        else:
            c.rect(x, self.h - y - h, w, h, stroke=ss, fill=sf)
        c.restoreState()

    def circ(self, x, y, r, lw=0.8, stroke=black, fill=None):
        c = self.c
        c.saveState()
        if fill is not None:
            c.setFillColor(fill)
        if stroke is not None:
            c.setStrokeColor(stroke)
            c.setLineWidth(lw)
        c.circle(x, self.h - y, r, stroke=1 if stroke is not None else 0,
                 fill=1 if fill is not None else 0)
        c.restoreState()

    def tri(self, pts, fill):
        c = self.c
        c.saveState()
        c.setFillColor(fill)
        path = c.beginPath()
        path.moveTo(pts[0][0], self.h - pts[0][1])
        for (px, py) in pts[1:]:
            path.lineTo(px, self.h - py)
        path.close()
        c.drawPath(path, fill=1, stroke=0)
        c.restoreState()

    def check(self):
        for (a, b, c2, d, s) in self.boxes:
            if a < -0.5 or c2 > self.w + 0.5 or b < -0.5 or d > self.h + 0.5:
                sys.stderr.write("[warn] %s: text outside page: %r\n" % (self.name, s))
        n = len(self.boxes)
        for i in range(n):
            a1, b1, c1, d1, s1 = self.boxes[i]
            for j in range(i + 1, n):
                a2, b2, c2, d2, s2 = self.boxes[j]
                ox = min(c1, c2) - max(a1, a2)
                oy = min(d1, d2) - max(b1, b2)
                if ox > 1.0 and oy > 1.5:
                    sys.stderr.write("[warn] %s: text overlap %r / %r\n" % (self.name, s1, s2))


# ---------------------------------------------------------------------------
# V081  restaurant split bill, 80mm thermal, per-person blocks + service charge
# ---------------------------------------------------------------------------
def v081(p):
    W = p.w
    L, R = 14, W - 14
    p.t(W / 2, 30, "酒肴ごちそう みなと亭", G, 13, "c", bold=True)
    p.t(W / 2, 44, "テスト市架空町1-2-3  TEL 000-0000-0000", G, 6.5, "c")
    p.t(W / 2, 54, "登録番号 T0000000000000", G, 6.5, "c")
    p.ln(L, 62, R, 62, 0.6, dash=(2, 2))
    p.t(L, 75, "2025年11月14日(金)", G, 8)
    p.t(R, 75, "22:41", G, 8, "r")
    p.t(L, 86, "伝票 No.0457   卓 12   3名様", G, 8)
    p.rc(L, 94, R - L, 13, stroke=None, fill=black)
    p.t(W / 2, 104, "ご精算 3名分割 (別会計)", G, 8, "c", col=white)

    people = [
        ("A", [("刺身盛合せ", 1, 1800), ("生ビール", 2, 1200)]),
        ("B", [("焼鳥五種盛り", 1, 1500), ("ハイボール", 1, 600), ("烏龍茶", 1, 400)]),
        ("C", [("天ぷら盛合せ", 1, 1700), ("日本酒 一合", 1, 900)]),
    ]
    y = 124
    items = []
    sub_all = 0
    svc_all = 0
    pay = {}
    for tag, rows in people:
        p.rc(L, y - 9, R - L, 12, stroke=black, fill=HexColor("#dddddd"), lw=0.5)
        p.t(L + 3, y, "お客様 " + tag, G, 8, bold=True)
        p.t(R - 3, y, "個別会計", G, 7, "r")
        y += 12
        sub = 0
        for (nm, q, amt) in rows:
            p.t(L + 2, y, nm, G, 8)
            if q > 1:
                p.t(150, y, "x%d" % q, G, 8, "r")
            p.t(R - 2, y, fmt(amt), G, 8, "r")
            sub += amt
            items.append({"name": "%s(%s)" % (nm, tag) + (" x%d" % q if q > 1 else ""),
                          "gross": amt, "rate": "10"})
            y += 10
        sv = sub // 10
        p.ln(L, y - 6, R, y - 6, 0.4, dash=(1, 2))
        y += 4
        p.t(L + 2, y, "小計", G, 7.5)
        p.t(R - 2, y, fmt(sub), G, 7.5, "r")
        y += 10
        p.t(L + 2, y, "サービス料 10%", G, 7.5)
        p.t(R - 2, y, fmt(sv), G, 7.5, "r")
        y += 11
        p.t(L + 2, y, tag + "様 お会計", G, 8.5, bold=True)
        p.t(R - 2, y, fmt(sub + sv), G, 8.5, "r", bold=True)
        pay[tag] = sub + sv
        sub_all += sub
        svc_all += sv
        y += 16
    items.append({"name": "サービス料 10% (A300/B250/C260)", "gross": svc_all, "rate": "10"})
    total = sub_all + svc_all

    p.ln(L, y - 6, R, y - 6, 1.2)
    p.ln(L, y - 3.5, R, y - 3.5, 0.4)
    y += 8
    p.t(L + 2, y, "小計 (3名分)", G, 7.5)
    p.t(R - 2, y, fmt(sub_all), G, 7.5, "r")
    y += 10
    p.t(L + 2, y, "サービス料 10%", G, 7.5)
    p.t(R - 2, y, fmt(svc_all), G, 7.5, "r")
    y += 8
    p.rc(L, y, R - L, 28, lw=1.4)
    p.t(L + 6, y + 18, "お会計合計", G, 9)
    p.t(R - 6, y + 21, "¥" + fmt(total), G, 17, "r", bold=True)
    y += 41
    tax = total * 10 // 110
    p.t(L, y, "(税込 内消費税10%% %s)" % fmt(tax), G, 6.5)
    y += 15
    p.t(L, y, "【お支払い内訳】", G, 7.5, bold=True)
    cash_due = pay["A"] + pay["C"]
    y += 10
    p.t(L + 2, y, "現金 (A+C分)", G, 7.5)
    p.t(R - 2, y, fmt(cash_due), G, 7.5, "r")
    y += 10
    p.t(L + 2, y, "お預り (現金)", G, 7.5)
    p.t(R - 2, y, "6,200", G, 7.5, "r")
    y += 10
    p.t(L + 2, y, "お釣り", G, 7.5)
    p.t(R - 2, y, fmt(6200 - cash_due), G, 7.5, "r")
    y += 10
    p.t(L + 2, y, "クレジット (B分)", G, 7.5)
    p.t(R - 2, y, fmt(pay["B"]), G, 7.5, "r")
    y += 24
    p.t(L, y, "宛名 上様   但し お飲食代として", G, 7.5)
    y += 12
    p.t(L, y, "担当 07   ご来店ありがとうございました", G, 7)
    p.t(R, p.h - 14, "架空テスト", G, 6, "r")

    return dict(
        title="飲食店 3名分割会計 サービス料10% 使途不明",
        layout_description="80mm thermal tall slip; reverse-video split banner, three per-person blocks each with own subtotal, 10% service charge and personal total, then boxed combined total and split cash/card tenders with change",
        document_kind="thermal_receipt",
        merchant="酒肴ごちそう みなと亭",
        date="2025-11-14", date_printed="2025年11月14日(金) 22:41",
        total=total,
        acceptable_accounts=[],
        expected_decision="hold",
        human_context="Three people shared a late dinner and paid separately by person; the receipt does not say who attended or why (client entertainment, internal meeting, or private). Addressee is 上様. Business purpose and attendee list are missing.",
        review_questions=["参加者と事業上の目的は何か", "会議費・交際費・私的支出のどれか", "自分の負担分はA/B/Cのどれか"],
        features=["80mm thermal", "split bill 3 blocks", "10% service charge", "cash change", "card + cash tender"],
        items=items,
    )


# ---------------------------------------------------------------------------
# V082  horizontal carbon-copy repair slip, handwritten-style entries, red seal
# ---------------------------------------------------------------------------
def v082(p):
    RED = HexColor("#c0262d")
    BLUE = HexColor("#1f3a93")
    CARB = HexColor("#4a3a9a")

    def hand(x, y, s, sz, ink, seed, al="l"):
        rnd = random.Random(seed)
        c = p.c
        ws = [pdfmetrics.stringWidth(ch, M, sz) for ch in s]
        tot = sum(ws)
        if al == "r":
            x -= tot
        elif al == "c":
            x -= tot / 2.0
        for ch, wd in zip(s, ws):
            c.saveState()
            c.translate(x + wd / 2.0, p.h - y + rnd.uniform(-0.8, 0.8))
            c.rotate(rnd.uniform(-4.5, 4.5))
            c.setFillColor(ink)
            c.setFont(M, sz * rnd.uniform(0.95, 1.05))
            c.drawCentredString(0, 0, ch)
            c.restoreState()
            x += wd + rnd.uniform(-0.2, 0.5)

    def panel(ox, label, ink, paper, pr):
        w = 352
        x0 = ox + 8
        hdr = HexColor("#e6e6e6") if paper == white else HexColor("#f3d5da")
        p.rc(ox, 8, w, 352, lw=0.9, stroke=pr, fill=paper)
        p.rc(x0, 16, 58, 14, lw=0.6, stroke=pr)
        p.t(x0 + 29, 26, label, M, 8.5, "c", col=pr)
        p.t(ox + w - 8, 26, "No. 000318", G, 8, "r", col=RED)
        p.t(ox + w / 2, 50, "修理伝票", M, 18, "c", col=pr, bold=True, cs=12)
        p.t(x0, 74, "架空町モータース", M, 12, col=pr, bold=True)
        p.t(x0, 84, "テスト市架空町4-5-6  TEL 000-0000-0000", G, 6.5, col=pr)
        p.t(x0, 93, "登録番号 T0000000000000  自動車整備工場", G, 6.5, col=pr)
        # date (printed labels, handwritten numerals)
        p.t(ox + 196, 74, "令和", M, 9, col=pr)
        hand(ox + 216, 74, "6", 11, ink, 1)
        p.t(ox + 228, 74, "年", M, 9, col=pr)
        hand(ox + 241, 74, "3", 11, ink, 2)
        p.t(ox + 251, 74, "月", M, 9, col=pr)
        hand(ox + 264, 74, "8", 11, ink, 3)
        p.t(ox + 274, 74, "日", M, 9, col=pr)
        # customer / vehicle
        hand(x0 + 6, 114, "宮坂ベーカリー", 11, ink, 11)
        p.ln(x0, 118, x0 + 190, 118, 0.6, col=pr)
        p.t(x0 + 194, 114, "様", M, 10, col=pr)
        p.t(ox + 226, 102, "車名・登録No.", G, 6.5, col=pr)
        hand(ox + 226, 114, "架空バン", 9, ink, 12)
        hand(ox + 226, 125, "テスト300 さ 00-00", 7, ink, 13)
        # amount box
        p.rc(x0, 130, 168, 30, lw=0.9, stroke=pr)
        p.t(x0 + 4, 139, "ご請求金額", G, 6.5, col=pr)
        hand(x0 + 14, 156, "¥15,950-", 17, ink, 14)
        # red seal
        p.circ(ox + 312, 146, 15, lw=1.6, stroke=RED)
        p.rt(ox + 312, 150, "宮田", M, 11, -6, RED, "c")
        # table
        cols = [0, 200, 236, 286, 336]
        p.rc(x0, 168, 336, 14, lw=0.6, stroke=pr, fill=hdr)
        for i, hh in enumerate(["品名・作業内容", "数量", "単価", "金額"]):
            p.t(x0 + (cols[i] + cols[i + 1]) / 2.0, 178, hh, G, 7, "c", col=pr)
        for r in range(6):
            p.rc(x0, 182 + 18 * r, 336, 18, lw=0.4, stroke=pr)
        for cx in cols[1:-1]:
            p.ln(x0 + cx, 168, x0 + cx, 290, 0.4, col=pr)
        rows = [("ブレーキパッド(前)", "1", "8,500", "8,500"),
                ("取付工賃(前輪)", "1", "6,000", "6,000")]
        for i, (a, b, cc, d) in enumerate(rows):
            by = 182 + 18 * i + 13
            hand(x0 + 6, by, a, 10, ink, 20 + i * 5)
            hand(x0 + (cols[1] + cols[2]) / 2.0, by, b, 10, ink, 21 + i * 5, "c")
            hand(x0 + cols[3] - 5, by, cc, 10, ink, 22 + i * 5, "r")
            hand(x0 + cols[4] - 5, by, d, 10, ink, 23 + i * 5, "r")
        hand(x0 + 100, 182 + 36 + 13, "ー 以下余白 ー", 9, ink, 30, "c")
        # summary
        sx = x0 + 200
        for i, (lab, val) in enumerate([("小計", "14,500"), ("消費税(10%)", "1,450"), ("合計", "15,950")]):
            yy = 290 + 16 * i
            p.rc(sx, yy, 136, 16, lw=0.5, stroke=pr, fill=hdr if i == 2 else None)
            p.ln(sx + 56, yy, sx + 56, yy + 16, 0.4, col=pr)
            p.t(sx + 28, yy + 11, lab, G, 7, "c", col=pr)
            hand(sx + 130, yy + 12, val, 10.5, ink, 40 + i, "r")
        p.rc(x0, 290, 196, 48, lw=0.5, stroke=pr)
        p.t(x0 + 4, 299, "備考", G, 6.5, col=pr)
        hand(x0 + 8, 315, "前輪パッド摩耗限度のため交換", 8, ink, 50)
        hand(x0 + 8, 329, "次回 車検時に後輪も点検", 8, ink, 51)
        p.t(x0, 350, "上記の金額、正に領収いたしました。", M, 6.5, col=pr)
        p.t(ox + w - 8, 357, "架空テスト", G, 6, "r", col=pr)

    panel(8, "お客様控", BLUE, white, HexColor("#222222"))
    panel(377, "店 控", CARB, HexColor("#fbe9ec"), HexColor("#6b2840"))
    p.ln(368.5, 0, 368.5, p.h, 0.6, col=HexColor("#888888"), dash=(3, 3))

    return dict(
        title="二連複写 修理伝票 手書き風 朱印",
        layout_description="Horizontal 2-up carbon-copy repair slip (customer copy white, shop copy pink) with perforation; ruled 4-column form filled in jittered handwriting, amount box top-left, red round seal, summary block bottom-right",
        document_kind="invoice_receipt",
        merchant="架空町モータース",
        date="2024-03-08", date_printed="令和6年3月8日",
        total=15950,
        acceptable_accounts=["修繕費", "車両費"],
        expected_decision="propose",
        human_context="Brake pad replacement on the bakery's delivery van, paid in cash at the garage. Customer is the sole proprietor's shop name. The vehicle is used for business deliveries.",
        review_questions=["車両は事業専用か私用兼用か", "修繕費か車両費か"],
        features=["horizontal landscape", "duplicate copy", "handwritten-style", "red seal", "10% tax summary", "parts and labor"],
        items=[{"name": "ブレーキパッド(前) 税抜8,500", "gross": 9350, "rate": "10"},
               {"name": "取付工賃(前輪) 税抜6,000", "gross": 6600, "rate": "10"}],
    )


# ---------------------------------------------------------------------------
# V083  fuel pump machine receipt with separate carwash block
# ---------------------------------------------------------------------------
def v083(p):
    W = p.w
    L, R = 14, W - 14
    p.t(W / 2, 24, "◆ ご利用明細書 ◆", G, 9, "c")
    p.t(W / 2, 40, "架空町セルフSS", G, 11, "c", bold=True)
    p.t(W / 2, 51, "テスト市架空町9-9", G, 6.5, "c")
    p.t(W / 2, 60, "登録番号 T0000000000000", G, 6.5, "c")
    p.ln(L, 67, R, 67, 0.8, dash=(1, 2))
    p.t(L, 80, "2026/02/19", G, 8.5)
    p.t(R, 80, "16:08", G, 8.5, "r")
    p.t(L, 91, "給油機 No.3", G, 7)
    p.t(R, 91, "取引 0215", G, 7, "r")
    p.ln(L, 97, R, 97, 0.8, dash=(1, 2))

    def row(y, lab, val, sz=7.5, bold=False, xl=None):
        xl = L + 22 if xl is None else xl
        xr = R - 6
        p.t(xl, y, lab, G, sz)
        p.t(xr, y, val, G, sz, "r", bold=bold)
        a = xl + p.sw(lab, G, sz) + 3
        b = xr - p.sw(val, G, sz) - 3
        if b - a > 6:
            p.ln(a, y + 1, b, y + 1, 0.5, dash=(0.4, 2.2))

    # fuel block
    p.rc(L, 104, R - L, 64, lw=0.8)
    p.rc(L, 104, 16, 64, stroke=None, fill=black)
    p.t(L + 8, 128, "給", G, 9, "c", col=white)
    p.t(L + 8, 142, "油", G, 9, "c", col=white)
    row(118, "油種", "レギュラー")
    row(130, "単価", "168.0 円/L")
    row(142, "数量", "32.74 L")
    row(158, "給油金額", "¥5,500", 8, True)
    # carwash block
    p.rc(L, 178, R - L, 34, lw=0.8)
    p.rc(L, 178, 16, 34, stroke=None, fill=black)
    p.t(L + 8, 192, "洗", G, 9, "c", col=white)
    p.t(L + 8, 205, "車", G, 9, "c", col=white)
    row(193, "洗車 ノーマル 1回", "¥550", 7.5, True)
    p.t(L + 22, 205, "※給油とは別明細 (税10%)", G, 6)
    # total bar
    p.rc(L, 222, R - L, 28, stroke=None, fill=black)
    p.t(L + 8, 240, "合 計", G, 10, col=white)
    p.t(R - 8, 242, "¥6,050", G, 16, "r", col=white, bold=True)
    row(264, "10%対象 内消費税", "550", 7, xl=L)
    row(278, "現金 お預り", "7,000", 7.5, xl=L)
    row(290, "お釣り", "950", 7.5, True, xl=L)
    p.t(W / 2, 312, "またのご来店をお待ちしております", G, 6.5, "c")
    rnd = random.Random(83)
    x = L + 6
    while x < R - 6:
        wd = rnd.choice([0.6, 1.0, 1.6, 2.2])
        p.ln(x, 322, x, 350, wd)
        x += wd + rnd.choice([1.0, 1.6, 2.4])
    p.t(W / 2, 362, "0215 0219 1608 0003", G, 6.5, "c")
    p.rt(W - 4, 414, "架空テスト", G, 6, 90, black)

    return dict(
        title="セルフ給油機レシート 洗車別明細",
        layout_description="70mm dot-matrix style fuel machine slip: black side-tab framed blocks (給油 / 洗車) with dotted-leader rows, inverted black total bar mid-lower, cash tender with change, barcode, vertical stamp text on right edge",
        document_kind="thermal_receipt",
        merchant="架空町セルフSS",
        date="2026-02-19", date_printed="2026/02/19 16:08",
        total=6050,
        acceptable_accounts=["車両費", "旅費交通費"],
        expected_decision="propose",
        human_context="Refuel and automatic carwash of the business vehicle used for client visits, paid in cash. The carwash is a separate taxable line on the same slip.",
        review_questions=["車両は事業用か", "洗車代も同じ科目でよいか"],
        features=["70mm dot matrix", "fuel quantity and unit price", "separate carwash block", "cash change", "inverted total bar"],
        items=[{"name": "レギュラー 32.74L @168.0", "gross": 5500, "rate": "10"},
               {"name": "洗車 ノーマル", "gross": 550, "rate": "10"}],
    )


# ---------------------------------------------------------------------------
# V084  English-only digital receipt, USD only
# ---------------------------------------------------------------------------
def v084(p):
    W, H = p.w, p.h
    TEAL = HexColor("#0f3d4c")
    GR = HexColor("#5b6670")
    LG = HexColor("#d0d6dc")
    p.rc(0, 0, W, H, stroke=None, fill=HexColor("#eef1f4"))
    p.rc(0, 0, W, 64, stroke=None, fill=TEAL)
    p.circ(34, 32, 14, stroke=None, fill=HexColor("#f08a24"))
    p.t(34, 38, "H", Y, 15, "c", col=white, bold=True)
    p.t(58, 30, "Harborlight Digital Ltd.", Y, 14, col=white, bold=True)
    p.t(58, 46, "Receipt for your purchase", Y, 8.5, col=HexColor("#cfe3ea"))
    p.t(W - 16, 30, "Receipt #HD-2025-004217", Y, 8, "r", col=white)
    p.t(W - 16, 46, "Oct 3, 2025", Y, 8, "r", col=white)
    p.rc(16, 80, 368, 520, lw=0.6, stroke=LG, fill=white)
    p.t(32, 114, "Thank you!", Y, 15, bold=True, col=TEAL)
    p.t(32, 130, "Payment received on Oct 3, 2025", Y, 8.5, col=GR)
    p.t(W - 32, 106, "Amount paid", Y, 7.5, "r", col=GR)
    p.t(W - 32, 134, "USD 129.00", Y, 22, "r", bold=True)
    p.ln(32, 150, W - 32, 150, 0.6, col=LG)
    p.t(32, 168, "BILLED TO", Y, 7, col=GR)
    p.t(32, 182, "Test Customer K.K.", Y, 8.5)
    p.t(32, 194, "Kakuu-cho 1-2-3, Test City", Y, 8.5)
    p.t(32, 206, "Japan", Y, 8.5)
    p.t(210, 168, "SOLD BY", Y, 7, col=GR)
    p.t(210, 182, "Harborlight Digital Ltd.", Y, 8.5)
    p.t(210, 194, "Export sales desk", Y, 8.5)
    p.t(210, 206, "Harbor Road 10, Testville", Y, 8.5)
    p.ln(32, 226, W - 32, 226, 0.6, col=LG)
    p.rc(32, 238, W - 64, 20, stroke=None, fill=HexColor("#f3f5f7"))
    p.t(38, 252, "Description", Y, 7.5, col=GR, bold=True)
    p.t(268, 252, "Qty", Y, 7.5, "c", col=GR, bold=True)
    p.t(W - 38, 252, "Amount (USD)", Y, 7.5, "r", col=GR, bold=True)
    p.t(38, 282, "Sound Library Pro - 12-month license", Y, 8.5)
    p.t(268, 282, "1", Y, 8.5, "c")
    p.t(W - 38, 282, "99.00", Y, 8.5, "r")
    p.ln(32, 292, W - 32, 292, 0.4, col=LG)
    p.t(38, 308, "Premium support add-on", Y, 8.5)
    p.t(268, 308, "1", Y, 8.5, "c")
    p.t(W - 38, 308, "30.00", Y, 8.5, "r")
    p.ln(32, 318, W - 32, 318, 0.4, col=LG)
    p.t(230, 340, "Subtotal", Y, 8.5)
    p.t(W - 38, 340, "129.00", Y, 8.5, "r")
    p.t(230, 356, "Tax - export sale (0%)", Y, 8.5)
    p.t(W - 38, 356, "0.00", Y, 8.5, "r")
    p.ln(226, 366, W - 32, 366, 0.8)
    p.t(230, 386, "Total paid", Y, 10.5, bold=True)
    p.t(W - 38, 386, "USD 129.00", Y, 10.5, "r", bold=True)
    p.t(32, 430, "PAYMENT METHOD", Y, 7, col=GR)
    p.t(32, 444, "Visa ending 0000  -  charged Oct 3, 2025", Y, 8.5)
    y = 478
    note = ("All amounts are shown in US dollars (USD) only. This receipt does not show a yen amount; "
            "the amount posted by your card issuer in your billing currency may differ.")
    for line in wrap(note, Y, 7.5, W - 64):
        p.t(32, y, line, Y, 7.5, col=GR)
        y += 11
    p.t(32, y + 6, "Digital goods delivered by download. Export sale: no Japanese consumption tax was added by the seller.", Y, 7.5, col=GR) if False else None
    for line in wrap("Digital goods delivered by download. Export sale: no Japanese consumption tax was added by the seller.", Y, 7.5, W - 64):
        p.t(32, y + 8, line, Y, 7.5, col=GR)
        y += 11
    p.t(32, 580, "架空テスト  Fictional test receipt", Y, 7, col=GR)
    p.t(W / 2, 622, "This is an automated receipt. Please do not reply.", Y, 7, "c", col=GR)

    return dict(
        title="海外向けデジタル領収書 USDのみ",
        layout_description="Web e-mail style receipt on grey canvas: teal banner with logo, white card, amount-paid headline top-right, two-column billed-to/sold-by, small English line table, wrapped disclaimer; English only, no yen figure anywhere",
        document_kind="invoice_receipt",
        merchant="Harborlight Digital Ltd.",
        date="2025-10-03", date_printed="Oct 3, 2025",
        total=None,
        acceptable_accounts=[],
        expected_decision="hold",
        human_context="Purchase of a software/sound library licence from an overseas seller, paid by card. Only USD 129.00 is printed; the yen amount charged is on the card statement and is not on this document. No exchange rate may be invented from the receipt.",
        review_questions=["カード明細の円建て請求額はいくらか", "事業利用目的か", "ソフトウェア等の資産計上要否"],
        features=["English only", "USD only", "JPY total unknown", "zero-rated export line", "web receipt layout"],
        items=[{"name": "Sound Library Pro 12-month license USD 99.00", "gross": None, "rate": "unknown"},
               {"name": "Premium support add-on USD 30.00", "gross": None, "rate": "unknown"}],
    )


# ---------------------------------------------------------------------------
# V085  prepaid parking ticket, landscape stub ticket
# ---------------------------------------------------------------------------
def v085(p):
    W, H = p.w, p.h
    GR = HexColor("#666666")
    RED = HexColor("#c0262d")
    p.rc(2, 2, W - 4, H - 4, lw=1.2, fill=HexColor("#fdf9ec"), r=6)
    p.ln(92, 4, 92, H - 4, 0.8, col=GR, dash=(3, 3))
    # stub
    p.t(46, 40, "7/22", G, 15, "c", bold=True)
    p.t(46, 54, "TUE", G, 8, "c")
    p.t(46, 84, "駐車券 控", M, 9, "c")
    p.t(46, 97, "No. 0731", G, 8, "c")
    rnd = random.Random(85)
    x = 18
    while x < 74:
        wd = rnd.choice([0.7, 1.2, 1.8])
        p.ln(x, 108, x, 138, wd)
        x += wd + rnd.choice([1.2, 2.0])
    p.t(46, 170, "架空テスト", G, 6.5, "c")
    # center
    p.t(196, 30, "事前精算券", M, 15, "c", bold=True, cs=3)
    p.t(196, 43, "テストパーキング架空町第3", G, 7.5, "c")
    p.t(196, 53, "登録番号 T0000000000000", G, 6.5, "c")
    p.t(104, 72, "2025年7月22日(火)", G, 8.5)
    p.t(104, 96, "課金 3時間19分", G, 7)
    p.t(262, 96, "精算 12:31", G, 7.5, "c")
    p.ln(110, 112, 280, 112, 1.2)
    p.ln(267.3, 112, 280, 112, 3.2, col=RED)
    for xx in (110, 267.3, 280):
        p.circ(xx, 112, 2.6, lw=0.8, fill=black)
    p.t(110, 128, "入庫 09:12", G, 7.5)
    p.t(280, 128, "出庫 12:47", G, 7.5, "c", bold=True)
    p.t(104, 150, "料金 30分 200円 × 7 = 1,400円", G, 7.5)
    p.t(104, 164, "内 消費税10% 127円", G, 7.5)
    # right amount box
    p.rc(306, 34, 108, 60, lw=1.6)
    p.t(360, 48, "駐車料金", G, 8, "c")
    p.t(388, 82, "1,400", G, 26, "r", bold=True)
    p.t(391, 82, "円", G, 10)
    p.t(310, 116, "お預り(現金)", G, 8)
    p.t(410, 116, "2,000円", G, 8.5, "r")
    p.t(310, 131, "お釣り", G, 8, bold=True)
    p.t(410, 131, "600円", G, 8.5, "r", bold=True)
    p.ln(306, 138, 414, 138, 0.6)
    p.t(310, 152, "精算機 No.2", G, 7)
    p.t(410, 152, "精算済", G, 7, "r")

    return dict(
        title="事前精算式 駐車券 出庫時刻・お釣り",
        layout_description="Landscape 150x68mm parking ticket with perforated left stub (date, barcode), centre horizontal timeline of entry/payment/exit with marker dots, framed big fee box at right and cash-in/change lines under it",
        document_kind="voucher",
        merchant="テストパーキング架空町第3",
        date="2025-07-22", date_printed="2025年7月22日(火)",
        total=1400,
        acceptable_accounts=["旅費交通費", "車両費"],
        expected_decision="propose",
        human_context="Paid parking while visiting a client by car; paid in advance at the machine with a 2,000 yen note, change 600 yen. The client name is not on the ticket.",
        review_questions=["訪問先と業務目的の記録があるか", "旅費交通費か車両費か"],
        features=["landscape ticket", "perforated stub", "entry/pay/exit times", "cash change", "10% included tax"],
        items=[{"name": "駐車料金 30分200円×7", "gross": 1400, "rate": "10"}],
    )


# ---------------------------------------------------------------------------
# V086  A4 paid hosting receipt, previous balance separated in side panel
# ---------------------------------------------------------------------------
def v086(p):
    W, H = p.w, p.h
    NAVY = HexColor("#1c3557")
    GRY = HexColor("#7b8594")
    LIGHT = HexColor("#eceff3")
    GREEN = HexColor("#2e7d4f")
    p.rc(0, 0, W, 6, stroke=None, fill=NAVY)
    p.circ(56, 56, 14, stroke=None, fill=NAVY)
    p.t(56, 61, "M", Y, 14, "c", col=white, bold=True)
    p.t(78, 54, "ミナトクラウド株式会社", Y, 13, bold=True, col=NAVY)
    p.t(78, 68, "テスト市架空町5-6-7   登録番号 T0000000000000", Y, 7.5, col=GRY)
    p.t(555, 58, "領収書", Y, 22, "r", col=NAVY, cs=6)
    p.t(555, 72, "RECEIPT", Y, 8, "r", col=GRY)
    p.ln(40, 88, 555, 88, 1.0, col=NAVY)
    # sidebar
    p.t(40, 110, "お客様", Y, 7.5, col=GRY)
    p.t(40, 124, "株式会社テストトレーディング", Y, 9.5)
    p.t(40, 138, "御中", Y, 9.5)
    p.t(40, 160, "アカウントID", Y, 7.5, col=GRY)
    p.t(40, 173, "KT-004217", Y, 9)
    p.t(40, 192, "領収書番号", Y, 7.5, col=GRY)
    p.t(40, 205, "R-2512-004217", Y, 9)
    p.t(40, 224, "発行日", Y, 7.5, col=GRY)
    p.t(40, 237, "2025年12月5日", Y, 9)
    p.rc(40, 262, 165, 150, lw=0.8, stroke=GRY, fill=LIGHT, dash=(3, 2))
    p.t(50, 280, "ご参考: 前回までの残高", Y, 8.5, col=GRY, bold=True)
    p.t(50, 294, "※今回の領収額(右)とは別の", Y, 7, col=GRY)
    p.t(50, 304, "　過去分の残高です", Y, 7, col=GRY)
    p.t(50, 326, "前回請求残高", Y, 8.5, col=GRY)
    p.t(197, 326, "¥5,500", Y, 8.5, "r", col=GRY)
    p.t(50, 342, "11/28 ご入金", Y, 8.5, col=GRY)
    p.t(197, 342, "-¥5,500", Y, 8.5, "r", col=GRY)
    p.ln(50, 350, 197, 350, 0.5, col=GRY)
    p.t(50, 366, "繰越残高", Y, 8.5, col=GRY, bold=True)
    p.t(197, 366, "¥0", Y, 8.5, "r", col=GRY, bold=True)
    p.t(50, 395, "(今回分は右の明細のみ)", Y, 7, col=GRY)
    # main
    p.t(235, 110, "今回のご利用明細", Y, 11, col=NAVY, bold=True)
    p.ln(235, 116, 555, 116, 1.6, col=NAVY)
    p.rc(235, 124, 320, 18, stroke=None, fill=NAVY)
    p.t(241, 136, "ご利用内容", Y, 8, col=white)
    p.t(420, 136, "期間・日付", Y, 8, col=white)
    p.t(549, 136, "金額(税込)", Y, 8, "r", col=white)
    p.t(241, 162, "レンタルサーバー スタンダード", Y, 9)
    p.t(241, 174, "月額プラン / 容量 300GB", Y, 7, col=GRY)
    p.t(420, 162, "2025/12/01-12/31", Y, 8)
    p.t(549, 162, "1,650", Y, 9, "r")
    p.ln(235, 184, 555, 184, 0.4, col=GRY)
    p.t(241, 202, "ドメイン更新  kakuu-test.jp", Y, 9)
    p.t(241, 214, "1年間更新", Y, 7, col=GRY)
    p.t(420, 202, "2025/12/05 更新", Y, 8)
    p.t(549, 202, "2,200", Y, 9, "r")
    p.ln(235, 222, 555, 222, 0.4, col=GRY)
    p.t(420, 244, "小計(税抜)", Y, 8.5)
    p.t(549, 244, "3,500", Y, 8.5, "r")
    p.t(420, 258, "消費税(10%)", Y, 8.5)
    p.t(549, 258, "350", Y, 8.5, "r")
    p.rc(400, 270, 155, 32, stroke=None, fill=NAVY)
    p.t(408, 290, "今回お支払額", Y, 8, col=white)
    p.t(548, 292, "¥3,850", Y, 16, "r", col=white, bold=True)
    p.t(235, 326, "10%対象 3,850 (税込) / 内消費税 350", Y, 7.5, col=GRY)
    p.rc(235, 346, 320, 26, lw=1.0, stroke=GREEN)
    p.t(243, 363, "お支払い済み", Y, 9, col=GREEN, bold=True)
    p.t(549, 363, "クレジットカード(****0000)  2025/12/05", Y, 8, "r")
    p.t(235, 400, "サービス: レンタルサーバー(2025年12月分)・ドメイン更新", Y, 7.5, col=GRY)
    p.t(235, 412, "ご不明点はマイページのサポートからお問い合わせください。", Y, 7.5, col=GRY)
    p.t(555, 818, "架空テスト", Y, 7, "r", col=GRY)

    return dict(
        title="ホスティング利用 領収書 前回残高を別枠表示",
        layout_description="A4 two-column web-invoice receipt: left sidebar with customer info and a dashed grey reference box for previous balance/carry-over, right main column with ruled usage table, tax split and navy total bar, green paid-status frame; lower half deliberately sparse",
        document_kind="invoice_receipt",
        merchant="ミナトクラウド株式会社",
        date="2025-12-05", date_printed="2025年12月5日",
        total=3850,
        acceptable_accounts=["通信費"],
        expected_decision="propose",
        human_context="December hosting fee and a domain renewal for the business website, paid by credit card. The 5,500 yen previous balance in the side box was settled earlier and is not part of this payment.",
        review_questions=["サイトは事業用か", "ドメイン更新を通信費に含めてよいか"],
        features=["A4", "sidebar previous balance", "paid status", "10% tax split", "registration number"],
        items=[{"name": "レンタルサーバー スタンダード 2025年12月分", "gross": 1650, "rate": "10"},
               {"name": "ドメイン更新 kakuu-test.jp", "gross": 2200, "rate": "10"}],
    )


# ---------------------------------------------------------------------------
# V087  square sparse event receipt, month/day only
# ---------------------------------------------------------------------------
def v087(p):
    W, H = p.w, p.h
    GREEN = HexColor("#2f5d50")
    GR = HexColor("#666666")
    p.circ(230, 190, 70, lw=1.0, stroke=HexColor("#d3e1db"))
    p.circ(230, 190, 62, lw=0.4, stroke=HexColor("#d3e1db"))
    p.rc(0, 0, 46, H, stroke=None, fill=GREEN)
    for i, ch in enumerate("領収書"):
        p.t(23, 138 + i * 30, ch, M, 20, "c", col=white, bold=True)
    p.t(23, 312, "No.17", G, 7, "c", col=white)
    p.t(70, 44, "第12回 架空町 異業種交流会", M, 11)
    p.t(70, 60, "懇親会費込み", G, 8, col=GR)
    p.t(322, 22, "架空テスト", G, 6, "r", col=GR)
    p.t(70, 186, "金", M, 12)
    p.t(316, 190, "¥5,000", M, 46, "r", bold=True)
    p.ln(74, 200, 316, 200, 2.0)
    p.t(70, 230, "上記、参加費として領収しました。", M, 9)
    p.t(70, 290, "受付日", G, 6.5, col=GR)
    p.t(70, 306, "11月8日", M, 14)
    p.t(322, 292, "架空町商店会 青年部", M, 9, "r")
    p.t(322, 304, "テスト市架空町2-2", G, 6.5, "r", col=GR)

    return dict(
        title="交流会参加費 領収書 月日のみ(年なし)",
        layout_description="120mm square, very sparse: green vertical band with stacked 領収書, huge centre amount with pale concentric rings behind it, date only as 月日 in lower-left, issuer at bottom-right; no year anywhere",
        document_kind="voucher",
        merchant="架空町商店会 青年部",
        date="", date_printed="11月8日",
        total=5000,
        acceptable_accounts=["諸会費", "交際費"],
        expected_decision="hold",
        human_context="Fee for a local business networking event including a social meal. The receipt shows only month and day, so the tax year cannot be determined. Business purpose and the actual year need confirming.",
        review_questions=["開催年はいつか", "会費か交際費か", "参加の事業上の目的"],
        features=["square sparse", "year missing", "vertical band", "huge centre amount", "handed receipt"],
        items=[{"name": "異業種交流会 参加費(懇親会費込み)", "gross": 5000, "rate": "unknown"}],
    )


# ---------------------------------------------------------------------------
# V088  donation acknowledgment certificate, B5 landscape
# ---------------------------------------------------------------------------
def v088(p):
    W, H = p.w, p.h
    BR = HexColor("#4a3320")
    GOLD = HexColor("#a07c2a")
    RED = HexColor("#c0262d")
    GR = HexColor("#6b6b6b")
    p.rc(14, 14, W - 28, H - 28, lw=2.5, stroke=BR)
    p.rc(22, 22, W - 44, H - 44, lw=0.6, stroke=BR)
    for (cx, cy) in ((30, 30), (W - 30, 30), (30, H - 30), (W - 30, H - 30)):
        p.circ(cx, cy, 4, stroke=None, fill=GOLD)
    p.t(W - 48, 52, "受領番号 第0042号", M, 8, "r", col=GR)
    p.t(W / 2, 110, "寄付金受領証", M, 30, "c", bold=True, col=BR, cs=10)
    p.ln(190, 124, W - 190, 124, 1.2, col=GOLD)
    p.ln(190, 128, W - 190, 128, 0.4, col=GOLD)
    p.t(70, 172, "河合 悠 様", M, 17)
    p.ln(60, 178, 330, 178, 0.6)
    p.rc(150, 205, 429, 70, lw=1.6, stroke=BR)
    p.rc(154, 209, 421, 62, lw=0.4, stroke=BR)
    p.t(162, 226, "ご寄付額", M, 8, col=GR)
    p.t(W / 2, 250, "金 壱万円也", M, 28, "c", bold=True)
    p.t(565, 266, "(¥10,000)", M, 11, "r")
    p.t(70, 316, "上記の金額を、令和7年12月20日、現金にて確かに受領いたしました。", M, 12)
    p.t(70, 340, "ご寄付は、子ども食堂「みらい食堂」の運営に使わせていただきます。", M, 12)
    p.t(70, 364, "※ 本書は寄付金の受領をお知らせするものです。", M, 9, col=GR)
    p.t(70, 410, "令和7年12月20日", M, 13)
    p.t(420, 392, "特定非営利活動法人", M, 10)
    p.t(420, 414, "テスト市みらい食堂", M, 15, bold=True)
    p.t(420, 430, "テスト市架空町3-4-5", G, 8, col=GR)
    p.t(420, 454, "代表理事", M, 10)
    p.rc(570, 424, 38, 44, lw=1.6, stroke=RED)
    p.t(589, 441, "受", M, 14, "c", col=RED)
    p.t(589, 458, "領", M, 14, "c", col=RED)
    p.t(48, H - 34, "架空テスト", G, 6.5, col=GR)

    return dict(
        title="寄付金受領証 現金寄付",
        layout_description="B5 landscape certificate: double brown border with gold corner dots, spaced mincho title, recipient underline, double-framed kanji amount 金壱万円也 at centre, body sentences, issuer block lower-right with red square seal",
        document_kind="other",
        merchant="特定非営利活動法人 テスト市みらい食堂",
        date="2025-12-20", date_printed="令和7年12月20日",
        total=10000,
        acceptable_accounts=["寄付金"],
        expected_decision="hold",
        human_context="Cash donation by an individual to a local children's cafeteria group. Donation is discretionary and not automatically a business expense; whether it is private, business, or deductible in some way is undetermined and the receipt does not say.",
        review_questions=["事業上の寄付か私的な寄付か", "寄付金としての扱いは税理士に確認するか"],
        features=["B5 landscape", "certificate frame", "kanji numeral amount", "red seal", "cash donation"],
        items=[{"name": "寄付金(現金)", "gross": 10000, "rate": "unknown"}],
    )


# ---------------------------------------------------------------------------
# V089  private grocery basket, 58mm dense thermal
# ---------------------------------------------------------------------------
def v089(p):
    W = p.w
    L, R = 8, W - 8
    p.t(W / 2, 20, "スーパーみなと市場", G, 10, "c", bold=True)
    p.t(W / 2, 31, "架空町店", G, 8, "c")
    p.t(W / 2, 41, "TEL 000-0000-0000", G, 6, "c")
    p.t(W / 2, 49, "登録番号 T0000000000000", G, 5.5, "c")
    p.t(L, 61, "2026/03/14(土) 18:25", G, 6.5)
    p.t(R, 61, "レジ03 #0412", G, 6.5, "r")
    p.ln(L, 65, R, 65, 0.5, dash=(1, 1.5))
    goods = [
        ("*食パン 6枚切", 158, 8, None), ("*牛乳 1L", 218, 8, "2コ x 109"),
        ("*卵 10個入", 278, 8, None), ("*鶏もも肉 300g", 548, 8, None),
        ("*キャベツ", 198, 8, None), ("*豆腐 3連", 98, 8, None),
        ("*納豆 3パック", 128, 8, None), ("*チョコレート", 168, 8, None),
        ("ティッシュ 5箱", 458, 10, None), ("食器用洗剤", 238, 10, None),
        ("缶ビール 350ml 6P", 1298, 10, None), ("レジ袋 小", 3, 10, None),
    ]
    y = 76
    sum8 = sum(a for (_, a, r, _) in goods if r == 8)
    sum10 = sum(a for (_, a, r, _) in goods if r == 10)
    items = []
    for (nm, amt, r, sub) in goods:
        p.t(L, y, nm, G, 6.5)
        p.t(R, y, fmt(amt), G, 6.5, "r")
        items.append({"name": nm.lstrip("*"), "gross": amt, "rate": str(r)})
        y += 8.8
        if sub:
            p.t(L + 8, y, sub, G, 5.5)
            y += 7.5
    total = sum8 + sum10
    tax8 = sum8 * 8 // 108
    tax10 = sum10 * 10 // 110
    p.ln(L, y - 4, R, y - 4, 0.5, dash=(1, 1.5))
    y += 6
    p.t(L, y, "小計", G, 7)
    p.t(R, y, fmt(total), G, 7, "r")
    y += 11
    p.t(L, y, "合計", G, 8.5, bold=True)
    p.t(R, y, "¥" + fmt(total), G, 8.5, "r", bold=True)
    y += 10
    p.ln(L, y - 5, R, y - 5, 0.5)
    p.t(L, y + 2, "8%対象(*)", G, 6)
    p.t(R, y + 2, fmt(sum8), G, 6, "r")
    y += 9
    p.t(L, y + 2, "内消費税 8%", G, 6)
    p.t(R, y + 2, fmt(tax8), G, 6, "r")
    y += 9
    p.t(L, y + 2, "10%対象", G, 6)
    p.t(R, y + 2, fmt(sum10), G, 6, "r")
    y += 9
    p.t(L, y + 2, "内消費税 10%", G, 6)
    p.t(R, y + 2, fmt(tax10), G, 6, "r")
    y += 14
    p.t(L, y, "お預り(現金)", G, 6.5)
    p.t(R, y, "4,000", G, 6.5, "r")
    y += 9
    p.t(L, y, "お釣り", G, 6.5)
    p.t(R, y, fmt(4000 - total), G, 6.5, "r")
    y += 13
    p.t(L, y, "今回ポイント 3P", G, 6)
    y += 8
    p.t(L, y, "* は軽減税率(8%)対象商品", G, 5.5)
    y += 14
    rnd = random.Random(89)
    x = L + 4
    while x < R - 4:
        wd = rnd.choice([0.5, 0.9, 1.4])
        p.ln(x, y, x, y + 26, wd)
        x += wd + rnd.choice([0.8, 1.3, 1.9])
    p.t(W / 2, y + 36, "2603 1418 2503 0412", G, 5.5, "c")
    p.t(W / 2, y + 50, "ありがとうございました", G, 6, "c")
    p.t(R, p.h - 12, "架空テスト", G, 5.5, "r")

    return dict(
        title="スーパー買物 軽減税率混在 自宅用",
        layout_description="58mm extra-narrow dense thermal strip, 6.5pt item rows with asterisk reduced-rate marks and sub-quantity line, total only as a modest bold row, two-rate included-tax breakdown, cash change, points and barcode",
        document_kind="thermal_receipt",
        merchant="スーパーみなと市場 架空町店",
        date="2026-03-14", date_printed="2026/03/14(土) 18:25",
        total=total,
        acceptable_accounts=[],
        expected_decision="hold",
        human_context="Weekend grocery shopping for the household: food, tissues, detergent and beer for home use. It is private living expense with no business use stated; nothing here links it to the business.",
        review_questions=["事業用の購入品が含まれるか", "私的支出として除外するか"],
        features=["58mm narrow", "dense small type", "two tax rates", "reduced-rate asterisk", "cash change", "points"],
        items=items,
    )


# ---------------------------------------------------------------------------
# V090  paid mini invoice: gross fee, withholding, actual transfer
# ---------------------------------------------------------------------------
def v090(p):
    W, H = p.w, p.h
    RED = HexColor("#c0262d")
    INK = HexColor("#222222")
    GR = HexColor("#666666")
    p.t(24, 44, "請求書", M, 22, col=INK, bold=True, cs=6)
    p.t(W - 24, 28, "No. T-2601-03", G, 7.5, "r")
    p.t(W - 24, 40, "請求日 令和8年1月20日", G, 8, "r")
    p.ln(24, 56, W - 24, 56, 1.6)
    p.t(24, 80, "株式会社テストトレーディング 御中", G, 10)
    p.ln(24, 84, 200, 84, 0.5)
    p.t(W - 24, 80, "言葉舎 水野", G, 10.5, "r", bold=True)
    p.t(W - 24, 92, "テスト市架空町7-8", G, 7, "r")
    p.t(W - 24, 102, "登録番号 T0000000000000", G, 6.5, "r")
    p.t(24, 128, "件名", G, 7, col=GR)
    p.t(52, 128, "英日翻訳業務(取扱説明書 24頁)", G, 9.5)
    p.t(24, 143, "お支払日", G, 7, col=GR)
    p.t(64, 143, "令和8年1月31日 (銀行振込)", G, 8.5)
    # flow
    p.t(30, 174, "翻訳料(税抜)  英日 24頁", G, 9)
    p.t(308, 174, "100,000", G, 10, "r")
    p.ln(24, 180, 316, 180, 0.4, col=GR)
    p.t(30, 194, "消費税 10%", G, 9)
    p.t(308, 194, "10,000", G, 10, "r")
    p.rc(24, 204, 292, 28, lw=1.0, stroke=INK, fill=HexColor("#fff4c2"))
    p.t(30, 222, "(1) 報酬総額(税込)", G, 10, bold=True)
    p.t(308, 224, "110,000", G, 15, "r", bold=True)
    p.tri([(162, 238), (178, 238), (170, 247)], GR)
    p.t(30, 264, "(2) 源泉所得税を差引", G, 9.5)
    p.t(30, 275, "税抜報酬100,000 × 10.21%", G, 6.5, col=GR)
    p.t(308, 266, "-10,210", G, 12, "r", col=RED)
    p.ln(24, 282, 316, 282, 0.4, col=GR)
    p.tri([(162, 287), (178, 287), (170, 296)], GR)
    p.rc(24, 302, 292, 36, lw=2.0, stroke=INK)
    p.rc(27, 305, 286, 30, lw=0.5, stroke=INK)
    p.t(34, 324, "(3) 実振込額", G, 10, bold=True)
    p.t(306, 327, "99,790", G, 18, "r", bold=True)
    p.t(24, 358, "振込先: テスト銀行 架空支店 普通 0000000", G, 7.5)
    p.t(24, 374, "※ 源泉所得税は税抜報酬から差し引き、", G, 6.5, col=GR)
    p.t(24, 384, "　(3)の実振込額を令和8年1月31日にお振込みいただきました。", G, 6.5, col=GR)
    c = p.c
    c.saveState()
    c.translate(250, H - 432)
    c.rotate(8)
    c.setStrokeColor(RED)
    c.setFillColor(RED)
    c.setLineWidth(2.2)
    c.rect(-44, -20, 88, 40, stroke=1, fill=0)
    c.setFont(M, 20)
    c.drawCentredString(0, 0, "支払済")
    c.setFont(G, 7)
    c.drawCentredString(0, -12, "R8.1.31")
    c.restoreState()
    p.t(24, H - 16, "架空テスト", G, 6.5, col=GR)

    return dict(
        title="翻訳報酬 請求書(支払済) 源泉徴収あり",
        layout_description="A6-ish portrait mini invoice laid out as a vertical calculation flow: net fee and tax lines, highlighted gross-fee band, down-arrow triangles to the withholding deduction in red, then a double-framed actual-transfer box; rotated red 支払済 stamp bottom-right",
        document_kind="invoice_receipt",
        merchant="言葉舎 水野",
        date="2026-01-31", date_printed="令和8年1月31日",
        total=99790,
        gross_fee=110000, withholding=10210,
        acceptable_accounts=["外注費"],
        expected_decision="hold",
        human_context="Freelance translator's invoice for a product manual, already paid by bank transfer. The fee shown is the gross 110,000 yen; 10,210 yen was withheld and 99,790 yen actually transferred. Whether withholding was required and how to book the gross vs net amounts needs review.",
        review_questions=["源泉徴収の要否と税額は正しいか", "外注費は総額110,000円で計上するか", "支払調書・納付の対応"],
        features=["A6-ish portrait", "calculation flow", "gross fee", "withholding line", "actual transfer", "paid stamp", "issue vs payment date"],
        items=[{"name": "英日翻訳料（税込）", "gross": 110000, "rate": "10"}, {"name": "源泉徴収控除（別途仕訳確認）", "gross": -10210, "rate": "unknown"}],
    )


CASES = [
    ("V081", "sonnet-01", v081, 80 * MM, 200 * MM),
    ("V082", "sonnet-02", v082, 737.0, 368.0),
    ("V083", "sonnet-03", v083, 70 * MM, 150 * MM),
    ("V084", "sonnet-04", v084, 400.0, 640.0),
    ("V085", "sonnet-05", v085, 150 * MM, 68 * MM),
    ("V086", "sonnet-06", v086, 595.0, 842.0),
    ("V087", "sonnet-07", v087, 340.0, 340.0),
    ("V088", "sonnet-08", v088, 729.0, 516.0),
    ("V089", "sonnet-09", v089, 58 * MM, 540.0),
    ("V090", "sonnet-10", v090, 340.0, 482.0),
]


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--output", required=True)
    a = ap.parse_args()
    out = os.path.abspath(a.output)
    ind = os.path.join(out, "individual")
    os.makedirs(ind, exist_ok=True)
    register_fonts()
    manifest = []
    for cid, lid, fn, w, h in CASES:
        path = os.path.join(ind, cid + ".pdf")
        c = canvas.Canvas(path, pagesize=(w, h), invariant=1)
        c.setTitle("架空テスト書類")
        c.setAuthor("")
        p = P(c, w, h, cid)
        meta = fn(p)
        p.check()
        c.showPage()
        c.save()
        m = {"case_id": cid, "file": "individual/" + cid + ".pdf", "layout_id": lid}
        m.update(meta)
        manifest.append(m)
    with open(os.path.join(out, "manifest-fragment.json"), "w", encoding="utf-8") as f:
        json.dump(manifest, f, ensure_ascii=False, indent=2)
    print("wrote %d pdfs to %s" % (len(manifest), ind))


if __name__ == "__main__":
    main()
