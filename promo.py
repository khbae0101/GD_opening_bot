"""
프로모션 공용 모듈 — 10월 1순기 후불 활성화 프로모션
─────────────────────────────────────────────
- 기간: 10/2 ~ 10/10 (누적)  ·  Phase1 10/2~10/6  ·  Phase2 10/7~10/10
- 집계: 실적공유방 원본(data/raw/날짜.txt)에서 매장별 개통 건수를 규칙으로 센다.
        무실적 점검(14·16·18시)과 같은 기준이라 두 공지의 숫자가 어긋나지 않는다.
- 공지: 아침(날씨봇) · 14/16/18시(중간봇) · 마감(시상봇) — 기간 밖이면 아무것도 안 함.
- 날씨봇·중간봇·시상봇은 이 파일의 is_promo_day / post_morning / post_status 만 부른다.
"""

import os
import re
import glob
import time
from datetime import date, timedelta

import requests

# ── 설정 ──────────────────────────────────────────
PROMO_ON   = True
PROMO_NAME = "10월 1순기 후불 활성화 프로모션"
PERIOD     = ("2026-10-02", "2026-10-10")                  # 누적 기간
PHASES     = [("Phase1", "2026-10-02", "2026-10-06"),
              ("Phase2", "2026-10-07", "2026-10-10")]

PHASE_TARGETS = {
    "Phase1": {
        "자양번영로": 8, "금호동": 8, "건대입구역": 8, "외대역": 8, "면목역": 8,
        "구리리맥스": 10, "상봉역": 8, "도농로": 9, "다산신도시": 8, "진접": 8,
        "지행역": 7, "의정부로데오": 9, "옥정신도시": 9, "삼양로": 8, "수유": 14,
        "중계아울렛": 11, "상계역": 8, "먹골역": 8, "양주덕계": 6,
        "원주무실": 9, "단구": 9, "강릉임당": 9, "동해천곡": 10, "강릉유천": 8,
        "석사": 11, "홍천중앙": 8, "후평": 9, "온의": 7,
    },
}
PHASE_TARGETS["Phase2"] = dict(PHASE_TARGETS["Phase1"])      # Phase2 목표는 Phase1과 동일

DISCLAIMER = ("※ 이 장표는 실적공유방 공유 실적 기준으로 실제와 다를 수 있습니다. "
              "최종 마감은 전산 데이터로 추출하여 별도 공지합니다.")

AREAS = {
    "광진/구리": ["자양번영로", "금호동", "건대입구역", "외대역", "면목역",
                 "구리리맥스", "상봉역", "도농로", "다산신도시", "진접"],
    "경기북부": ["지행역", "의정부로데오", "옥정신도시", "삼양로", "수유",
                "중계아울렛", "상계역", "먹골역", "양주덕계"],
    "강원": ["원주무실", "단구", "강릉임당", "동해천곡", "강릉유천",
            "석사", "홍천중앙", "후평", "온의"],
}
AREA_SHORT = {"광진/구리": "광구", "경기북부": "경북", "강원": "강원"}
STORE_SHORT = {"의정부로데오": "의정부"}
STORES = [s for ss in AREAS.values() for s in ss]

# 매장 인식 별칭 — 중간봇(무실적 점검)과 동일. 모호한 "강릉", "양주"는 제외
ALIASES = {
    "도농로": ["도농로", "도농"], "구리리맥스": ["구리리맥스", "구리"],
    "자양번영로": ["자양번영로", "자양"], "다산신도시": ["다산신도시", "다산"],
    "건대입구역": ["건대입구역", "건대입구", "건대"], "면목역": ["면목역", "면목"],
    "상봉역": ["상봉역", "상봉"], "외대역": ["외대역", "외대"],
    "금호동": ["금호동", "금호"], "진접": ["진접"],
    "중계아울렛": ["중계아울렛", "중계"], "수유": ["수유"],
    "의정부로데오": ["의정부로데오", "의정부", "의로"], "옥정신도시": ["옥정신도시", "옥정"],
    "삼양로": ["삼양로", "삼양"], "먹골역": ["먹골역", "먹골"],
    "지행역": ["지행역", "지행"], "상계역": ["상계역", "상계"],
    "양주덕계": ["양주덕계", "덕계"], "동해천곡": ["동해천곡", "동해"],
    "석사": ["석사"], "강릉임당": ["강릉임당", "임당"],
    "원주무실": ["원주무실", "무실"], "단구": ["단구"],
    "강릉유천": ["강릉유천", "유천"], "홍천중앙": ["홍천중앙", "홍천"],
    "후평": ["후평"], "온의": ["온의"],
}
_ALIAS_LIST = sorted(((k, s) for s, ks in ALIASES.items() for k in ks), key=lambda x: -len(x[0]))

