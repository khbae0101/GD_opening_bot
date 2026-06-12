"""
텔레그램 마감보고 요약 (1회 실행용)
─────────────────────────────────────────────
- 방에 올라온 각 매장 '마감보고' 메시지만 골라내(잡담은 무시),
  전일자 기준으로 상권별 실적을 정리해 게시하고 종료.
- cron-job.org가 매일 아침 정해진 시각에 이 워크플로를 호출해서 실행됨.
 
비밀값(Secrets) — 출근봇과 겹치지 않게 _2 를 붙였습니다:
  TELEGRAM_TOKEN_2   : 마감보고 봇 토큰
  SOURCE_CHAT_ID_2   : 마감보고 방 ID
  TARGET_CHAT_ID_2   : 요약 올릴 방 ID (같으면 SOURCE와 동일)
  ANTHROPIC_API_KEY  : (출근봇과 공유)
"""
 
import os
import requests
import anthropic
 
TELEGRAM_TOKEN    = os.environ["TELEGRAM_TOKEN_2"]
ANTHROPIC_API_KEY = os.environ["ANTHROPIC_API_KEY"]
SOURCE_CHAT_ID    = int(os.environ["SOURCE_CHAT_ID_2"])
TARGET_CHAT_ID    = int(os.environ.get("TARGET_CHAT_ID_2", SOURCE_CHAT_ID))
SUMMARY_MODEL     = "claude-sonnet-4-6"
 
API = f"https://api.telegram.org/bot{TELEGRAM_TOKEN}"
 
# ── 매장 목록 (상권별) — 출근봇과 동일 ──────────────────
STORE_MAP = {
    "광진/구리": ["도농로", "구리리맥스", "자양번영로", "다산신도시", "건대입구역",
                 "면목역", "상봉역", "외대역", "금호동", "진접"],
    "경기북부": ["중계아울렛", "수유", "의정부로데오", "옥정신도시", "삼양로",
                "먹골역", "지행역", "상계역"],
    "강원":     ["동해천곡", "석사", "강릉임당", "원주무실", "단구",
                "강릉유천", "홍천중앙", "후평", "온의"],
}
 
 
def fetch_messages():
    """방의 모든 업데이트를 가져오면서 동시에 확인 처리."""
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
 
 
def build_prompt(messages):
    store_lines = "\n".join(
        f"[{area}] " + ", ".join(stores) for area, stores in STORE_MAP.items()
    )
    body = "\n\n---\n\n".join(messages)
    return f"""아래는 텔레그램 방에 올라온 메시지들이야. 마감보고 외에 잡담·공지도 많이 섞여 있어.
각 매장의 '마감보고'만 골라내서 전일자 기준 실적을 정리해줘.
 
# 매장 목록 (상권별 — 이 목록이 기준이야)
{store_lines}
 
# 마감보고 식별
- 마감보고는 보통 "개통", "문자발송 당일/누적", "100점 확정건 당일/누적", "처리자"와
  날짜·매장명을 포함해. 이런 구조가 아닌 일반 잡담/대화/공지는 전부 무시해.
 
# 매장명 매칭
- 매장명은 줄여서 올라올 수 있어 (예: 무실→원주무실, 임당→강릉임당, 의로→의정부로데오,
  유천→강릉유천, 상계→상계역, 먹골→먹골역, 건대→건대입구역, 중계→중계아울렛, 구리→구리리맥스 등).
  위 목록의 매장으로 매칭해.
- 한 보고에 여러 날짜가 있으면 가장 최근 날짜(전일자) 것만 사용해.
 
# 집계 (매장별)
- 문자발송: 당일 / 누적
- 100점 확정건: 당일 / 누적
- 확정비중 = 100점확정 ÷ 문자발송 (당일·누적 각각, 소수점 1자리 %). 발송이 0이면 "-".
- 문자발송 제외 건이 있으면 비고에 "제외 N건(사유)".
- 목록에 있는데 마감보고가 없는 매장은 "미등록".
 
# 합계
- 상권별 합계, 지사 전체 합계도 같은 항목으로. (발송·확정은 합, 비중은 확정합÷발송합)
 
# 순위 (그날 마감을 올린 매장만 대상, 미등록 제외)
- 누적 확정비중 기준 상위 5개(높은 순), 하위 5개(낮은 순)
 
# 출력 규칙 (매우 중요)
- 검토 과정·설명·머리말 절대 쓰지 말고, "📊"로 시작하는 첫 줄부터 곧바로 그 형식만 출력해.
- 계산·재계산·검증은 머릿속으로만 하고, 그 과정(예: "재계산 필요", "2/4=50%" 같은 풀이,
  "이상값이나 그대로 반영" 같은 메모)은 절대 출력하지 마. 최종 결과 숫자만 형식에 넣어.
- "📊 마감 현황" 보고는 딱 한 번만, 최종본만 출력해. 같은 보고를 두 번 쓰지 마.
- 헤더 날짜는 마감보고의 가장 최근 날짜(전일자)를 M/D로 적어.
 
# 출력 형식 (그대로)
📊 (날짜) 마감 현황
 
🏆 상위5: 매장 00.0% · 매장 00.0% · 매장 00.0% · 매장 00.0% · 매장 00.0%
🔻 하위5: 매장 00.0% · 매장 00.0% · 매장 00.0% · 매장 00.0% · 매장 00.0%
 
[지사 합계]
당일 : 발송 0건 / 확정 0건 / 0.0%
누적 : 발송 0건 / 확정 0건 / 0.0%
 
━━━━━━━━━━
🏙 광진/구리
당일 : 발송 0건 / 확정 0건 / 0.0%
누적 : 발송 0건 / 확정 0건 / 0.0%
━━━━━━━━━━
 
▪ 매장명
당일 : 발송 0건 / 확정 0건 / 0.0%
누적 : 발송 0건 / 확정 0건 / 0.0%
 
▪ 매장명 — 미등록
 
(경기북부, 강원도 같은 방식. 비고 있으면 매장 블록 아래 "비고 : ..." 한 줄 추가)
 
━━━━━━━━━━
전일 미등록 매장 : N점 (매장명, 매장명, ...)
 
# 맨 마지막 줄 규칙
- 마감보고를 안 올린(미등록) 매장 수를 "N점"으로 적고, 괄호 안에 그 매장명을 모두 나열해.
- 미등록 매장이 없으면 "전일 미등록 매장 : 0점" 으로만 적어.
 
# 메시지 원문
{body}
"""
 
 
def summarize(messages):
    client = anthropic.Anthropic(api_key=ANTHROPIC_API_KEY)
    resp = client.messages.create(
        model=SUMMARY_MODEL,
        max_tokens=3500,
        system="너는 정해진 출력 형식만 그대로 출력하는 도구야. "
               "검토 과정이나 설명, 머리말을 절대 붙이지 말고 요청된 형식만 출력해.",
        messages=[{"role": "user", "content": build_prompt(messages)}],
    )
    text = resp.content[0].text
    # 혹시 앞에 검토·재계산 과정이 붙어도, '마지막' 📊(최종 보고)부터만 남긴다.
    if "📊" in text:
        text = text[text.rindex("📊"):]
    return text.strip()
 
 
def send(text):
    requests.post(
        f"{API}/sendMessage",
        json={"chat_id": TARGET_CHAT_ID, "text": text},
        timeout=30,
    )
 
 
def main():
    messages = fetch_messages()
    if not messages:
        print("가져온 메시지가 없습니다.")
        return
    result = summarize(messages)
    send(result)
    print("마감보고 요약 게시 완료")
 
 
if __name__ == "__main__":
    main()
 
