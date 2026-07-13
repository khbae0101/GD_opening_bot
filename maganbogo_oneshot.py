# -*- coding: utf-8 -*-
"""
텔레그램 마감보고 이미지 요약 — 만점이 v2 (1회 실행용)
─────────────────────────────────────────────
- 방에 올라온 각 매장 '마감보고'만 골라내(잡담 무시) 전일자 기준으로 정리.
- Claude는 매장별 숫자 '추출(JSON)'만 담당, 합계·비중·순위·음영은 파이썬이 계산.
- 결과를 표 이미지로 만들어 게시. (텍스트 → 이미지 전환 버전)
 
비밀값(Secrets) — 기존과 동일:
  TELEGRAM_TOKEN_2 / SOURCE_CHAT_ID_2 / TARGET_CHAT_ID_2 / ANTHROPIC_API_KEY
 
워크플로 준비물 (기존 대비 추가):
  sudo apt-get install -y fonts-noto-cjk
  pip install requests anthropic pillow
"""
 
import json
import os
import re
from datetime import datetime, timedelta, timezone
 
import requests
import anthropic
from PIL import Image, ImageDraw, ImageFont
 
TELEGRAM_TOKEN    = os.environ["TELEGRAM_TOKEN_2"]
ANTHROPIC_API_KEY = os.environ["ANTHROPIC_API_KEY"]
SOURCE_CHAT_ID    = int(os.environ["SOURCE_CHAT_ID_2"])
TARGET_CHAT_ID    = int(os.environ.get("TARGET_CHAT_ID_2", SOURCE_CHAT_ID))
SUMMARY_MODEL     = "claude-sonnet-4-6"
 
API = f"https://api.telegram.org/bot{TELEGRAM_TOKEN}"
KST = timezone(timedelta(hours=9))
WEEKDAY = "월화수목금토일"
 
TITLE = "■ 강동소매 TCSI 모바일조사 100점 확정건수 현황"
 
STORE_MAP = {
    "광진/구리": ["도농로", "구리리맥스", "자양번영로", "다산신도시", "건대입구역",
                 "면목역", "상봉역", "외대역", "금호동", "진접"],
    "경기북부": ["중계아울렛", "수유", "의정부로데오", "옥정신도시", "삼양로",
                "먹골역", "지행역", "상계역", "양주덕계"],
    "강원":     ["동해천곡", "석사", "강릉임당", "원주무실", "단구",
                "강릉유천", "홍천중앙", "후평", "온의"],
}
ALL_STORES = [s for lst in STORE_MAP.values() for s in lst]
 
 
# ──────────────────────────── 텔레그램 수집 ────────────────────────────
def fetch_messages():
    collected, offset = [], None
    while True:
        params = {"timeout": 0, "limit": 100}
        if offset is not None:
            params["offset"] = offset
        r = requests.get(f"{API}/getUpdates", params=params, timeout=30).json()
        batch = r.get("result", [])
        if not batch:
            break
        for u in batch:
            offset = u["update_id"] + 1
            m = u.get("message") or u.get("channel_post")
            if not m:
                continue
            text = m.get("text") or m.get("caption")
            if text and m["chat"]["id"] == SOURCE_CHAT_ID:
                collected.append(text)
    return collected
 
 
