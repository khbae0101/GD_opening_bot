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
 
COUNT_SYSTEM = ("너는 휴대폰 판매 실적 보고를 분석해 직원별 개통 건수를 세는 도구야. "
                "반드시 지정된 JSON만 출력하고 다른 말은 하지 마.")
 
 
def fetch_messages():
    """방의 새 메시지(텍스트)를 가져오면서 동시에 확인 처리(offset 전진)."""
    texts, offset = [], None
    while True:
        params = {"timeout": 0, "limit": 100}
        if offset is not None:
            params["offset"] = offset
        r = requests.get(f"{TG}/getUpdates", params=params, timeout=30).json()
        batch = r.get("result", [])
        if not batch:
            break
        for u in batch:
            offset = u["update_id"] + 1
            m = u.get("message") or u.get("channel_post")
            if not m or not m.get("text"):
                continue
            if m["chat"]["id"] != CHAT_ID:
                continue
            texts.append(m["text"])
    return texts
 
 
def looks_like_report(t):
    """실적 보고로 보이는 메시지만 추려서 토큰 절약(잡담 제외)."""
    keys = ["기변", "신규", "번이", "MNP", "mnp", "공시", "요할", "심플"]
    return any(k in t for k in keys)
 
 
def load_roster():
    """표준 인원 명단(data/roster.csv)을 읽어 '매장 이름' 목록으로 반환. 없으면 빈 리스트."""
    path = "data/roster.csv"
    try:
        with open(path, newline="", encoding="utf-8-sig") as f:
            rows = list(csv.reader(f))
        return [f"{r[0].strip()} {r[1].strip()}" for r in rows[1:] if len(r) >= 2 and r[0].strip()]
    except Exception as e:
        print(f"[시상] 명단 로드 실패({e!r}) - 명단 없이 진행")
        return []
 
 
def count_sales(reports):
    """AI로 직원별 휴대폰 개통 건수를 집계해 dict로 반환."""
    body = "\n\n──\n\n".join(reports)
    roster = load_roster()
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
- 휴대폰 모델명으로 시작하는 줄 1개 = 1건. 한 사람 아래 개통 줄이 2~3개면 그만큼 여러 건으로 센다.
- 다음은 세지 마라(제외): 유선상품(에센스, 베이직, 모든G, 인터넷/TV, MITT, GTT, 신동, 원스톱, ITTM 등), 2nd기기(워치·패드·버즈, '2ND'/'세컨' 표기), 약정갱신만 있는 줄.
- 휴대폰 신규/기변/번이/MNP/공시 개통만 센다.
- 맨 앞 글 작성자 줄("...님:" 또는 "이름:")은 무시하고 "점명 이름"으로 집계한다.
- 이름 줄 없이 개통 줄만 이어지면 바로 위 사람 것으로 본다.
 
# 출력 형식 (JSON만, 머리말·설명·코드블록 없이)
{{"counts": [{{"name": "점명 이름", "count": 건수}}, ...]}}
 
