"""
프로모션 공용 모듈 (날씨봇 · 중간봇 · 시상봇이 함께 사용)
─────────────────────────────────────────────
- 기간·매장별 목표를 여기서만 관리한다.
- 집계는 AI 없이 규칙 기반: data/raw/날짜.txt(실적 원본)에서 매장별 개통 줄 수를 센다.
  → 14·16·18시·마감 어디서 세도 같은 규칙이라 숫자가 일관되게 쌓인다.
- 기간이 아니면 아무것도 하지 않는다(다른 기능 영향 없음).
"""

import os
import re
import glob
import time

import requests

# ── 설정 ──────────────────────────────────────────
PROMO_ON   = True
PROMO_NAME = "9월 프로모션"
PROMO_DAYS = ["2026-09-23", "2026-09-24"]      # 2일 합산

PROMO_TARGETS = {
    # 광진/구리 (34)
    "자양번영로": 3, "금호동": 3, "건대입구역": 4, "외대역": 3, "다산신도시": 3,
    "진접": 3, "면목역": 3, "구리리맥스": 5, "상봉역": 3, "도농로": 4,
    # 경기북부 (39)
    "삼양로": 4, "수유": 7, "중계아울렛": 5, "먹골역": 4, "양주덕계": 3,
    "의정부로데오": 4, "옥정신도시": 4, "지행역": 4, "상계역": 4,
    # 강원 (37)
    "원주무실": 4, "단구": 4, "강릉임당": 5, "동해천곡": 5, "강릉유천": 3,
    "석사": 5, "홍천중앙": 4, "후평": 4, "온의": 3,
}

AREAS = {
    "광구": ["도농로", "구리리맥스", "자양번영로", "다산신도시", "건대입구역",
             "면목역", "상봉역", "외대역", "금호동", "진접"],
    "경북": ["중계아울렛", "수유", "의정부로데오", "옥정신도시", "삼양로",
             "먹골역", "지행역", "상계역", "양주덕계"],
    "강원": ["동해천곡", "석사", "강릉임당", "원주무실", "단구",
             "강릉유천", "홍천중앙", "후평", "온의"],
}
SHORT_NAME = {"의정부로데오": "의정부"}          # 표에서만 짧게

# 매장 인식 별칭 (모호한 "강릉", "양주"는 제외)
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
_ALIAS_LIST = sorted(((k, s) for s, ks in ALIASES.items() for k in ks),
                     key=lambda x: -len(x[0]))    # 긴 별칭부터 매칭

RAW_DIR = "data/raw"
SEP = "\n<<<MSG>>>\n"

HINTS = ("기변", "신규", "번이", "번호이동", "MNP", "mnp", "요할", "심플",
         "단할", "공시", "공통")
MODEL_PAT = re.compile(r"^([a-z]{1,4}\d|폴드|플립|아이폰|아\d|\d+울트라)", re.I)
SKIP_PAT = re.compile(
    r"(2nd|세컨|순신규|약갱|약정갱신|에센스|모든\s*[지G]|GTT|MIT|ITT|UIT|MUIT|"
    r"신동|원스톱|인터넷|일반전화|오피스넷|가전구독|단품|제카|위약금)", re.I)


def is_promo_day(date_str):
    return PROMO_ON and date_str in PROMO_DAYS


def day_index(date_str):
    """1일차=1, 2일차=2 … (기간 밖이면 0)."""
    return PROMO_DAYS.index(date_str) + 1 if date_str in PROMO_DAYS else 0


# ── 집계 (규칙 기반) ─────────────────────────────
def _load_raw(date_str):
    try:
        with open(f"{RAW_DIR}/{date_str}.txt", encoding="utf-8") as f:
            txt = f.read()
    except FileNotFoundError:
        return []
    return [m.strip() for m in txt.split(SEP) if m.strip()]


def _store_of(line):
    """줄 맨 앞의 매장명(별칭 포함)을 표준 매장명으로. 없으면 None."""
    for k, s in _ALIAS_LIST:
        if line.startswith(k):
            return s
    return None


def _is_sale_line(line):
    if line.count("/") < 1 or line.endswith(":") or "직영점" in line:
        return False
    model = line.split("/")[0].strip()
    if not model or len(model) > 15 or model.count(" ") >= 2:
        return False
    if SKIP_PAT.search(line):
        return False
    return bool(MODEL_PAT.match(model.replace(" ", ""))) or any(h in line for h in HINTS)


