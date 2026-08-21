"""
텔레그램 방 일일 출근현황 - 표 이미지 게시 (1회 실행용)
─────────────────────────────────────────────
- 출근등록 메시지를 AI가 읽어 매장별 데이터(JSON)로 정리
- 코드가 표 이미지(상권/매장/총원/근무자/휴무자 + 특이사항)를 그려 사진으로 게시
- 이미지 생성 실패 시 텍스트로 대신 게시(안전장치)
 
전제 조건:
- 봇 웹훅 없음, 다른 곳에서 polling 안 함, 하루 1번 이상 실행
- 그룹이면 BotFather에서 privacy mode Disable
- 워크플로에 pillow 설치 + fonts-noto-cjk 설치 필요
"""
 
import os
import json
import glob
from datetime import datetime
from zoneinfo import ZoneInfo
 
import requests
import anthropic
 
TELEGRAM_TOKEN    = os.environ["TELEGRAM_TOKEN"]
ANTHROPIC_API_KEY = os.environ["ANTHROPIC_API_KEY"]
SOURCE_CHAT_ID    = int(os.environ["SOURCE_CHAT_ID"])
TARGET_CHAT_ID    = int(os.environ.get("TARGET_CHAT_ID", SOURCE_CHAT_ID))
SUMMARY_MODEL     = "claude-sonnet-4-6"
 
API = f"https://api.telegram.org/bot{TELEGRAM_TOKEN}"
KST = ZoneInfo("Asia/Seoul")
 
# ── 직영점 목록 (상권별) — 매장 변동 시 여기만 수정 ──────
STORE_MAP = {
    "광진/구리": ["도농로", "구리리맥스", "자양번영로", "다산신도시", "건대입구역",
                 "면목역", "상봉역", "외대역", "금호동", "진접"],
    "경기북부": ["중계아울렛", "수유", "의정부로데오", "옥정신도시", "삼양로",
                "먹골역", "지행역", "상계역", "양주덕계"],
    "강원":     ["동해천곡", "석사", "강릉임당", "원주무실", "단구",
                "강릉유천", "홍천중앙", "후평", "온의"],
}
AREA_SHORT = {"광진/구리": "광구", "경기북부": "경북", "강원": "강원"}
# 표(이미지)에서만 짧게 표시할 매장명. 실제 매칭·명단은 원래 이름 그대로 사용.
STORE_SHORT = {"의정부로데오": "의정부"}
 
 
def load_managers():
    """data/roster.csv에서 매장별 점장 이름 {매장: 이름}. 없으면 빈 dict."""
    import csv
    mgr = {}
    try:
        with open("data/roster.csv", newline="", encoding="utf-8-sig") as f:
            rows = list(csv.reader(f))
        for r in rows[1:]:
            if len(r) >= 3 and r[2].strip() == "점장":
                mgr[r[0].strip()] = r[1].strip()
    except Exception as e:
        print(f"[출근] 점장 명단 로드 실패({e!r}) - 점장 표시 없이 진행")
    return mgr
 
 
MONTH_MARKS = (3, 6, 9)                 # 1년 미만: 개월 축하
YEAR_MARKS  = (1, 3, 5, 10, 15, 20)     # 1년 이상: 주년 축하
 
 
def anniversaries(today):
    """오늘 입사 기념일인 직원 [(매장, 이름, '3개월'|'1주년')]."""
    import csv
    out = []
    try:
        with open("data/roster.csv", newline="", encoding="utf-8-sig") as f:
            rows = list(csv.reader(f))
    except Exception as e:
        print(f"[출근] 명단 로드 실패({e!r}) - 기념일 생략")
        return out
    for r in rows[1:]:
        if len(r) < 4 or not r[3].strip():
            continue
        try:
            hd = datetime.strptime(r[3].strip(), "%Y-%m-%d").date()
        except ValueError:
            continue
        if hd >= today or hd.day != today.day:
            continue
        months = ((today.year - hd.year) * 12 + today.month - hd.month)
        if months < 12:
            if months in MONTH_MARKS:
                out.append((r[0].strip(), r[1].strip(), f"{months}개월"))
        elif months % 12 == 0 and months // 12 in YEAR_MARKS:
            out.append((r[0].strip(), r[1].strip(), f"{months // 12}주년"))
    return out
 
 