# 보고 내용
{body}
"""
    client = anthropic.Anthropic(api_key=ANTHROPIC_API_KEY)
    resp = client.messages.create(
        model=MODEL, max_tokens=4000,
        system=COUNT_SYSTEM,
        messages=[{"role": "user", "content": prompt}],
    )
    text = (resp.content[0].text if resp.content else "").strip()
    if "```" in text:
        text = text.split("```")[1].replace("json", "", 1).strip() if text.count("```") >= 2 else text
    if "{" in text and "}" in text:
        text = text[text.index("{"):text.rindex("}") + 1]
    else:
        # JSON이 아예 없으면(빈 응답 등) 그날은 집계 없음으로 처리(크래시 방지)
        print(f"[시상] AI 응답에 JSON이 없어 집계를 건너뜁니다. 응답 일부: {text[:200]!r}")
        return {}
    try:
        data = json.loads(text)
    except Exception as e:
        print(f"[시상] JSON 파싱 실패: {e!r} · 응답 일부: {text[:200]!r}")
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
 
 
def load_month_history(today_str):
    """이번 달, 오늘 이전 날짜의 개인 실적 이력 [(날짜, 이름, 건수)]."""
    hist = []
    try:
        with open(SALES_CSV, newline="", encoding="utf-8-sig") as f:
            rows = list(csv.reader(f))
        month = today_str[:7]
        for r in rows[1:]:
            if len(r) >= 3 and r[0][:7] == month and r[0] < today_str:
                try:
                    hist.append((r[0], r[1].strip(), int(r[2])))
                except ValueError:
                    pass
    except FileNotFoundError:
        pass
    return hist
 
 
def workdays(d1, d2):
    """d1~d2(포함) 사이 일요일을 뺀 날짜 수. d1이 d2보다 뒤면 0."""
    n, d = 0, d1
    while d <= d2:
        if d.weekday() != 6:
            n += 1
        d += timedelta(days=1)
    return n
 
 
NOSALE_CHEERS = ["내일 첫 테이프 끊어봐요!", "곧 터질 거예요, 파이팅!",
                 "내일은 꼭 1건! 응원해요!", "슬슬 시동 걸어볼까요?"]
 
STEADY_CHEERS = {5: "좋은 페이스예요!", 10: "꾸준함이 실력입니다!",
                 15: "이달의 개근왕 예약!", 20: "전설의 꾸준함!",
                 25: "경이로운 기록입니다!"}
 
 
def compute_extras(counts, now):
    """월 단위 프로모션 계산: 5건 달성 / 골든벨 / 데뷔 / 컴백 / 무실적 응원."""
    today_str = now.strftime("%Y-%m-%d")
    today = now.date()
    first = today.replace(day=1)
    hist = load_month_history(today_str)
    roster = load_roster()
    rset = set(roster)
 
    prev_cum, last_sale = {}, {}
    for d, name, c in hist:
        prev_cum[name] = prev_cum.get(name, 0) + c
        if c > 0 and (name not in last_sale or d > last_sale[name]):
            last_sale[name] = d
 
    # ① 5건 단위 달성 (오늘 실적으로 5의 배수를 새로 넘은 사람)
    milestones = []
    for name, c in counts.items():
        prev = prev_cum.get(name, 0)
        new = prev + c
        if new // 5 > prev // 5:
            milestones.append((name, (new // 5) * 5))
    milestones.sort(key=lambda x: -x[1])
 
    # ② 지사 골든벨 (월 누적 100건 단위 돌파) — 현재 미운영(공유 누락으로 실제와 차이).
    #    다시 켜려면 아래 GOLDENBELL_ON 을 True 로 바꾸면 됩니다.
    GOLDENBELL_ON = False
    total_prev = sum(prev_cum.values())
    total_new = total_prev + sum(counts.values())
    goldenbell = None
    if GOLDENBELL_ON and total_new // 100 > total_prev // 100:
        threshold = (total_prev // 100 + 1) * 100
        running = total_prev
        for name, c in counts.items():   # 보고 순서(근사)
            running += c
            if running >= threshold:
                goldenbell = (threshold, name)
                break
 
    # ③ 데뷔 축하 (명단에 없고 이번 달 첫 등장 = 신규 입사 추정, 최초 1회)
    seen_before = set(n for _, n, _ in hist)
    debuts = [n for n in counts if rset and n not in rset and n not in seen_before]
 
    # ④ 컴백 축하 (일요일 제외 5일 이상 무실적이었다가 오늘 실적 발생, 명단 직원만)
    comebacks = []
    for name in counts:
        if rset and name not in rset:
            continue   # 명단 외(신규)는 데뷔로 처리
        if name in last_sale:
            last = datetime.strptime(last_sale[name], "%Y-%m-%d").date()
            gap_from = last + timedelta(days=1)
        else:
            if not hist:
                continue   # 이력 자체가 없으면(월초 등) 판단 불가
            gap_from = first
        gap = workdays(gap_from, today - timedelta(days=1))
        if gap >= 5:
            comebacks.append((name, workdays(gap_from, today)))
 
    # ⑤ 무실적 응원 (명단 직원 중 오늘도 실적 없음, 일요일 제외 5일 단위 그날만)
    nosales = []
    if roster and hist:   # 명단·이력 있어야 의미 있음
        for name in roster:
            if name in counts:
                continue
            if name in last_sale:
                gap_from = datetime.strptime(last_sale[name], "%Y-%m-%d").date() + timedelta(days=1)
            else:
                gap_from = first
            streak = workdays(gap_from, today)
            if streak >= 5 and streak % 5 == 0:
                nosales.append((name, streak))
        nosales.sort(key=lambda x: -x[1])
 
    # ⑥ 꾸준왕 (이달 참여일수가 오늘로 5일 단위에 도달한 사람)
    steadies = []
    part_days = {}
    for d0, name, c in hist:
        if c > 0:
            part_days.setdefault(name, set()).add(d0)
    for name in counts:
        days = len(part_days.get(name, set())) + 1   # 오늘 포함
        if days >= 5 and days % 5 == 0:
            steadies.append((name, days))
    steadies.sort(key=lambda x: -x[1])
 
    # ⑦ 매장 완전체 (명단 기준 매장 전원이 오늘 1건 이상)
    full_stores = []
    if roster:
        by_store = {}
        for full in roster:
            store = full.split(" ", 1)[0]
            by_store.setdefault(store, []).append(full)
        for store, members in by_store.items():
            if members and all(m in counts for m in members):
                names_only = [m.split(" ", 1)[1] for m in members]
                full_stores.append((store, names_only))
 
    return {"milestones": milestones, "goldenbell": goldenbell,
            "debuts": debuts, "comebacks": comebacks, "nosales": nosales,
            "steadies": steadies, "full_stores": full_stores}
 
 
def compute_result(counts):
    now = datetime.now(KST)
    people = list(counts.keys())
    top = max(counts.values())
    kings = [n for n, c in counts.items() if c == top]
    # 토요일은 럭키추첨 2명(참여자가 적으면 있는 만큼), 그 외 1명
    n_lucky = min(2 if now.weekday() == 5 else 1, len(people))
    luckies = random.sample(people, n_lucky)
    res = {
        "date": now.strftime("%Y-%m-%d"),
        "md": f"{now.month}/{now.day}",
        "people": people, "total": sum(counts.values()),
        "top": top, "kings": kings, "luckies": luckies, "counts": counts,
        "sat": now.weekday() == 5,
    }
    res.update(compute_extras(counts, now))
    return res
 
 
def build_message(res):
    lines = [f"⭐ 오늘의 판매스타 ({res['md']}) ⭐", ""]
    lines.append(f"오늘 실적 공유에 참여해주신 {len(res['people'])}분, 모두 고생 많으셨어요!")
    lines.append(f"총 {res['total']}건의 판매가 공유됐습니다 👏")
    lines.append("")
    lines.append(f"👑 오늘의 판매왕 ({res['top']}건)")
    for k in res["kings"]:
        lines.append(f"  · {k}")
    lines.append("정말 대단해요! 🔥" if len(res["kings"]) == 1 else "모두 정말 대단해요! 🔥")
    lines.append("")
    if len(res["luckies"]) >= 2:
        lines.append("🎰 럭키 추첨 (토요일 특별 2배 추첨! · 당일 1건 이상 공유자 중)")
    else:
        lines.append("🎰 럭키 추첨 (당일 1건 이상 공유자 중 추첨)")
    for lk in res["luckies"]:
        lines.append(f"  · {lk} 🎉")
    lines.append("축하드려요!")
 
    if res.get("milestones"):
        lines.append("")
        lines.append("🎯 달성 축하")
        for name, m in res["milestones"]:
            lines.append(f"  · {name} 님, 이달 {m}건 달성! 👏")
 
    if res.get("goldenbell"):
        n, name = res["goldenbell"]
        lines.append("")
        lines.append("🔔 지사 골든벨")
        lines.append(f"  오늘 우리 지사 월누적 {n}건 돌파! 🎊")
        lines.append(f"  {n}번째 주인공: {name} 님")
 
    if res.get("steadies"):
        lines.append("")
        lines.append("🏅 꾸준왕")
        for name, days in res["steadies"]:
            cheer = STEADY_CHEERS.get(days, "대단한 꾸준함이에요!")
            lines.append(f"  · {name} 님, 이달 {days}일째 참여! {cheer}")
 
    if res.get("full_stores"):
        lines.append("")
        lines.append("🎖 매장 완전체")
        for store, names in res["full_stores"]:
            lines.append(f"  · 오늘 {store}, 전원 실적 달성! 완벽한 팀워크 👏")
            lines.append(f"    ({' · '.join(names)})")
 
    if res.get("debuts"):
        lines.append("")
        lines.append("🌱 데뷔 축하")
        for name in res["debuts"]:
            lines.append(f"  · {name} 님, 첫 실적 신고! 환영합니다!")
 
    if res.get("comebacks"):
        lines.append("")
        lines.append("🎉 컴백 축하")
        for name, gap in res["comebacks"]:
            lines.append(f"  · {name} 님, {gap}일 만의 복귀! 다시 달려봐요!")
 
    if res.get("nosales"):
        lines.append("")
        lines.append("💪 응원합니다")
        for name, streak in res["nosales"]:
            cheer = random.choice(NOSALE_CHEERS)
            lines.append(f"  · {name} 님, {streak}일째 잠잠… {cheer}")
 
    lines.append("")
    lines.append("내일도 1인 1건! 우리 지사 파이팅 💪")
    return "\n".join(lines)
 
 
def _upsert_csv(path, header, rows, date_str):
    """같은 날짜 줄은 지우고 새로 기록(중복 방지). Excel용 utf-8-sig."""
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
    # 시상 결과
    _upsert_csv(
        AWARD_CSV, ["날짜", "판매왕", "판매왕건수", "럭키추첨"],
        [[d, ", ".join(res["kings"]), res["top"], ", ".join(res["luckies"])]], d,
    )
    # 개인 실적 (사람별 한 줄)
    rows = [[d, name, cnt] for name, cnt in sorted(res["counts"].items(),
                                                   key=lambda x: -x[1])]
    _upsert_csv(SALES_CSV, ["날짜", "점명이름", "건수"], rows, d)
 
 
def main():
    msgs = [t for t in fetch_messages() if looks_like_report(t)]
    if not msgs:
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
 