# ──────────────────────────── Claude 숫자 추출 ────────────────────────────
def build_prompt(messages):
    store_lines = "\n".join(
        f"[{area}] " + ", ".join(stores) for area, stores in STORE_MAP.items())
    body = "\n\n---\n\n".join(messages)
    return f"""아래는 텔레그램 방에 올라온 메시지들이야. 마감보고 외에 잡담·공지도 섞여 있어.
각 매장의 '마감보고'만 골라내서 전일자 기준 숫자를 JSON으로 추출해줘. 계산은 하지 마.
 
# 매장 목록 (이 이름으로만 매칭)
{store_lines}
 
# 마감보고 식별
- 보통 "개통", "문자발송 당일/누적", "100점 확정건 당일/누적", "처리자"와 날짜·매장명을 포함.
- 이런 구조가 아닌 잡담/공지는 전부 무시.
 
# 매장명 매칭
- 줄임말 매칭: 무실→원주무실, 임당→강릉임당, 의로→의정부로데오, 유천→강릉유천,
  상계→상계역, 먹골→먹골역, 건대→건대입구역, 중계→중계아울렛, 구리→구리리맥스, 덕계→양주덕계 등.
- "양주" 단독은 옥정신도시와 양주덕계 중 어느 쪽인지 불명확 → "unmatched"에 원문 매장명으로 넣어.
- 한 보고에 여러 날짜가 있으면 가장 최근 날짜(전일자) 것만 사용.
- 같은 매장 보고가 여러 건이면 가장 마지막(최신) 것만 사용.
 
# 출력 (JSON만, 다른 텍스트·마크다운 백틱 절대 금지)
{{
 "date": "M/D",
 "stores": {{
   "매장명": {{"d_snd": 당일발송, "d_cfm": 당일확정, "c_snd": 누적발송, "c_cfm": 누적확정}}
 }},
 "unmatched": ["매칭 실패한 원문 매장명"]
}}
- 숫자는 정수. 보고에 값이 없으면 0.
- 마감보고가 없는 매장은 stores에 넣지 마 (파이썬이 미등록 처리).
 
# 메시지 원문
{body}
"""
 
 
def extract(messages):
    client = anthropic.Anthropic(api_key=ANTHROPIC_API_KEY)
    resp = client.messages.create(
        model=SUMMARY_MODEL,
        max_tokens=4000,
        system="너는 JSON 추출 도구야. 요청된 JSON 객체 하나만 출력하고 "
               "설명·머리말·백틱을 절대 붙이지 마.",
        messages=[{"role": "user", "content": build_prompt(messages)}],
    )
    text = resp.content[0].text.strip()
    text = re.sub(r"^```(json)?|```$", "", text, flags=re.MULTILINE).strip()
    m = re.search(r"\{.*\}", text, re.DOTALL)
    if not m:
        raise ValueError(f"JSON 파싱 실패: {text[:200]}")
    data = json.loads(m.group())
    # 이름 검증: 목록 밖 이름은 unmatched로 이동
    stores = {}
    unmatched = list(data.get("unmatched", []))
    for name, v in data.get("stores", {}).items():
        if name in ALL_STORES:
            stores[name] = {k: int(v.get(k, 0))
                            for k in ("d_snd", "d_cfm", "c_snd", "c_cfm")}
        else:
            unmatched.append(name)
    return data.get("date", ""), stores, unmatched
 
 
# ──────────────────────────── 이미지 렌더 ────────────────────────────
FONT_R = "/usr/share/fonts/opentype/noto/NotoSansCJK-Regular.ttc"
FONT_B = "/usr/share/fonts/opentype/noto/NotoSansCJK-Bold.ttc"
 
COLS = [("상권", "", 106), ("매장", "", 166),
        ("당일", "발송", 74), ("당일", "확정", 74), ("당일", "확정비중", 106),
        ("누적", "발송", 74), ("누적", "확정", 74), ("누적", "확정비중", 106)]
 
C_TITLE = (63, 63, 63); C_HEAD = (217, 217, 217); C_TOTAL = (38, 38, 38)
C_REGION = (228, 223, 236); C_GRID = (160, 160, 160); C_MISS = (150, 150, 150)
C_HL = {"good": ((198, 239, 206), (0, 97, 0)),
        "bad": ((255, 199, 206), (156, 0, 6))}
 
 
def pct(c, s):
    return f"{c/s*100:.1f}%" if s else "-"
 
 