def fetch_messages():
    collected = []
    offset = None
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
            if not text:
                continue
            if m["chat"]["id"] != SOURCE_CHAT_ID:
                continue
            collected.append(text)
    return collected
 
 
def build_prompt(messages, today):
    store_lines = "\n".join(
        f"[{area}] " + ", ".join(stores) for area, stores in STORE_MAP.items()
    )
    body = "\n\n---\n\n".join(messages)
    return f"""오늘은 {today} 야. 아래는 텔레그램 방에 올라온 메시지들이야.
각 직영점이 올리는 "출근등록" 메시지를 읽고, 매장별 출근 데이터를 JSON으로 정리해줘.
 
# 직영점 목록 (상권별 — 이 목록의 매장명이 기준이야)
{store_lines}
 
# 점포명 매칭 규칙
- 매장이 매장명을 줄여서 올릴 수 있어. (예: "강릉임당"→"강릉", "동해천곡"→"동해", "양주덕계"→"덕계")
- 올라온 이름이 위 목록 중 어떤 점포와 닮았거나 일부면 그 점포로 매칭하고, store 값은 반드시 목록의 표준 매장명으로 써.
- "덕계"는 양주덕계로 매칭해. 다만 "양주"만 단독이면 옥정신도시와 헷갈리니 note에 "표기 확인필요"라고 적어.
- 한 약칭이 두 점포에 모두 해당되면(예: "강릉") 임의 추측하지 말고 note에 "표기 확인필요(원문: ...)"로 적어.
- 목록에 없는 매장이나 출근등록이 아닌 잡담은 무시해.
 
# 정리 규칙
- work = 근무(출근) 인원 이름 배열, off = 휴무 인원 이름 배열.
- note = 메시지에 적힌 특이사항(교육, 오후출근, 지각, 외근, 반차 등)을 짧게. 없으면 "".
- 출근등록 메시지가 없는 매장은 결과에 넣지 마(코드가 미등록 처리해).
 
# 출력 (JSON만, 설명·코드블록 없이)
{{"stores": [{{"store": "도농로", "work": ["이름", "이름"], "off": ["이름"], "note": ""}}, ...]}}
 
# 메시지 원문
{body}
"""
 
 
def summarize(messages, today):
    client = anthropic.Anthropic(api_key=ANTHROPIC_API_KEY)
    resp = client.messages.create(
        model=SUMMARY_MODEL,
        max_tokens=4000,
        system="너는 요청된 JSON만 출력하는 도구야. 설명이나 코드블록 없이 JSON만 출력해.",
        messages=[{"role": "user", "content": build_prompt(messages, today)}],
    )
    text = (resp.content[0].text if resp.content else "").strip()
    if "```" in text:
        text = text.split("```")[1].replace("json", "", 1).strip() if text.count("```") >= 2 else text
    if "{" in text and "}" in text:
        text = text[text.index("{"):text.rindex("}") + 1]
    else:
        print(f"[출근] AI 응답에 JSON 없음: {text[:200]!r}")
        return None
    try:
        return json.loads(text)
    except Exception as e:
        print(f"[출근] JSON 파싱 실패: {e!r} · {text[:200]!r}")
        return None
 
 
# ── 표 이미지 렌더링 ─────────────────────────────────────
def _font(bold, size):
    pats = ["/usr/share/fonts/opentype/noto/NotoSansCJK-Bold.ttc",
            "/usr/share/fonts/**/NotoSansCJK*Bold*"] if bold else \
           ["/usr/share/fonts/opentype/noto/NotoSansCJK-Regular.ttc",
            "/usr/share/fonts/**/NotoSansCJK*Regular*"]
    from PIL import ImageFont
    for p in pats:
        hits = glob.glob(p, recursive=True)
        if hits:
            return ImageFont.truetype(hits[0], size)
    return ImageFont.load_default()
 
 
