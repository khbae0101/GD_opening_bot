"""
오늘의 판매스타 (시상봇, 1회 실행용)
─────────────────────────────────────────────
- 강동요정봇과 같은 봇/같은 방(실적 공유 방)에서 동작.
- 마감 시간(저녁)에 cron-job.org가 호출해서 1회 실행.
- 그날 방에 올라온 판매 실적을 읽어 직원별 '휴대폰 개통 건수'를 집계.
  · 휴대폰 모델명으로 시작하는 개통 줄 1개 = 1건 (한 사람이 여러 건 가능)
  · 유선(에센스/베이직/모든G/MITT/GTT 등), 2nd기기(워치·패드·버즈), 약정갱신은 제외
- 판매왕(그날 최고 건수 전원) + 럭키추첨(1건 이상 공유자 중 랜덤 1명) 발표.
 
비밀값(Secrets): TELEGRAM_TOKEN_3 / TARGET_CHAT_ID_3 / ANTHROPIC_API_KEY
* 방의 실적 글을 읽어야 하므로 강동요정봇의 Group Privacy를 꺼야 합니다.
"""
 
import os
import csv
import json
import random
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo
 
import requests
import anthropic
 
TELEGRAM_TOKEN    = os.environ["TELEGRAM_TOKEN_3"]
CHAT_ID           = int(os.environ["TARGET_CHAT_ID_3"])
ANTHROPIC_API_KEY = os.environ["ANTHROPIC_API_KEY"]
MODEL             = "claude-sonnet-4-6"
KST = ZoneInfo("Asia/Seoul")
TG  = f"https://api.telegram.org/bot{TELEGRAM_TOKEN}"
 
AWARD_CSV = "data/award_results.csv"   # 일별 시상 결과
SALES_CSV = "data/daily_sales.csv"     # 일별 개인 실적
 
# ── 시상 설정 ─────────────────────────────────────
LUCKY_MIN,  LUCKY_MAX,  LUCKY_STEP  = 10000, 20000, 1000    # 개인 럭키 (1명)
SLUCKY_MIN, SLUCKY_MAX, SLUCKY_STEP = 20000, 50000, 5000    # 매장 럭키 (1매장)
MILESTONE_STEP = 10     # 달성 축하 단위(건)
STEADY_STEP    = 10     # 꾸준왕 단위(실적 발생일수)
NOSALE_STEP    = 5      # 무실적 응원 단위(일요일 제외 일수)
CLEANWEEK_PRIZE = 100000   # 클린위크(월~토 무실적 0일) 매장 시상금
NOTICE = "내일도 1인 1건! 우리 지사 파이팅 💪"
 
# ── 상권 구성 (표시는 축약명) ─────────────────────
AREAS = {
    "광구": ["도농로", "구리리맥스", "자양번영로", "다산신도시", "건대입구역",
             "면목역", "상봉역", "외대역", "금호동", "진접"],
    "경북": ["중계아울렛", "수유", "의정부로데오", "옥정신도시", "삼양로",
             "먹골역", "지행역", "상계역", "양주덕계"],
    "강원": ["동해천곡", "석사", "강릉임당", "원주무실", "단구",
             "강릉유천", "홍천중앙", "후평", "온의"],
}
SHORT = {
    "도농로": "도농", "구리리맥스": "구리", "자양번영로": "자양", "다산신도시": "다산",
    "건대입구역": "건대", "면목역": "면목", "상봉역": "상봉", "외대역": "외대",
    "금호동": "금호", "진접": "진접",
    "중계아울렛": "중계", "수유": "수유", "의정부로데오": "의정부", "옥정신도시": "옥정",
    "삼양로": "삼양", "먹골역": "먹골", "지행역": "지행", "상계역": "상계", "양주덕계": "덕계",
    "동해천곡": "동해", "석사": "석사", "강릉임당": "임당", "원주무실": "무실", "단구": "단구",
    "강릉유천": "유천", "홍천중앙": "홍천", "후평": "후평", "온의": "온의",
}
STORE_AREA = {s: a for a, ss in AREAS.items() for s in ss}
 
