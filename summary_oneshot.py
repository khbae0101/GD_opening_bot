"""
텔레그램 방 일일 출근현황 요약 (1회 실행용)
─────────────────────────────────────────────
- 스케줄러(GitHub Actions 등)가 정해진 시각에 이 스크립트를 1번 실행
- 방에 올라온 각 직영점 '출근등록' 메시지를 읽어 상권별로 정리해 게시 후 종료
- PC를 24시간 켜둘 필요 없음

전제 조건:
- 이 봇에 웹훅(webhook)이 설정돼 있으면 안 됩니다 (getUpdates와 충돌)
- 다른 곳에서 같은 봇을 polling 하면 안 됩니다
- 하루 1번 이상 실행해야 합니다 (텔레그램은 업데이트를 24시간만 보관)
- 그룹이면 BotFather에서 봇 privacy mode를 Disable 해두세요
"""

import os
from datetime import datetime
from zoneinfo import ZoneInfo

import requests
import anthropic

TELEGRAM_TOKEN    = os.environ["TELEGRAM_TOKEN"]
ANTHROPIC_API_KEY = os.environ["ANTHROPIC_API_KEY"]
SOURCE_CHAT_ID    = int(os.environ["SOURCE_CHAT_ID"])
TARGET_CHAT_ID    = int(os.environ.get("TARGET_CHAT_ID", SOURCE_CHAT_ID))
SUMMARY_MODEL     = "claude-sonnet-4-6"   # 더 저렴/빠르게: "claude-haiku-4-5-20251001"

API = f"https://api.telegram.org/bot{TELEGRAM_TOKEN}"

# ── 직영점 목록 (상권별) ─────────────────────────────────
# 매장이 새로 생기거나 상권이 바뀌면 여기만 고치면 됩니다.
STORE_MAP = {
    "광진/구리": ["도농로", "구리리맥스", "자양번영로", "다산신도시", "건대입구역",
                 "면목역", "상봉역", "외대역", "금호동", "진접"],
    "경기북부": ["중계아울렛", "수유", "의정부로데오", "옥정신도시", "삼양로",
                "먹골역", "지행역", "상계역", "양주덕계"],
    "강원":     ["동해천곡", "석사", "강릉임당", "원주무실", "단구",
                "강릉유천", "홍천중앙", "후평", "온의"],
}


def fetch_messages():
    """텔레그램이 보관 중인 업데이트를 모두 가져오면서 동시에 확인 처리."""
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
            # 사진에 붙은 설명글(caption)도 함께 수집
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
각 직영점이 올리는 "출근등록" 메시지를 읽고, 지사 전체 출근 현황을 보기 좋게 정리해줘.

# 직영점 목록 (상권별 — 이 목록이 기준이야)
{store_lines}

# 점포명 매칭 규칙
- 매장이 매장명을 줄여서 올릴 수 있어. (예: "강릉임당"→"강릉", "동해천곡"→"동해", "양주덕계"→"덕계")
- 올라온 이름이 위 목록 중 어떤 점포와 닮았거나 일부면 그 점포로 매칭해줘.
- "덕계"는 양주덕계로 매칭해줘. 다만 "양주"만 단독으로 쓰이면 옥정신도시(양주)와 양주덕계가 모두 양주라
  헷갈릴 수 있으니, 그땐 "(확인필요)"로 표시해줘.
- 단, 한 약칭이 두 점포에 모두 해당될 수 있으면(예: "강릉"은 강릉임당·강릉유천 둘 다 가능)
  임의로 추측하지 말고 점포명 옆에 "(확인필요)"라고 표시해줘.
- 위 목록에 없거나 출근등록이 아닌 잡담 메시지는 무시해.

# 집계 규칙
- 각 점포의 출근(근무) 인원과 휴무 인원을 메시지에서 읽어.
- 목록에 있는데 출근등록 메시지가 없는 점포는 "미등록"으로 표시.
- 지사 전체 합계 → 출근 = 전체 근무 인원수, 휴무 = 전체 휴무 인원수, 총원 = 출근 + 휴무.

# 출력 규칙 (매우 중요)
- 검토 과정, 분석 메모, 머리말, 인사말 등 어떤 설명도 절대 쓰지 마.
- 아래 출력 형식의 "📋"로 시작하는 첫 줄부터 곧바로 시작해서, 그 형식만 출력해.
- 각 점포는 점포명을 한 줄, 출근과 휴무를 각각 다음 줄에 적어 블록으로 만들어.
- 점포와 점포 사이에는 빈 줄을 하나 넣어서 구분이 잘 되게 해.

# 출력 형식 (아래 형식 그대로)
📋 {today} 출근 현황
전체: 총원 N명 / 출근 N명 / 휴무 N명

━━━━━━━━━━
🏙 광진/구리
━━━━━━━━━━

▪ 점포명
　출근: 이름, 이름
　휴무: 이름

▪ 점포명
　미등록

(경기북부, 강원도 같은 방식. 상권 제목 사이에는 빈 줄)

━━━━━━━━━━
⚠️ 특이사항
━━━━━━━━━━
• 점포명: 내용
(특이사항이 하나도 없으면 "없음"이라고만)

# 메시지 원문
{body}
"""


def summarize(messages, today):
    client = anthropic.Anthropic(api_key=ANTHROPIC_API_KEY)
    resp = client.messages.create(
        model=SUMMARY_MODEL,
        max_tokens=3000,
        system="너는 정해진 출력 형식만 그대로 출력하는 도구야. "
               "검토 과정이나 설명, 머리말을 절대 붙이지 말고 요청된 형식만 출력해.",
        messages=[{"role": "user", "content": build_prompt(messages, today)}],
    )
    text = resp.content[0].text
    # 안전장치: 혹시 앞에 설명이 붙으면 '📋'부터 잘라서 보냄
    if "📋" in text:
        text = text[text.index("📋"):]
    return text.strip()


def send(text):
    requests.post(
        f"{API}/sendMessage",
        json={"chat_id": TARGET_CHAT_ID, "text": text},
        timeout=30,
    )


def main():
    today = datetime.now(ZoneInfo("Asia/Seoul")).strftime("%Y-%m-%d")
    messages = fetch_messages()
    if not messages:
        print("가져온 메시지가 없습니다.")
        return
    result = summarize(messages, today)
    send(result)
    print("출근 현황 게시 완료")


if __name__ == "__main__":
    main()