def render_image(data, today_label, path="attend.png", managers=None, annis=None):
    from PIL import Image, ImageDraw
    managers = managers or {}
    annis = annis or []
 
    # data: {store: {"work": [...], "off": [...], "note": ""}}
    W, M = 1080, 28
    NAVY = (16, 42, 84); TEAL = (0, 150, 160)
    RED = (214, 69, 65); GRAY = (120, 130, 140); DARK = (35, 45, 60)
    LINE = (226, 230, 236); ALT = (248, 250, 252); AREABG = (233, 238, 246)
 
    f_title = _font(True, 46); f_sub = _font(False, 28)
    f_head = _font(True, 27); f_cell = _font(False, 27)
    f_cellb = _font(True, 27); f_area = _font(True, 30)
    f_note = _font(False, 26); f_foot = _font(False, 22)
    f_noteh = _font(True, 30)
 
    MGR = (0, 96, 108)   # 점장 이름 강조색(진한 틸)
    COLS = [M, M + 66, M + 230, M + 288, M + 346, M + 404, M + 780, W - M]
    LABELS = ["상권", "매장", "총원", "근무", "점장", "근무자", "휴무자"]
    IDX_WORK, IDX_OFF = 5, 6   # 근무자·휴무자 열 시작 인덱스
    ROW = 52
 
    def wrap(text, font, maxw, d):
        if not text:
            return [""]
        words, lines, cur = text.split(), [], ""
        for w0 in words:
            t = (cur + " " + w0).strip()
            if d.textlength(t, font=font) <= maxw:
                cur = t
            else:
                if cur:
                    lines.append(cur)
                cur = w0
        lines.append(cur)
        return lines
 
    tmp = ImageDraw.Draw(Image.new("RGB", (10, 10)))
 
    WORK_W = COLS[IDX_OFF] - COLS[IDX_WORK] - 24
    OFF_W = COLS[IDX_OFF + 1] - COLS[IDX_OFF] - 24
 
    def row_h(work_str, off_str=""):
        n = max(len(wrap(work_str, f_cell, WORK_W, tmp)),
                len(wrap(off_str, f_cell, OFF_W, tmp)))
        return max(ROW, 30 * n + 20)
 
    # 특이사항 목록 (미등록은 한 줄로 묶고, note는 매장별로)
    notes = []
    missing = [STORE_SHORT.get(s, s) for area, ss in STORE_MAP.items()
               for s in ss if s not in data]
    if missing:
        notes.append(("미등록", ", ".join(missing)))
    for area, stores in STORE_MAP.items():
        for s in stores:
            info = data.get(s)
            if info and info.get("note"):
                notes.append((STORE_SHORT.get(s, s), info["note"]))
 
    tot_work = sum(len(v.get("work", [])) for v in data.values())
    tot_off = sum(len(v.get("off", [])) for v in data.values())
    n_missing = sum(1 for area, ss in STORE_MAP.items() for s in ss if s not in data)
    summary = (f"{today_label} · 총원 {tot_work + tot_off} · 출근 {tot_work}"
               f" · 휴무 {tot_off} · 미등록 {n_missing}점")
 
    H = 148 + 46
    for area, stores in STORE_MAP.items():
        for s in stores:
            info = data.get(s, {})
            H += row_h(" ".join(info.get("work", [])), ", ".join(info.get("off", [])))
    H += 40 + 46 + max(len(notes), 1) * 36 + 160   # 특이사항 줄바꿈 여유
    if annis:
        H += 46 + len(annis) * 36 + 20   # 입사 기념일 블록
 
    img = Image.new("RGB", (W, H), (255, 255, 255))
    d = ImageDraw.Draw(img)
    y = 36
    d.text((M, y), "출근 현황", font=f_title, fill=NAVY)
    d.text((M + 228, y + 13), summary, font=f_sub, fill=GRAY)
    y += 72
 
    d.rectangle([M, y, W - M, y + 46], fill=NAVY)
    for i, lab in enumerate(LABELS):
        d.text(((COLS[i] + COLS[i + 1]) / 2, y + 9), lab, font=f_head,
               fill=(255, 255, 255), anchor="ma")
    y += 46
    top_table = y
 
    ri = 0
    for area, stores in STORE_MAP.items():
        y0 = y
        for s in stores:
            info = data.get(s)
            work = " ".join(info.get("work", [])) if info else ""
            off = ", ".join(info.get("off", [])) if info else ""
            h = row_h(work, off)
            if ri % 2:
                d.rectangle([COLS[1], y, W - M, y + h], fill=ALT)
            total = (len(info.get("work", [])) + len(info.get("off", []))) if info else 0
            n_work = len(info.get("work", [])) if info else 0
            d.text((COLS[1] + 14, y + h / 2), STORE_SHORT.get(s, s), font=f_cellb,
                   fill=DARK, anchor="lm")
            d.text(((COLS[2] + COLS[3]) / 2, y + h / 2), str(total) if total else "-",
                   font=f_cell, fill=DARK, anchor="mm")
            d.text(((COLS[3] + COLS[4]) / 2, y + h / 2), str(n_work) if info else "-",
                   font=f_cellb if n_work else f_cell, fill=TEAL if n_work else LINE,
                   anchor="mm")
            # 점장 근무 여부 (O / X)
            mgr_name = managers.get(s)
            if info is None or not mgr_name:
                mark, mcolor, mfont = "-", LINE, f_cell
            elif mgr_name in info.get("work", []):
                mark, mcolor, mfont = "O", MGR, f_cellb
            else:
                mark, mcolor, mfont = "X", RED, f_cell
            d.text(((COLS[4] + COLS[5]) / 2, y + h / 2), mark, font=mfont,
                   fill=mcolor, anchor="mm")
            if info is None:
                d.text((COLS[IDX_WORK] + 14, y + h / 2), "미등록", font=f_cellb,
                       fill=RED, anchor="lm")
            else:
                ls = wrap(work, f_cell, WORK_W, d)
                ty = y + h / 2 - (len(ls) - 1) * 15
                for l in ls:
                    tx = COLS[IDX_WORK] + 14
                    for tok in l.split():          # 이름 단위로 그려 점장만 강조
                        is_mgr = (tok == mgr_name)
                        fnt = f_cellb if is_mgr else f_cell
                        d.text((tx, ty), tok, font=fnt,
                               fill=MGR if is_mgr else TEAL, anchor="lm")
                        tx += d.textlength(tok + " ", font=fnt)
                    ty += 30
            if off:
                ols = wrap(off, f_cell, OFF_W, d)
                oy = y + h / 2 - (len(ols) - 1) * 15
                for l in ols:
                    d.text((COLS[IDX_OFF] + 14, oy), l, font=f_cell, fill=GRAY, anchor="lm")
                    oy += 30
            else:
                d.text((COLS[IDX_OFF] + 14, y + h / 2), "-", font=f_cell,
                       fill=LINE, anchor="lm")
            d.line([COLS[1], y + h, W - M, y + h], fill=LINE, width=1)
            y += h
            ri += 1
        d.rectangle([M, y0, COLS[1], y], fill=AREABG)
        d.text(((M + COLS[1]) / 2, (y0 + y) / 2), AREA_SHORT.get(area, area),
               font=f_area, fill=NAVY, anchor="mm")
        d.line([M, y, W - M, y], fill=NAVY, width=3)
 
    for x in COLS[1:-1]:
        d.line([x, top_table, x, y], fill=LINE, width=1)
    d.line([M, top_table, M, y], fill=NAVY, width=1)
    d.line([W - M, top_table, W - M, y], fill=NAVY, width=1)
 
    y += 26
    d.rounded_rectangle([M, y + 4, M + 7, y + 32], radius=3, fill=TEAL)
    d.text((M + 20, y), "특이사항", font=f_noteh, fill=NAVY)
    y += 46
    if notes:
        for store, note in notes:
            color = RED if store == "미등록" or "미등록" in note else GRAY
            for i, l in enumerate(wrap(f"· {store} — {note}", f_note, W - M * 2 - 20, d)):
                d.text((M + 10 + (24 if i else 0), y), l, font=f_note, fill=color)
                y += 36
    else:
        d.text((M + 10, y), "· 없음", font=f_note, fill=GRAY)
        y += 36
 
    if annis:
        y += 24
        d.rounded_rectangle([M, y + 4, M + 7, y + 32], radius=3, fill=TEAL)
        d.text((M + 20, y), "오늘의 입사 기념일", font=f_noteh, fill=NAVY)
        y += 46
        for store, name, label in annis:
            txt = f"· {STORE_SHORT.get(store, store)} {name} 님 — 입사 {label}"
            d.text((M + 10, y), txt, font=f_note, fill=TEAL)
            y += 36
 
    d.text((M, y + 8), "강동요정봇 자동 생성", font=f_foot, fill=(185, 192, 200))
    img.save(path)
    return path, summary
 
 