STEADY_CHEERS = {10: "꾸준함이 실력입니다!", 20: "전설의 꾸준함!", 30: "경이로운 기록!"}
NOSALE_CHEERS = ["내일 첫 테이프 끊어봐요!", "곧 터질 거예요, 파이팅!",
                 "슬슬 시동 걸어볼까요?", "내일은 꼭 1건! 응원해요!"]
 
 
def short(store):
    return SHORT.get(store, store)
 
 
def split_name(full):
    """'의정부로데오 김해진' → ('의정부로데오', '김해진')"""
    return full.split(" ", 1) if " " in full else ("", full)
 
 
COUNT_SYSTEM = ("너는 휴대폰 판매 실적 보고를 분석해 직원별 개통 건수를 세는 도구야. "
                "계산·판단은 머릿속으로만 하고, 설명·메모·중간 과정·머리말을 절대 출력하지 마. "
                "응답은 '{'로 시작해 '}'로 끝나는 JSON 객체 하나뿐이어야 한다.")
 
 
def fetch_messages():
    """방의 새 메시지(텍스트)를 가져오면서 동시에 확인 처리(offset 전진)."""
    texts, offset = [], None
    n_updates = 0          # 받은 업데이트 총 개수
    chat_seen = {}         # 어떤 방에서 몇 건 왔는지 (방ID 변경 감지용)
    while True:
        params = {"timeout": 0, "limit": 100}
        if offset is not None:
            params["offset"] = offset
        try:
            r = requests.get(f"{TG}/getUpdates", params=params, timeout=30).json()
        except Exception as e:
            print(f"[시상] getUpdates 호출 실패: {e!r}")
            break
        if not r.get("ok", False):
            print(f"[시상] 텔레그램 API 오류: {r.get('error_code')} {r.get('description')!r}")
            break
        batch = r.get("result", [])
        if not batch:
            break
        n_updates += len(batch)
        for u in batch:
            offset = u["update_id"] + 1
            m = u.get("message") or u.get("channel_post")
            if not m or not m.get("text"):
                continue
            cid = m["chat"]["id"]
            chat_seen[cid] = chat_seen.get(cid, 0) + 1
            if cid != CHAT_ID:
                continue
            texts.append(m["text"])
    print(f"[시상] 업데이트 {n_updates}건 수신 · 방별 분포 {chat_seen} "
          f"· 대상방({CHAT_ID}) 메시지 {len(texts)}건")
    if chat_seen and not texts:
        print("[시상] ⚠️ 메시지는 있는데 대상 방 ID와 일치하는 게 없습니다. "
              "방이 슈퍼그룹으로 전환돼 ID가 바뀌었을 수 있으니 TARGET_CHAT_ID_3를 확인하세요.")
    return texts
 
 
def looks_like_report(t):
    """실적 보고로 보이는 메시지만 추려서 토큰 절약(잡담 제외)."""
    keys = ["기변", "신규", "번이", "MNP", "mnp", "공시", "요할", "심플"]
    return any(k in t for k in keys)
 
 
def load_roster():
    """명단(data/roster.csv) → (전체 '매장 이름' 목록, 점장 '매장 이름' 집합)."""
    try:
        with open("data/roster.csv", newline="", encoding="utf-8-sig") as f:
            rows = list(csv.reader(f))
    except Exception as e:
        print(f"[시상] 명단 로드 실패({e!r}) - 명단 없이 진행")
        return [], set()
    names, mgrs = [], set()
    for r in rows[1:]:
        if len(r) < 2 or not r[0].strip():
            continue
        full = f"{r[0].strip()} {r[1].strip()}"
        names.append(full)
        if len(r) >= 3 and r[2].strip() == "점장":
            mgrs.add(full)
    return names, mgrs
 
 