RAW_DIR = "data/raw"
SEP = "\n<<<MSG>>>\n"

# 무실적 점검과 같은 판정 기준
REPORT_HINTS = ("기변", "신규", "번이", "MNP", "mnp", "요할", "심플", "단할", "공시")
SKIP_PAT = re.compile(
    r"(2nd|2ND|세컨|순신규|약갱|약정갱신|에센스|베이직\s*\+|모든\s*지|모든\s*G|"
    r"GTT|MIT|ITT|신동|원스톱|인터넷|^Pre$)", re.I)
RESV_PAT = re.compile(r"(예판|사전예약|사전\s*예약|락인)")     # 예약 건은 개통 아님


# ── 날짜 도우미 ──────────────────────────────────
def _d(s):
    return date.fromisoformat(s)


def _md(s):
    d = _d(s)
    return f"{d.month}/{d.day}({'월화수목금토일'[d.weekday()]})"


def _days(a, b):
    """a~b(포함) 날짜 문자열 목록."""
    out, d = [], _d(a)
    while d <= _d(b):
        out.append(d.isoformat())
        d += timedelta(days=1)
    return out


def _workdays(a, b):
    return sum(1 for s in _days(a, b) if _d(s).weekday() != 6)


def is_promo_day(date_str):
    return PROMO_ON and PERIOD[0] <= date_str <= PERIOD[1]


def phase_of(date_str):
    for name, a, b in PHASES:
        if a <= date_str <= b:
            return name, a, b
    return None


# ── 집계 (무실적 점검과 동일 기준) ───────────────
def _load_raw(date_str):
    try:
        with open(f"{RAW_DIR}/{date_str}.txt", encoding="utf-8") as f:
            txt = f.read()
    except FileNotFoundError:
        return []
    return [m.strip() for m in txt.split(SEP) if m.strip()]


def _looks_like_report(t):
    if sum(1 for k in REPORT_HINTS if k in t) >= 2:
        return True
    for line in t.splitlines():
        line = line.strip()
        if line.count("/") < 2 or line.endswith(":") or "직영점" in line:
            continue
        if any(k in line for k in REPORT_HINTS):
            return True
    return False


def _is_sale_line(line):
    if "/" not in line or line.endswith(":") or "직영점" in line:
        return False
    model = line.split("/")[0].strip()
    if not model or len(model) > 15 or model.count(" ") >= 2:
        return False
    if SKIP_PAT.search(line) or SKIP_PAT.search(model) or RESV_PAT.search(line):
        return False
    return True


def _store_of(line):
    for k, s in _ALIAS_LIST:
        if line.startswith(k):
            return s
    return None


def count_sales(dates):
    """주어진 날짜들의 매장별 개통 건수."""
    counts = {s: 0 for s in STORES}
    for dt in dates:
        for msg in _load_raw(dt):
            if not _looks_like_report(msg):
                continue
            cur = None
            for raw in msg.splitlines():
                line = raw.strip()
                if not line:
                    continue
                if "/" not in line:                 # 점명·이름 줄
                    s = _store_of(line)
                    if s:
                        cur = s
                    continue
                if cur and _is_sale_line(line):
                    counts[cur] += 1
    return counts


def _ranks(act, tgt):
    """달성률 순(동률 시 실적 건수). 같은 값은 공동 순위, 다음 순위는 건너뜀."""
    keys = sorted(tgt, key=lambda s: (-act[s] / tgt[s], -act[s]))
    r, prev, n = {}, None, 0
    for i, s in enumerate(keys):
        k = (round(act[s] / tgt[s], 6), act[s])
        if k != prev:
            n, prev = i + 1, k
        r[s] = n
    return r


def snapshot(upto, phase_name):
    """upto까지(포함) Phase·누적 집계. upto가 기간 시작 전이면 0."""
    pname, pa_, pb_ = next(p for p in PHASES if p[0] == phase_name)
    pdates = _days(pa_, min(pb_, upto)) if upto >= pa_ else []
    cdates = _days(PERIOD[0], min(PERIOD[1], upto)) if upto >= PERIOD[0] else []
    pt = PHASE_TARGETS[pname]
    ct = {s: sum(PHASE_TARGETS[n][s] for n, _, _ in PHASES) for s in STORES}
    return {"phase": pname, "prange": (pa_, pb_), "pa": count_sales(pdates), "pt": pt,
            "ca": count_sales(cdates), "ct": ct}