def build_fallback_text(data, today_label):
    """이미지 실패 시 대신 보낼 간단 텍스트."""
    lines = [f"📋 {today_label} 출근 현황"]
    for area, stores in STORE_MAP.items():
        lines.append("")
        lines.append(f"🏙 {area}")
        for s in stores:
            info = data.get(s)
            if info is None:
                lines.append(f"▪ {s} : 미등록")
            else:
                w = ", ".join(info.get("work", []))
                o = ", ".join(info.get("off", []))
                lines.append(f"▪ {s} : {w}" + (f" / 휴무 {o}" if o else ""))
    return "\n".join(lines)
 
 
def _report(kind, resp):
    """전송 결과 로그. 실패 시 텔레그램이 알려준 사유를 그대로 남긴다."""
    try:
        j = resp.json()
    except Exception:
        print(f"[출근] {kind} 응답 해석 실패 · status={resp.status_code}"
              f" · 본문={resp.text[:300]!r}")
        return False
    if j.get("ok"):
        print(f"[출근] {kind} ok=True")
        return True
    print(f"[출근] {kind} ok=False · error_code={j.get('error_code')}"
          f" · description={j.get('description')!r}")
    print(f"[출근] 사용 중인 chat_id={TARGET_CHAT_ID}")
    return False
 
 