def _extract_json(text):
    """앞뒤에 설명글이 섞여 있어도 counts가 든 JSON 객체만 찾아 파싱."""
    if not text:
        return None
    idx = text.find('"counts"')
    if idx == -1:
        return None
    start = text.rfind("{", 0, idx)      # counts를 감싸는 바깥 중괄호
    if start == -1:
        return None
    depth = 0
    for i in range(start, len(text)):
        if text[i] == "{":
            depth += 1
        elif text[i] == "}":
            depth -= 1
            if depth == 0:
                try:
                    return json.loads(text[start:i + 1])
                except Exception:
                    return None
    return None
 
 
def count_sales(reports):
    """AI로 직원별 휴대폰 개통 건수를 집계해 dict로 반환."""
    body = "\n\n──\n\n".join(reports)
    roster, _mgrs = load_roster()
    roster_block = ""
    if roster:
        roster_block = f"""
# 표준 인원 명단 ("매장 이름" — 이 표기가 기준이야)
{chr(10).join(roster)}
 
# 명단 매칭 규칙
- 보고의 점명·이름을 위 명단과 매칭해서, 기록은 반드시 명단의 "매장 이름" 표기로 통일한다.
  (예: "의로 김해진", "의정부 김해진" → "의정부로데오 김해진")
- 점명이 줄임말·다른 표기라도 이름이 명단에 한 명뿐이면 그 사람으로 확정한다.
- 명단에 없는 사람(신규 입사 등)은 보고된 "점명 이름" 그대로 기록한다(빼지 마).
"""
    prompt = f"""아래는 오늘 단체방에 올라온 휴대폰 판매 실적 보고들이야. 직원별 '휴대폰 개통 건수'를 세줘.
{roster_block}
# 세는 규칙
- 보통 "점명 이름"(예: 중계 전우진) 줄 다음에 개통 내역 줄이 온다.
- 개통 내역 줄은 "모델명/개통유형/요금제/부가/보험/카드/리본" 형식이고, 휴대폰 모델명으로 시작한다(A175, S948, F966, AIP17, M366, ZTE 클래식폴더 등).
  구분자는 "/"가 보통이지만 "+"로 쓰기도 한다(예: "폴드8+기변+120k+패드+리본"). 둘 다 개통 줄로 인정한다.
- 신모델 코드명/별칭도 동일하게 모델명으로 인정한다:
  · F971 = 폴드8 (표기: "폴드8", "갤럭시 폴드8")
  · F976 = 폴드8 울트라 (표기: "폴드8 울트라", "폴드8울트라", "폴드8 Ultra" 등 띄어쓰기 유무 무관)
  · F776 = 플립8 (표기: "플립8", "갤럭시 플립8")
- 모델명만 단독으로 한 줄에 있고 개통 조건이 다음 줄에 이어져도 그 묶음 전체를 1건으로 센다
  (예: "폴드8 울트라" 줄 + 다음 줄 "120K+워치+에어팟프로" → 합쳐서 1건).
- 휴대폰 모델명으로 시작하는 줄 1개 = 1건. 한 사람 아래 개통 줄이 2~3개면 그만큼 여러 건으로 센다. 신모델도 동일하게 1건으로 센다.
 
# 세지 않는 안내 문구 (매우 중요)
- "폴더블8"은 시리즈 행사 명칭이지 모델명이 아니다. "폴더블8 사전예약", "폴더블8 사전예약 (락인완)" 같은 줄은
  개통 건이 아니라 안내 머리말이므로 절대 세지 마라. ("폴드8"과 "폴더블8"은 다른 말이다.)
- "사전예약", "프리세일즈", "락인완", "예약", "상담예정", "가망" 등 실적이 아닌 안내·상태 표기 줄도 세지 마라.
- 이런 안내 줄 바로 아래에 실제 개통 줄이 오면, 그 개통 줄만 센다(안내 줄은 0건).
- 다음도 세지 마라(제외): 유선상품(에센스, 베이직, 모든G, 인터넷/TV, MITT, GTT, 신동, 원스톱, ITTM 등), 2nd기기(워치·패드·버즈, '2ND'/'세컨' 표기), 약정갱신만 있는 줄.
- 휴대폰 신규/기변/번이/MNP/공시 개통만 센다.
- 맨 앞 글 작성자 줄("...님:" 또는 "이름:")은 무시하고 "점명 이름"으로 집계한다.
- 이름 줄 없이 개통 줄만 이어지면 바로 위 사람 것으로 본다.
 
# 출력 형식 (매우 중요)
- 파싱 메모·판단 근거·중간 계산을 절대 쓰지 마라. 오직 아래 JSON 하나만 출력한다.
- 응답의 첫 글자는 "{", 마지막 글자는 "}" 여야 한다. 코드블록(```)도 쓰지 마라.
{{"counts": [{{"name": "점명 이름", "count": 건수}}, ...]}}
 
# 보고 내용
{body}
"""
    client = anthropic.Anthropic(api_key=ANTHROPIC_API_KEY)
    data = None
    for attempt in (1, 2):
        content = prompt if attempt == 1 else (
            "[재시도] 직전 응답에 설명·메모가 섞여 파싱에 실패했다. "
            "이번에는 어떤 설명도 쓰지 말고 JSON 객체 하나만 출력하라.\n\n" + prompt)
        try:
            resp = client.messages.create(
                model=MODEL, max_tokens=8000, temperature=0,
                system=COUNT_SYSTEM,
                messages=[{"role": "user", "content": content}],
            )
        except Exception as e:
            print(f"[시상] AI 호출 실패({attempt}차): {e!r}")
            continue
        text = (resp.content[0].text if resp.content else "").strip()
        if resp.stop_reason == "max_tokens":
            print(f"[시상] 경고: 응답이 길이 제한에 걸림({attempt}차)")
        data = _extract_json(text)
        if data is not None:
            if attempt == 2:
                print("[시상] 재시도로 집계 성공")
            break
        print(f"[시상] JSON 추출 실패({attempt}차) · 응답 일부: {text[:200]!r}")
    if data is None:
        print("[시상] 집계 실패 - 게시하지 않습니다.")
        return {}
    result = {}
    for it in data.get("counts", []):
        name = str(it.get("name", "")).strip()
        try:
            cnt = int(it.get("count", 0))
        except Exception:
            cnt = 0
        if name and cnt > 0:
            result[name] = result.get(name, 0) + cnt
    return result
 
 
