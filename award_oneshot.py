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
from datetime import datetime
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
 
 
def count_sales(reports):
    """AI로 직원별 휴대폰 개통 건수를 집계해 dict로 반환."""
    body = "\n\n──\n\n".join(reports)
    prompt = f"""아래는 오늘 단체방에 올라온 휴대폰 판매 실적 보고들이야. 직원별 '휴대폰 개통 건수'를 세줘.
 
# 세는 규칙
- 보통 "점명 이름"(예: 중계 전우진) 줄 다음에 개통 내역 줄이 온다.
- 개통 내역 줄은 "모델명/개통유형/요금제/부가/보험/카드/리본" 형식이고, 휴대폰 모델명으로 시작한다(A175, S948, F966, AIP17, M366, ZTE 클래식폴더 등).
- 휴대폰 모델명으로 시작하는 줄 1개 = 1건. 한 사람 아래 개통 줄이 2~3개면 그만큼 여러 건으로 센다.
- 다음은 세지 마라(제외): 유선상품(에센스, 베이직, 모든G, 인터넷/TV, MITT, GTT, 신동, 원스톱, ITTM 등), 2nd기기(워치·패드·버즈, '2ND'/'세컨' 표기), 약정갱신만 있는 줄.
- 휴대폰 신규/기변/번이/MNP/공시 개통만 센다.
- 맨 앞 글 작성자 줄("...님:" 또는 "이름:")은 무시하고 "점명 이름"으로 집계한다.
- 이름 줄 없이 개통 줄만 이어지면 바로 위 사람 것으로 본다.
- 점명이 줄임말/다른 표기로 적혀도 표준 점명으로 통일해서 기록한다. 특히 "덕계", "양주덕계점"은 "양주덕계"로 기록한다. (단, "양주"만 단독으로 적힌 경우는 옥정신도시와 구분이 안 되니 적힌 그대로 둔다.)
 
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
 
 
def compute_result(counts):
    now = datetime.now(KST)
    people = list(counts.keys())
    top = max(counts.values())
    kings = [n for n, c in counts.items() if c == top]
    # 토요일은 럭키추첨 2명(참여자가 적으면 있는 만큼), 그 외 1명
    n_lucky = min(2 if now.weekday() == 5 else 1, len(people))
    luckies = random.sample(people, n_lucky)
    return {
        "date": now.strftime("%Y-%m-%d"),
        "md": f"{now.month}/{now.day}",
        "people": people, "total": sum(counts.values()),
        "top": top, "kings": kings, "luckies": luckies, "counts": counts,
        "sat": now.weekday() == 5,
    }
 
 
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
 