def render(rows, date_str, out_path, footnote=None, scale=2):
    F = lambda p, s: ImageFont.truetype(p, int(s * scale))
    f_title, f_head, f_sub = F(FONT_B, 26), F(FONT_B, 18), F(FONT_B, 15)
    f_cell, f_cellb = F(FONT_R, 19), F(FONT_B, 19)
    pad, row_h, title_h, head_h = int(14*scale), int(42*scale), int(56*scale), int(62*scale)
    widths = [int(w * scale) for *_, w in COLS]
    W = sum(widths) + pad * 2
    foot_h = int(40 * scale) if footnote else int(10 * scale)
    H = pad + title_h + head_h + len(rows) * row_h + foot_h + pad
    img = Image.new("RGB", (W, H), "white")
    d = ImageDraw.Draw(img)
 
    d.rectangle([pad, pad, W - pad, pad + title_h], fill=C_TITLE)
    d.text((pad + 12*scale, pad + title_h//2), TITLE, font=f_title,
           fill="white", anchor="lm")
    d.text((W - pad - 12*scale, pad + title_h//2), date_str, font=f_title,
           fill=(255, 220, 90), anchor="rm")
 
    xs = [pad]
    for w in widths:
        xs.append(xs[-1] + w)
    y0 = pad + title_h
    d.rectangle([pad, y0, W - pad, y0 + head_h], fill=C_HEAD)
    i = 0
    while i < len(COLS):
        g = COLS[i][0]; j = i
        while j < len(COLS) and COLS[j][0] == g:
            j += 1
        x1, x2 = xs[i], xs[j]
        if any(COLS[k][1] for k in range(i, j)):
            ymid = y0 + head_h // 2
            d.text(((x1+x2)//2, y0 + head_h//4), g, font=f_head, anchor="mm")
            d.line([x1, ymid, x2, ymid], fill=C_GRID, width=scale)
            for k in range(i, j):
                d.text(((xs[k]+xs[k+1])//2, y0 + 3*head_h//4), COLS[k][1],
                       font=f_sub, anchor="mm")
                if k > i:
                    d.line([xs[k], ymid, xs[k], y0+head_h], fill=C_GRID, width=scale)
        else:
            d.text(((x1+x2)//2, y0 + head_h//2), g, font=f_head, anchor="mm")
        d.line([x2, y0, x2, y0 + head_h], fill=C_GRID, width=scale)
        i = j
 
    y = y0 + head_h
    for r in rows:
        typ = r["type"]
        bg = {"total": C_TOTAL, "region": C_REGION}.get(typ)
        if bg:
            d.rectangle([pad, y, W - pad, y + row_h], fill=bg)
        fg = "white" if typ == "total" else (0, 0, 0)
        font = f_cellb if typ in ("total", "region") else f_cell
        hl = r.get("hl")
        if hl:
            bgc, fgc = C_HL[hl]
            d.rectangle([pad, y, W - pad, y + row_h], fill=bgc)
            fg = fgc
        for k, v in enumerate(r["cells"]):
            if v is None or v == "":
                continue
            cell_fg = C_MISS if typ == "store_missing" else fg
            d.text(((xs[k]+xs[k+1])//2, y + row_h//2), str(v), font=font,
                   fill=cell_fg, anchor="mm")
        d.line([pad, y + row_h, W - pad, y + row_h], fill=C_GRID, width=scale)
        y += row_h
    for x in xs:
        d.line([x, y0, x, y], fill=C_GRID, width=scale)
    d.rectangle([pad, y0, W - pad, y], outline=(60, 60, 60), width=scale)
    if footnote:
        d.text((pad + 2*scale, y + 12*scale), footnote, font=F(FONT_B, 16),
               fill=(200, 30, 30), anchor="lm")
    img.save(out_path, optimize=True)
    return out_path
 
 
def build_rows(stores):
    """stores: {매장명: {d_snd,d_cfm,c_snd,c_cfm}} → 표 행 + 부가정보."""
    def cells(name_cols, ds, dc, cs, cc):
        return name_cols + [ds, dc, pct(dc, ds), cs, cc, pct(cc, cs)]
 
    def agg(names):
        vs = [stores[n] for n in names if n in stores]
        return (sum(v["d_snd"] for v in vs), sum(v["d_cfm"] for v in vs),
                sum(v["c_snd"] for v in vs), sum(v["c_cfm"] for v in vs))
 
    registered = [n for n in ALL_STORES if n in stores]
    ranked = sorted(registered,
                    key=lambda n: (stores[n]["c_cfm"] / stores[n]["c_snd"])
                    if stores[n]["c_snd"] else 0, reverse=True)
    q = max(1, int(len(ranked) * 0.25 + 0.5)) if ranked else 0
    hlmap = {}
    for i, n in enumerate(ranked):
        if i < q:
            hlmap[n] = "good"
        elif i >= len(ranked) - q:
            hlmap[n] = "bad"
 
    rows = [{"type": "total", "cells": cells(["", "지사 계"], *agg(ALL_STORES))}]
    missing = []
    for reg, names in STORE_MAP.items():
        rows.append({"type": "region", "cells": cells(["", reg], *agg(names))})
        reg_on = [n for n in names if n in stores]
        reg_on.sort(key=lambda n: (stores[n]["c_cfm"] / stores[n]["c_snd"])
                    if stores[n]["c_snd"] else 0, reverse=True)
        for n in reg_on:
            v = stores[n]
            rows.append({"type": "store", "hl": hlmap.get(n),
                         "cells": cells([reg, n], v["d_snd"], v["d_cfm"],
                                        v["c_snd"], v["c_cfm"])})
        for n in [x for x in names if x not in stores]:
            rows.append({"type": "store_missing",
                         "cells": [reg, n, "미등록"] + [""] * 5})
            missing.append(n)
    return rows, missing, agg(ALL_STORES)
 
 
def send_photo(path, caption):
    with open(path, "rb") as f:
        requests.post(f"{API}/sendPhoto",
                      data={"chat_id": TARGET_CHAT_ID, "caption": caption},
                      files={"photo": f}, timeout=60).raise_for_status()
 
 
def date_with_weekday(mdate):
    """'7/9' → '7/9(목) 기준'. 파싱 실패 시 원문 그대로."""
    try:
        m, d = (int(x) for x in mdate.split("/"))
        now = datetime.now(KST)
        dt = datetime(now.year, m, d)
        return f"{m}/{d}({WEEKDAY[dt.weekday()]}) 기준"
    except Exception:
        return f"{mdate} 기준" if mdate else "마감 기준"
 
 
def main():
    messages = fetch_messages()
    print(f"마감보고방에서 {len(messages)}건 수집")
    if not messages:
        print("메시지 없음 - 게시하지 않습니다.")
        return
    if len(messages) < 5:
        print(f"수집 {len(messages)}건으로 너무 적어 보류합니다. (읽기 타이밍 가능성)")
        return
 
    mdate, stores, unmatched = extract(messages)
    if not stores:
        print("추출된 마감보고가 없어 게시하지 않습니다.")
        return
 
    rows, missing, (ds, dc, cs, cc) = build_rows(stores)
    foot = f"※ 미등록 매장({len(missing)}개) : " + ", ".join(missing) if missing \
        else "※ 전 매장 등록 완료"
    foot += "   /   음영 : 누적 확정비중 상·하위 25%"
 
    out = "manjeom_report.png"
    render(rows, date_with_weekday(mdate), out, footnote=foot)
 
    cap = (f"📊 {mdate} 마감 현황\n"
           f"지사 누적 : 발송 {cs:,}건 / 확정 {cc:,}건 / {pct(cc, cs)}")
    if missing:
        cap += f"\n※ 미등록 {len(missing)}점: {', '.join(missing)}"
    if unmatched:
        cap += f"\n⚠ 매장명 확인필요: {', '.join(unmatched)}"
    send_photo(out, cap)
    print("마감보고 이미지 게시 완료")
 
 
if __name__ == "__main__":
    main()
 