# ── 표 이미지 ────────────────────────────────────
def _font(bold, size):
    from PIL import ImageFont
    pats = (["/usr/share/fonts/opentype/noto/NotoSansCJK-Bold.ttc",
             "/usr/share/fonts/**/NotoSansCJK*Bold*"] if bold else
            ["/usr/share/fonts/opentype/noto/NotoSansCJK-Regular.ttc",
             "/usr/share/fonts/**/NotoSansCJK*Regular*"])
    for p in pats:
        hits = glob.glob(p, recursive=True)
        if hits:
            return ImageFont.truetype(hits[0], size)
    return ImageFont.load_default()


def render(snap, subtitle, path="promo.png"):
    from PIL import Image, ImageDraw
    F = _font
    NAVY = (16, 42, 84); TEAL = (0, 150, 160); RED = (214, 69, 65); GRAY = (120, 130, 140)
    DARK = (35, 45, 60); GREEN = (0, 128, 96); LINE = (226, 230, 236); ALT = (248, 250, 252)
    AREABG = (233, 238, 246)
    MEDAL = [(212, 160, 23), (150, 160, 172), (192, 122, 58)]
    pa, pt, ca, ct = snap["pa"], snap["pt"], snap["ca"], snap["ct"]
    pr, cr = _ranks(pa, pt), _ranks(ca, ct)

    W, M, ROW, SR = 1240, 28, 44, 48
    C = [M, M + 70, M + 228]
    x = C[-1]
    for _ in range(2):
        for w in (90, 70, 200, 76):
            x += w
            C.append(x)
    H = 128 + 40 + 44 + SR * 4 + 10 + ROW * len(STORES) + 112
    img = Image.new("RGB", (W, H), "white")
    d = ImageDraw.Draw(img)
    d.text((M, 26), PROMO_NAME, font=F(True, 42), fill=NAVY)
    d.text((M, 84), subtitle, font=F(False, 25), fill=GRAY)

    y = 128
    hdr_top = y
    pa_, pb_ = snap["prange"]
    d.rectangle([C[0], y, C[2], y + 40], fill=NAVY)
    d.rectangle([C[2], y, C[6], y + 40], fill=(47, 84, 150))
    d.rectangle([C[6], y, C[10], y + 40], fill=(176, 110, 10))
    d.text(((C[2] + C[6]) / 2, y + 20), f"{snap['phase']} ({_md(pa_)[:-3]}~{_md(pb_)[:-3]})",
           font=F(True, 22), fill="white", anchor="mm")
    d.text(((C[6] + C[10]) / 2, y + 20), f"누적 ({_md(PERIOD[0])[:-3]}~{_md(PERIOD[1])[:-3]})",
           font=F(True, 22), fill="white", anchor="mm")
    y += 40
    d.rectangle([C[0], y, C[10], y + 44], fill=NAVY)
    for i, lab in enumerate(["상권", "매장", "목표", "실적", "달성률", "순위", "목표", "실적", "달성률", "순위"]):
        d.text(((C[i] + C[i + 1]) / 2, y + 22), lab, font=F(True, 22), fill="white", anchor="mm")
    y += 44

    def bar(gx, a, t, cy, h=7, track=(235, 238, 243)):
        rate = a / t * 100 if t else 0
        col = GREEN if rate >= 100 else (DARK if rate >= 50 else RED)
        bx0, bx1 = C[gx + 2] + 10, C[gx + 3] - 80
        d.rounded_rectangle([bx0, cy - h, bx1, cy + h], radius=h, fill=track)
        d.rounded_rectangle([bx0, cy - h, bx0 + max(5, int((bx1 - bx0) * min(rate, 100) / 100)), cy + h],
                            radius=h, fill=col)
        d.text((C[gx + 3] - 8, cy), f"{rate:.0f}%", font=F(True, 20), fill=col, anchor="rm")

    # 요약 4줄 (지사 전체 + 상권)
    asum = {a: (sum(pa[s] for s in ss), sum(pt[s] for s in ss),
                sum(ca[s] for s in ss), sum(ct[s] for s in ss)) for a, ss in AREAS.items()}

    def arank(i):
        if sum(asum[a][i] for a in asum) == 0:
            return {a: "-" for a in asum}
        order = sorted(asum, key=lambda a: -(asum[a][i] / asum[a][i + 1]))
        return {a: f"{order.index(a) + 1}위" for a in asum}
    rp, rc = arank(0), arank(2)
    rows = [("지사 전체", sum(pa.values()), sum(pt.values()), sum(ca.values()), sum(ct.values()), "-", "-",
             (214, 226, 245))]
    rows += [(a, *asum[a], rp[a], rc[a], (240, 244, 250)) for a in AREAS]
    for label, a1, t1, a2, t2, r1, r2, bg in rows:
        d.rectangle([C[0], y, C[10], y + SR], fill=bg)
        cy = y + SR / 2
        d.text(((C[0] + C[2]) / 2, cy), label, font=F(True, 23), fill=NAVY, anchor="mm")
        for gx, a, t, r in ((2, a1, t1, r1), (6, a2, t2, r2)):
            d.text(((C[gx] + C[gx + 1]) / 2, cy), str(t), font=F(True, 22), fill=DARK, anchor="mm")
            d.text(((C[gx + 1] + C[gx + 2]) / 2, cy), str(a), font=F(True, 22), fill=TEAL, anchor="mm")
            bar(gx, a, t, cy, 8, "white")
            d.text(((C[gx + 3] + C[gx + 4]) / 2, cy), r, font=F(True, 21), fill=NAVY, anchor="mm")
        d.line([C[0], y + SR, C[10], y + SR], fill=(210, 216, 226))
        y += SR
    d.line([C[0], y, C[10], y], fill=NAVY, width=3)
    y += 10
    top, ri = y, 0

    def rank_cell(gx, r, cy, a):
        cx = (C[gx + 3] + C[gx + 4]) / 2
        if a == 0:
            d.text((cx, cy), "-", font=F(False, 22), fill=(170, 176, 186), anchor="mm")
        elif r <= 3:
            d.ellipse([cx - 17, cy - 17, cx + 17, cy + 17], fill=MEDAL[r - 1])
            d.text((cx, cy), str(r), font=F(True, 21), fill="white", anchor="mm")
        else:
            d.text((cx, cy), str(r), font=F(False, 22), fill=DARK, anchor="mm")

    for area, ss in AREAS.items():
        y0 = y
        for s in ss:
            if ri % 2:
                d.rectangle([C[1], y, C[10], y + ROW], fill=ALT)
            cy = y + ROW / 2
            d.text((C[1] + 12, cy), STORE_SHORT.get(s, s), font=F(True, 22), fill=DARK, anchor="lm")
            for gx, a, t, r in ((2, pa[s], pt[s], pr[s]), (6, ca[s], ct[s], cr[s])):
                d.text(((C[gx] + C[gx + 1]) / 2, cy), str(t), font=F(False, 22), fill=DARK, anchor="mm")
                d.text(((C[gx + 1] + C[gx + 2]) / 2, cy), str(a), font=F(True, 22), fill=TEAL, anchor="mm")
                bar(gx, a, t, cy)
                rank_cell(gx, r, cy, a)
            d.line([C[1], y + ROW, C[10], y + ROW], fill=LINE)
            y += ROW
            ri += 1
        at = sum(pt[s] for s in ss)
        aa = sum(pa[s] for s in ss)
        d.rectangle([C[0], y0, C[1], y], fill=AREABG)
        d.text(((C[0] + C[1]) / 2, (y0 + y) / 2 - 12), AREA_SHORT[area], font=F(True, 25), fill=NAVY, anchor="mm")
        d.text(((C[0] + C[1]) / 2, (y0 + y) / 2 + 16), f"{aa / at * 100:.0f}%", font=F(True, 19),
               fill=TEAL, anchor="mm")
        d.line([C[0], y, C[10], y], fill=NAVY, width=3)
    for xx in C[1:-1]:
        d.line([xx, top, xx, y], fill=LINE)
    d.line([C[6], hdr_top, C[6], y], fill=NAVY, width=3)

    d.text((M, y + 14), "매장 순위: 28개 매장 달성률 순(동률 시 실적 건수, 실적 0건은 '-') · 상권 순위: 3개 상권 달성률 순",
           font=F(False, 20), fill=GRAY)
    d.text((M, y + 40), "달성률 색상: 초록 100%↑ · 검정 50%↑ · 빨강 50% 미만", font=F(False, 20), fill=GRAY)
    d.rounded_rectangle([M, y + 70, W - M, y + 104], radius=8, fill=(254, 242, 242))
    d.text((M + 14, y + 87), DISCLAIMER, font=F(True, 19), fill=RED, anchor="lm")
    img.save(path)
    return path