def load_history(today_str):
    """이번 달, 오늘 이전의 개인 실적 이력 [(날짜, '매장 이름', 건수)]."""
    hist = []
    try:
        with open(SALES_CSV, newline="", encoding="utf-8-sig") as f:
            rows = list(csv.reader(f))
    except FileNotFoundError:
        return hist
    month = today_str[:7]
    for r in rows[1:]:
        if len(r) >= 3 and r[0][:7] == month and r[0] < today_str:
            try:
                hist.append((r[0], r[1].strip(), int(r[2])))
            except ValueError:
                pass
    return hist
 
 
def workdays(d1, d2):
    """d1~d2(포함) 중 일요일을 뺀 날짜 수."""
    n, d = 0, d1
    while d <= d2:
        if d.weekday() != 6:
            n += 1
        d += timedelta(days=1)
    return n
 
 
def area_status(counts, hist, today_str):
    """상권별 무실적 매장 현황 + 이달 일소 누적일수."""
    # 오늘 실적이 발생한 매장
    today_stores = {split_name(n)[0] for n, c in counts.items() if c > 0}
    # 과거 날짜별 실적 발생 매장
    by_day = {}
    for d, name, c in hist:
        if c > 0:
            by_day.setdefault(d, set()).add(split_name(name)[0])
 
    rows, clears = [], {}
    for area, stores in AREAS.items():
        past = sum(1 for d, ss in by_day.items()
                   if all(s in ss for s in stores))
        miss = [s for s in stores if s not in today_stores]
        done = not miss
        clears[area] = past + (1 if done else 0)
        rows.append({"area": area, "miss": miss, "done": done,
                     "total": len(stores), "clear_days": clears[area]})
    # 일소 누적 순위(공동 순위)
    order = sorted({v for v in clears.values()}, reverse=True)
    for r in rows:
        r["rank"] = order.index(r["clear_days"]) + 1
    return rows
 
 