def send_photo(path, caption):
    import os as _os
    size = _os.path.getsize(path) if _os.path.exists(path) else -1
    print(f"[출근] 이미지 크기 {size:,} bytes")
    try:
        with open(path, "rb") as f:
            r = requests.post(f"{API}/sendPhoto",
                              data={"chat_id": TARGET_CHAT_ID, "caption": caption},
                              files={"photo": f}, timeout=(10, 120))
    except Exception as exc:
        print(f"[출근] sendPhoto 전송 예외: {exc!r}")
        return False
    return _report("sendPhoto", r)
 
 
def send_text(text):
    try:
        r = requests.post(f"{API}/sendMessage",
                          json={"chat_id": TARGET_CHAT_ID, "text": text},
                          timeout=(10, 60))
    except Exception as exc:
        print(f"[출근] sendMessage 전송 예외: {exc!r}")
        return False
    return _report("sendMessage", r)
 
 
def main():
    now = datetime.now(KST)
    today = now.strftime("%Y-%m-%d")
    wk = "월화수목금토일"[now.weekday()]
    today_label = f"{now.month}/{now.day} ({wk})"
 
    messages = fetch_messages()
    print(f"[출근] 수집 메시지 {len(messages)}건")
    if not messages:
        print("가져온 메시지가 없습니다.")
        return
 
    parsed = summarize(messages, today)
    if not parsed or "stores" not in parsed:
        print("[출근] AI 정리 실패 - 게시하지 않습니다.")
        return
    data = {}
    for it in parsed["stores"]:
        name = str(it.get("store", "")).strip()
        if name:
            data[name] = {"work": [str(x).strip() for x in it.get("work", []) if str(x).strip()],
                          "off": [str(x).strip() for x in it.get("off", []) if str(x).strip()],
                          "note": str(it.get("note", "")).strip()}
    print(f"[출근] 등록 매장 {len(data)}곳")
 
    managers = load_managers()
    print(f"[출근] 점장 명단 {len(managers)}개 매장")
 
    annis = anniversaries(now.date())
    if annis:
        print(f"[출근] 입사 기념일 {len(annis)}명: "
              + ", ".join(f"{s} {n}({l})" for s, n, l in annis))
 
    try:
        path, summary = render_image(data, today_label, managers=managers, annis=annis)
        if send_photo(path, f"📋 출근 현황 · {summary}"):
            print("출근 현황 이미지 게시 완료")
            return
    except Exception as e:
        print(f"[출근] 이미지 생성 실패: {e!r}")
 
    # 폴백: 텍스트 게시
    send_text(build_fallback_text(data, today_label))
    print("출근 현황 텍스트(폴백) 게시 완료")
 
 
if __name__ == "__main__":
    main()
 