# ── 전송 ─────────────────────────────────────────
def send_photo(tg_base, chat_id, path, caption, retries=3):
    for i in range(1, retries + 1):
        try:
            with open(path, "rb") as f:
                r = requests.post(f"{tg_base}/sendPhoto",
                                  data={"chat_id": chat_id, "caption": caption},
                                  files={"photo": f}, timeout=(10, 120))
            j = r.json()
            if j.get("ok"):
                print("[프로모션] 표 게시 완료" + (f" (재시도 {i}회차)" if i > 1 else ""))
                return True
            print(f"[프로모션] 표 게시 거부: {j.get('error_code')} {j.get('description')!r}")
            if j.get("error_code") not in (500, 502, 503, 504, 429):
                return False
        except Exception as exc:
            print(f"[프로모션] 표 전송 예외({i}/{retries}): {exc!r}")
        if i < retries:
            time.sleep(10 * i)
    return False


def _pct(a, t):
    return f"{a}/{t}건 ({a / t * 100:.0f}%)" if t else f"{a}건"


def _caption(head, snap):
    pa, pt, ca, ct = (sum(snap[k].values()) for k in ("pa", "pt", "ca", "ct"))
    return (f"{head}\n{snap['phase']} {_pct(pa, pt)} · 누적 {_pct(ca, ct)}\n"
            f"※ 실적공유방 기준 · 최종 마감은 전산 데이터로 별도 공지")


