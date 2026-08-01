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
LUCKY_PICKS = 2        # 럭키추첨 인원(매일)
LUCKY_MIN   = 5000     # 당첨금 최소
LUCKY_MAX   = 20000    # 당첨금 최대 (천원 단위 랜덤)
NOTICE = ("8월 판매챌린지는 8/10(월)부터 시작됩니다!\n"
          "새로운 시상과 함께 돌아올게요, 많이 기대해주세요 🔥")
 
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
    """표준 인원 명단(data/roster.csv)을 읽어 '매장 이름' 목록으로 반환. 없으면 빈 리스트."""
    path = "data/roster.csv"
    try:
        with open(path, newline="", encoding="utf-8-sig") as f:
            rows = list(csv.reader(f))
        return [f"{r[0].strip()} {r[1].strip()}" for r in rows[1:] if len(r) >= 2 and r[0].strip()]
    except Exception as e:
        print(f"[시상] 명단 로드 실패({e!r}) - 명단 없이 진행")
        return []
 
 
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
 
 
def compute_result(counts):
    now = datetime.now(KST)
    people = list(counts.keys())
    top = max(counts.values())
    kings = [n for n, c in counts.items() if c == top]
    # 럭키추첨: 매일 2명 (참여자가 적으면 있는 만큼), 금액은 5천~2만원 천원단위 랜덤
    n_lucky = min(LUCKY_PICKS, len(people))
    luckies = random.sample(people, n_lucky)
    prizes = [random.randrange(LUCKY_MIN, LUCKY_MAX + 1, 1000) for _ in luckies]
    res = {
        "date": now.strftime("%Y-%m-%d"),
        "md": f"{now.month}/{now.day}",
        "people": people, "total": sum(counts.values()),
        "top": top, "kings": kings, "luckies": luckies, "prizes": prizes,
        "counts": counts, "sat": now.weekday() == 5,
    }
 
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
    lines.append(f"🎰 럭키 추첨 (당일 1건 이상 공유자 중 {LUCKY_PICKS}명 · 당첨금 {LUCKY_MIN:,}~{LUCKY_MAX:,}원 랜덤)")
    for name, prize in zip(res["luckies"], res.get("prizes", [])):
        tail = " 🎊 잭팟!" if prize >= LUCKY_MAX else ""
        lines.append(f"  · {name} — {prize:,}원{tail} 🎉")
    lines.append("축하드려요!")
 
 
    lines.append("")
    lines.append(NOTICE)
    return "\n".join(lines)
 
 
def write_csv(res):
    d = res["date"]
    # 시상 결과 (럭키추첨은 "이름(금액)" 형태로 기록)
    lucky_txt = ", ".join(f"{n}({p:,}원)" for n, p in
                          zip(res["luckies"], res.get("prizes", [])))
    _upsert_csv(
        AWARD_CSV, ["날짜", "판매왕", "판매왕건수", "럭키추첨"],
        [[d, ", ".join(res["kings"]), res["top"], lucky_txt]], d,
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
 