def clean_week(counts, hist, today):
    """토요일 기준, 이번 주(월~토) 무실적 0일 매장 목록."""
    if today.weekday() != 5:          # 토요일에만 발표
        return []
    monday = today - timedelta(days=5)
    # 날짜별 실적 발생 매장
    by_day = {}
    for d, name, c in hist:
        if c > 0:
            by_day.setdefault(d, set()).add(split_name(name)[0])
    by_day[today.strftime("%Y-%m-%d")] = {
        split_name(n)[0] for n, c in counts.items() if c > 0}
 
    week_days = [(monday + timedelta(i)).strftime("%Y-%m-%d") for i in range(6)]
    have = [d for d in week_days if d in by_day]
    if len(have) < 6:                 # 주중 데이터가 빠지면 판정 보류
        print(f"[클린위크] 주중 기록 {len(have)}/6일 - 판정 생략")
        return []
    all_stores = [s for ss in AREAS.values() for s in ss]
    return sorted(s for s in all_stores
                  if all(s in by_day[d] for d in week_days))
 
 
def compute_result(counts):
    now = datetime.now(KST)
    today_str = now.strftime("%Y-%m-%d")
    today = now.date()
    first = today.replace(day=1)
    people = list(counts.keys())
    top = max(counts.values())
    kings = [n for n, c in counts.items() if c == top]
 
    hist = load_history(today_str)
    roster, mgrs = load_roster()
 
    # 개인 누적/참여일수(오늘 이전)
    prev_cum, prev_days, last_sale = {}, {}, {}
    for d, name, c in hist:
        prev_cum[name] = prev_cum.get(name, 0) + c
        if c > 0:
            prev_days.setdefault(name, set()).add(d)
            if name not in last_sale or d > last_sale[name]:
                last_sale[name] = d
 
    # ① 럭키추첨 — 개인 1명 / 매장 1곳
    lucky_person = random.choice(people)
    lucky_prize = random.randrange(LUCKY_MIN, LUCKY_MAX + 1, LUCKY_STEP)
    stores_today = sorted({split_name(n)[0] for n, c in counts.items() if c > 0})
    lucky_store = random.choice(stores_today) if stores_today else None
    store_prize = (random.randrange(SLUCKY_MIN, SLUCKY_MAX + 1, SLUCKY_STEP)
                   if lucky_store else 0)
 
    # ② 상권별 무실적 현황
    areas = area_status(counts, hist, today_str)
 
    # ③ 매장 완전체 (명단 기준 전원 실적)
    full_stores = []
    if roster:
        by_store = {}
        for full in roster:
            by_store.setdefault(split_name(full)[0], []).append(full)
        for store, members in by_store.items():
            if members and all(m in counts for m in members):
                full_stores.append((store, [split_name(m)[1] for m in members]))
 
    # ④ 달성 축하 (10건 단위, 같은 단계끼리 묶음)
    ms = {}
    for name, c in counts.items():
        prev = prev_cum.get(name, 0)
        new_total = prev + c
        step = MILESTONE_STEP
        if new_total // step > prev // step:
            ms.setdefault((new_total // step) * step, []).append(name)
 
    # ⑤ 꾸준왕 (참여일수 10일 단위, 묶음)
    st = {}
    for name in counts:
        d_cnt = len(prev_days.get(name, set())) + 1
        if d_cnt >= STEADY_STEP and d_cnt % STEADY_STEP == 0:
            st.setdefault(d_cnt, []).append(name)
 
    # ⑥ 무실적 응원 (5일 단위 · 이달 실적 있는 사람만 · 점장 제외 · 묶음)
    ns = {}
    for name in roster:
        if name in mgrs or name in counts:
            continue
        if name not in last_sale:      # 이달 실적 전무 → 대상 제외
            continue
        gap_from = datetime.strptime(last_sale[name], "%Y-%m-%d").date() + timedelta(days=1)
        streak = workdays(gap_from, today)
        if streak >= NOSALE_STEP and streak % NOSALE_STEP == 0:
            ns.setdefault(streak, []).append(name)
 
    return {
        "date": today_str, "md": f"{now.month}/{now.day}",
        "people": people, "total": sum(counts.values()),
        "top": top, "kings": kings, "counts": counts,
        "lucky_person": lucky_person, "lucky_prize": lucky_prize,
        "lucky_store": lucky_store, "store_prize": store_prize,
        "areas": areas, "full_stores": full_stores,
        "milestones": ms, "steadies": st, "nosales": ns,
        "clean_week": clean_week(counts, hist, today),
    }
 
 
def _fmt(names):
    """'매장 이름' 목록 → '축약 이름' 나열."""
    out = []
    for n in names:
        s, nm = split_name(n)
        out.append(f"{short(s)} {nm}" if s else nm)
    return ", ".join(out)
 
 
def build_message(res):
    L = [f"⭐ 오늘의 판매스타 ({res['md']}) ⭐", ""]
    L.append(f"오늘 실적 공유에 참여해주신 {len(res['people'])}분, 총 {res['total']}건 👏")
 
    # ── 상권별 무실적 매장 (최상단)
    L += ["", "📍 상권별 무실적 매장"]
    for a in res["areas"]:
        if a["done"]:
            L.append(f"  · {a['area']} — 🎊🎊 전 매장 무실적 일소!! 🎊🎊")
            L.append(f"      {a['total']}개 매장 전원 실적 발생, 단 한 곳도 빠짐없이 해냈습니다!!")
            L.append(f"      이거 정말 어려운 겁니다. {a['area']} 상권 최고예요!! 🔥🔥🔥")
            L.append(f"      (이달 {a['clear_days']}일째 일소 · 상권 {a['rank']}위)")
        else:
            miss = ", ".join(short(s) for s in a["miss"])
            L.append(f"  · {a['area']} — {len(a['miss'])}점 ({miss})")
 
    # ── 판매왕
    L += ["", f"👑 오늘의 판매왕 ({res['top']}건)"]
    for k in res["kings"]:
        s, nm = split_name(k)
        L.append(f"  · {short(s)} {nm}")
    L.append("정말 대단해요! 🔥" if len(res["kings"]) == 1 else "모두 정말 대단해요! 🔥")
 
    # ── 럭키추첨
    L += ["", "🎰 오늘의 럭키 추첨 (당일 실적발생 개인/매장 대상)"]
    s, nm = split_name(res["lucky_person"])
    jp = " 🎊 잭팟!" if res["lucky_prize"] >= LUCKY_MAX else ""
    L.append(f"  👤 개인 — {short(s)} {nm} · {res['lucky_prize']:,}원{jp} 🎉")
    if res["lucky_store"]:
        sj = " 🎊 잭팟!" if res["store_prize"] >= SLUCKY_MAX else ""
        L.append(f"  🏪 매장 — {short(res['lucky_store'])} · {res['store_prize']:,}원{sj} 🎉")
    L.append("축하드려요!")
 
    # ── 매장 완전체
    if res["full_stores"]:
        L.append("")
        L.append("🎖 매장 완전체")
        for store, names in res["full_stores"]:
            L.append(f"  🎊 오늘 {short(store)}, 전 직원 실적 달성!! 🎊")
            L.append(f"  {len(names)}명 전원이 한 명도 빠짐없이 판매했습니다. 완벽한 팀워크예요!! 🔥🔥")
            L.append(f"  ({' · '.join(names)})")
 
    # ── 클린위크 (토요일)
    if res.get("clean_week"):
        L += ["", "✨ 이번 주 클린위크 달성! (월~토 무실적 0일)"]
        for s in res["clean_week"]:
            L.append(f"  🏆 {short(s)} — {CLEANWEEK_PRIZE:,}원 🎊")
        L.append(f"  6일 내내 단 하루도 빠짐없이! 정말 대단합니다 🔥🔥")
 
    # ── 달성 축하
    if res["milestones"]:
        L += ["", "🎯 달성 축하"]
        for m in sorted(res["milestones"], reverse=True):
            L.append(f"  · {m}건 — {_fmt(res['milestones'][m])}")
 
    # ── 꾸준왕
    if res["steadies"]:
        L += ["", "🏅 꾸준왕"]
        for d in sorted(res["steadies"], reverse=True):
            cheer = STEADY_CHEERS.get(d, "대단한 꾸준함이에요!")
            L.append(f"  · {d}일째 참여 — {_fmt(res['steadies'][d])}  {cheer}")
 
    # ── 무실적 응원
    if res["nosales"]:
        L += ["", "💪 응원합니다"]
        for d in sorted(res["nosales"], reverse=True):
            L.append(f"  · {d}일째 — {_fmt(res['nosales'][d])}")
        L.append(f"  {random.choice(NOSALE_CHEERS)}")
 
    L += ["", NOTICE]
    return "\n".join(L)
 
 
def _upsert_csv(path, header, rows, date_str):
    """같은 날짜 줄은 지우고 새로 기록(중복 방지). 날짜순 정렬, Excel용 utf-8-sig."""
    os.makedirs(os.path.dirname(path), exist_ok=True)
    existing = []
    if os.path.exists(path):
        with open(path, newline="", encoding="utf-8-sig") as f:
            r = list(csv.reader(f))
        existing = [row for row in r[1:] if row and row[0] != date_str]
    with open(path, "w", newline="", encoding="utf-8-sig") as f:
        w = csv.writer(f)
        w.writerow(header)
        w.writerows(sorted(existing + rows, key=lambda r: r[0]))
 
 
def write_csv(res):
    d = res["date"]
    lucky_txt = f"{res['lucky_person']}({res['lucky_prize']:,}원)"
    store_txt = (f"{res['lucky_store']}({res['store_prize']:,}원)"
                 if res["lucky_store"] else "")
    _upsert_csv(
        AWARD_CSV, ["날짜", "판매왕", "판매왕건수", "럭키추첨", "매장럭키"],
        [[d, ", ".join(res["kings"]), res["top"], lucky_txt, store_txt]], d,
    )
    # 개인 실적 (사람별 한 줄)
    rows = [[d, name, cnt] for name, cnt in sorted(res["counts"].items(),
                                                   key=lambda x: -x[1])]
    _upsert_csv(SALES_CSV, ["날짜", "점명이름", "건수"], rows, d)
 
 
def main():
    raw = fetch_messages()
    msgs = [t for t in raw if looks_like_report(t)]
    print(f"[시상] 실적 보고로 인식 {len(msgs)}건 / 전체 {len(raw)}건")
    if not msgs:
        if raw:
            print("[시상] 메시지는 있으나 실적 보고 형식이 아닙니다. 예시: "
                  f"{raw[0][:120]!r}")
        print("오늘 공유된 실적이 없습니다. 게시하지 않습니다.")
        return
    counts = count_sales(msgs)
    if not counts:
        print("집계 결과가 비어 있습니다. 게시하지 않습니다.")
        return
    res = compute_result(counts)
    text = build_message(res)
    requests.post(f"{TG}/sendMessage",
                  json={"chat_id": CHAT_ID, "text": text}, timeout=30)
    write_csv(res)   # 데이터 기록 (GitHub에 저장됨)
    print(f"시상 게시 완료 · 참여 {len(counts)}명 / 총 {sum(counts.values())}건 · CSV 기록 완료")
 
 
if __name__ == "__main__":
    main()
 