def post_status(tg_base, chat_id, date_str, when):
    """중간점검(14·16·18시)·마감: 오늘까지 집계 → 표 → 전송."""
    if not is_promo_day(date_str):
        return
    try:
        ph = phase_of(date_str)
        if not ph:
            return
        name, pa_, pb_ = ph
        snap = snapshot(date_str, name)
        n = _workdays(pa_, date_str)
        if when == "마감" and date_str == PERIOD[1]:
            sub, head = f"{_md(date_str)} 마감 · 최종 결과 (실적공유방 기준)", \
                f"🏆 {PROMO_NAME} 최종 결과"
        elif when == "마감" and date_str == pb_:
            sub, head = f"{_md(date_str)} 마감 · {name} 최종 결과", f"🏁 {name} 최종 결과"
        else:
            sub, head = f"{_md(date_str)} {when} · {name} {n}일차", f"🎯 {name} {n}일차 {when} 현황"
        path = render(snap, sub)
        send_photo(tg_base, chat_id, path, _caption(head, snap))
    except Exception as exc:
        import traceback
        print(f"[프로모션] 처리 실패: {exc!r}")
        traceback.print_exc()


def post_morning(tg_base, chat_id, date_str, send_text):
    """아침: 어제까지 누적 기준 표. Phase 첫날은 시작 안내 문구를 붙인다."""
    if not is_promo_day(date_str):
        return
    try:
        ph = phase_of(date_str)
        if not ph:
            return
        name, pa_, pb_ = ph
        prev = (_d(date_str) - timedelta(days=1)).isoformat()
        snap = snapshot(prev, name)
        if date_str == PERIOD[0]:
            sub = f"{_md(date_str)} 시작 · {name} ({_md(pa_)}~{_md(pb_)})"
            head = f"🚀 {PROMO_NAME} 시작! 매장별 목표를 확인해주세요"
        elif date_str == pa_:
            sub = f"{_md(date_str)} {name} 시작 · 누적은 어제까지 기준"
            head = f"🚀 {name} 시작! ({_md(pa_)}~{_md(pb_)}) — 누적 순위도 이어집니다"
        else:
            sub = f"{_md(date_str)} 아침 · 어제까지 기준"
            head = f"☀️ {name} {_workdays(pa_, date_str)}일차 아침 — 어제까지 현황"
        path = render(snap, sub)
        send_photo(tg_base, chat_id, path, _caption(head, snap))
    except Exception as exc:
        import traceback
        print(f"[프로모션] 아침 안내 실패: {exc!r}")
        traceback.print_exc()