def count_store_sales(dates):
    """주어진 날짜들의 원본에서 매장별 개통 건수."""
    counts = {s: 0 for s in PROMO_TARGETS}
    for d in dates:
        for msg in _load_raw(d):
            cur = None
            for raw in msg.splitlines():
                line = raw.strip()
                if not line:
                    continue
                if line.endswith(":") and "직영점" in line:      # 작성자 줄 → 기본 매장
                    m = re.search(r"(\S+?)직영점", line)
                    if m and cur is None:
                        cur = _store_of(m.group(1))
                    continue
                if "/" not in line:                              # 점명·이름 줄
                    s = _store_of(line)
                    if s:
                        cur = s
                    continue
                if cur and _is_sale_line(line):
                    counts[cur] = counts.get(cur, 0) + 1
    return counts


def dates_until(date_str):
    """기간 시작부터 date_str까지(포함)."""
    return [d for d in PROMO_DAYS if d <= date_str]


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


def render_table(counts, title, subtitle, path="promo.png"):
    from PIL import Image, ImageDraw
    W, M = 1080, 28
    NAVY = (16, 42, 84); TEAL = (0, 150, 160); RED = (214, 69, 65)
    GRAY = (120, 130, 140); DARK = (35, 45, 60); GREEN = (0, 128, 96)
    LINE = (226, 230, 236); ALT = (248, 250, 252); AREABG = (233, 238, 246)
    COLS = [M, M + 78, M + 262, M + 370, M + 478, M + 700, W - M]
    LAB = ["상권", "매장", "목표", "실적", "달성률", "잔여"]
    ROW = 50
    H = 150 + 46 + ROW * sum(len(v) for v in AREAS.values()) + 90

    img = Image.new("RGB", (W, H), "white")
    d = ImageDraw.Draw(img)
    T = sum(PROMO_TARGETS.values())
    A = sum(min(counts.get(s, 0), 10**6) for s in PROMO_TARGETS)
    d.text((M, 34), title, font=_font(True, 44), fill=NAVY)
    tw = d.textlength(title, font=_font(True, 44))
    d.text((M + tw + 24, 48), f"{subtitle} · 지사 {A}/{T}건 ({A / T * 100:.0f}%)",
           font=_font(False, 27), fill=GRAY)

    y = 108
    d.rectangle([M, y, W - M, y + 46], fill=NAVY)
    for i, lab in enumerate(LAB):
        d.text(((COLS[i] + COLS[i + 1]) / 2, y + 9), lab,
               font=_font(True, 27), fill="white", anchor="ma")
    y += 46
    top, ri = y, 0
    for area, stores in AREAS.items():
        y0 = y
        for s in stores:
            if ri % 2:
                d.rectangle([COLS[1], y, W - M, y + ROW], fill=ALT)
            t, a = PROMO_TARGETS[s], counts.get(s, 0)
            r = a / t * 100 if t else 0
            col = GREEN if r >= 100 else (DARK if r >= 50 else RED)
            cy = y + ROW / 2
            d.text((COLS[1] + 14, cy), SHORT_NAME.get(s, s), font=_font(True, 27),
                   fill=DARK, anchor="lm")
            d.text(((COLS[2] + COLS[3]) / 2, cy), str(t), font=_font(False, 27),
                   fill=DARK, anchor="mm")
            d.text(((COLS[3] + COLS[4]) / 2, cy), str(a), font=_font(True, 27),
                   fill=TEAL, anchor="mm")
            bx0, bx1 = COLS[4] + 14, COLS[5] - 84
            bw = bx1 - bx0
            d.rounded_rectangle([bx0, cy - 9, bx1, cy + 9], radius=9, fill=(235, 238, 243))
            d.rounded_rectangle([bx0, cy - 9, bx0 + max(6, int(bw * min(r, 100) / 100)),
                                 cy + 9], radius=9, fill=col)
            d.text((COLS[5] - 10, cy), f"{r:.0f}%", font=_font(True, 25),
                   fill=col, anchor="rm")
            rem = max(0, t - a)
            d.text(((COLS[5] + COLS[6]) / 2, cy), "달성 ✓" if rem == 0 else f"{rem}건",
                   font=_font(rem == 0, 26), fill=GREEN if rem == 0 else GRAY, anchor="mm")
            d.line([COLS[1], y + ROW, W - M, y + ROW], fill=LINE)
            y += ROW
            ri += 1
        at = sum(PROMO_TARGETS[s] for s in stores)
        aa = sum(counts.get(s, 0) for s in stores)
        d.rectangle([M, y0, COLS[1], y], fill=AREABG)
        d.text(((M + COLS[1]) / 2, (y0 + y) / 2 - 14), area, font=_font(True, 29),
               fill=NAVY, anchor="mm")
        d.text(((M + COLS[1]) / 2, (y0 + y) / 2 + 18), f"{aa / at * 100:.0f}%",
               font=_font(True, 22), fill=TEAL, anchor="mm")
        d.line([M, y, W - M, y], fill=NAVY, width=3)
    for x in COLS[1:-1]:
        d.line([x, top, x, y], fill=LINE)
    n_ok = sum(1 for s in PROMO_TARGETS if counts.get(s, 0) >= PROMO_TARGETS[s])
    d.text((M, y + 18),
           f"달성 {n_ok}곳 · 미달 {len(PROMO_TARGETS) - n_ok}곳   |   "
           f"실적공유방 기준 · 초록 100%↑ 검정 50%↑ 빨강 50% 미만",
           font=_font(False, 22), fill=GRAY)
    img.save(path)
    return path, n_ok, A, T


# ── 아침 목표 안내(1일차) ────────────────────────
def targets_text():
    d1, d2 = PROMO_DAYS[0], PROMO_DAYS[-1]
    L = [f"🎯 {PROMO_NAME} 시작! ({int(d1[5:7])}/{int(d1[8:])}~{int(d2[5:7])}/{int(d2[8:])} · 2일 합산)",
         "", "매장별 목표 (실적공유방 휴대폰 개통 기준)", ""]
    for area, stores in AREAS.items():
        at = sum(PROMO_TARGETS[s] for s in stores)
        L.append(f"[{area}] {at}건")
        items = [f"{SHORT_NAME.get(s, s)} {PROMO_TARGETS[s]}" for s in stores]
        for i in range(0, len(items), 5):
            L.append("  " + " · ".join(items[i:i + 5]))
        L.append("")
    L.append(f"지사 목표 {sum(PROMO_TARGETS.values())}건")
    L.append("14·16·18시와 마감에 달성 현황을 표로 공유합니다 🔥")
    return "\n".join(L)


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
                print(f"[프로모션] 표 게시 완료" + (f" (재시도 {i}회차)" if i > 1 else ""))
                return True
            print(f"[프로모션] 표 게시 거부: {j.get('error_code')} {j.get('description')!r}")
            if j.get("error_code") not in (500, 502, 503, 504, 429):
                return False
        except Exception as exc:
            print(f"[프로모션] 표 전송 예외({i}/{retries}): {exc!r}")
        if i < retries:
            time.sleep(10 * i)
    return False


def post_status(tg_base, chat_id, date_str, when):
    """중간점검·마감용: 누적 집계 → 표 이미지 → 전송. 기간 밖이면 아무것도 안 함."""
    if not is_promo_day(date_str):
        return
    try:
        counts = count_store_sales(dates_until(date_str))
        n = day_index(date_str)
        final = (date_str == PROMO_DAYS[-1] and when == "마감")
        title = f"{PROMO_NAME} {'최종 결과' if final else '달성 현황'}"
        md = f"{int(date_str[5:7])}/{int(date_str[8:])}"
        sub = f"{md} {n}일차 {when}"
        path, n_ok, A, T = render_table(counts, title, sub)
        if final:
            cap = (f"🏆 {PROMO_NAME} 최종 결과 — 지사 {A}/{T}건 ({A / T * 100:.0f}%) · "
                   f"달성 매장 {n_ok}곳! 2일간 고생 많으셨습니다 👏")
        else:
            cap = f"🎯 {PROMO_NAME} {n}일차 {when} 현황 — 지사 {A}/{T}건 ({A / T * 100:.0f}%)"
        send_photo(tg_base, chat_id, path, cap)
    except Exception as exc:
        import traceback
        print(f"[프로모션] 처리 실패: {exc!r}")
        traceback.print_exc()


def post_morning(tg_base, chat_id, date_str, send_text):
    """아침: 1일차는 목표 안내 텍스트, 2일차부터는 전일까지 누적 표."""
    if not is_promo_day(date_str):
        return
    try:
        n = day_index(date_str)
        if n == 1:
            send_text(targets_text())
            return
        prev = [d for d in PROMO_DAYS if d < date_str]
        counts = count_store_sales(prev)
        md = f"{int(prev[-1][5:7])}/{int(prev[-1][8:])}"
        path, n_ok, A, T = render_table(counts, f"{PROMO_NAME} 중간 결과",
                                        f"{md}까지 누적")
        cap = (f"🎯 {PROMO_NAME} {n}일차 (마지막 날!) — 어제까지 {A}/{T}건 "
               f"({A / T * 100:.0f}%) · 달성 {n_ok}곳\n"
               f"잔여 건수 확인하시고 오늘 마무리해봐요 🔥")
        send_photo(tg_base, chat_id, path, cap)
    except Exception as exc:
        import traceback
        print(f"[프로모션] 아침 안내 실패: {exc!r}")
        traceback.print_exc()
