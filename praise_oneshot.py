"""
칭찬봇 (1회 실행용)
─────────────────────────────────────────────
- 본부 텔레그램방을 30분마다 확인(cron-job.org가 호출).
- '우리 지사 27개 매장' 소속의 판매 성공사례(드림스타 등)에만 반응.
  (다른 지사·매장 글, 일반 잡담은 무시 → 답글 안 달고 SKIP)
- 해당되면 내용을 읽고 핵심을 짚어 '격하게 칭찬'하는 답글(reply)을 단다.
- getUpdates가 메시지를 한 번 읽으면 비우므로, 같은 글에 두 번 답글 달지 않음.

비밀값(Secrets):
  TELEGRAM_TOKEN_4  : 칭찬봇 토큰
  SOURCE_CHAT_ID_4  : 본부 방 ID
  ANTHROPIC_API_KEY : (기존 공유)

* 이 봇은 방의 글을 읽어야 하므로 BotFather에서 Group Privacy를 꺼야 합니다.
"""

import os
import requests
import anthropic

TELEGRAM_TOKEN    = os.environ["TELEGRAM_TOKEN_4"]
ANTHROPIC_API_KEY = os.environ["ANTHROPIC_API_KEY"]
SOURCE_CHAT_ID    = int(os.environ["SOURCE_CHAT_ID_4"])
MODEL             = "claude-sonnet-4-6"   # 비용 더 아끼려면 "claude-haiku-4-5-20251001"

TG = f"https://api.telegram.org/bot{TELEGRAM_TOKEN}"

STORE_MAP = {
    "광진/구리": ["도농로", "구리리맥스", "자양번영로", "다산신도시", "건대입구역",
                 "면목역", "상봉역", "외대역", "금호동", "진접"],
    "경기북부": ["중계아울렛", "수유", "의정부로데오", "옥정신도시", "삼양로",
                "먹골역", "지행역", "상계역", "양주덕계"],
    "강원":     ["동해천곡", "석사", "강릉임당", "원주무실", "단구",
                "강릉유천", "홍천중앙", "후평", "온의"],
}
ALL_STORES = ", ".join(sum(STORE_MAP.values(), []))

PRAISE_SYSTEM = (
    "너는 텔레그램 본부방에서 '우리 지사 매장'의 판매 성공사례에만 반응해 격하게 "
    "칭찬하는 봇이야. 조건에 안 맞으면 다른 말 없이 정확히 'SKIP'만 출력해."
)


def fetch_messages():
    """방의 새 메시지(텍스트)를 message_id와 함께 가져오고 동시에 확인 처리."""
    items, offset = [], None
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
            m = u.get("message")
            if not m or not m.get("text"):
                continue
            if m["chat"]["id"] != SOURCE_CHAT_ID:
                continue
            items.append({"id": m["message_id"], "text": m["text"]})
    return items


def looks_like_success(t):
    """AI에 보내기 전 1차 거르기: 성공사례 형태인지 대략 판단(잡담 제외)."""
    if "드림스타" in t:
        return True
    if "소속" in t and ("유치" in t or "성공" in t):
        return True
    return False


def make_praise(text):
    prompt = f"""아래는 본부 텔레그램방에 올라온 메시지야.

# 우리 지사 매장(27곳)
{ALL_STORES}

# 판단
- 이 메시지가 판매 성공사례 보고(드림스타 등)이고, 그 '소속' 매장이 위 27곳 중 하나면
  → 그 직원을 격하게 칭찬하는 답글을 써.
- 성공사례가 아니거나, 소속이 위 27곳이 아니면(다른 지사·매장) → 정확히 'SKIP' 한 단어만 출력.
- 약칭으로 적혀 있어도(예: 무실=원주무실, 의로=의정부로데오, 덕계=양주덕계) 위 목록과 매칭해서 판단해.
  단, "양주"만 단독으로 적힌 경우는 옥정신도시(양주)와 양주덕계가 모두 양주라 어느 매장인지 불확실하니 'SKIP' 한다.

# 칭찬 답글 규칙 (해당될 때만)
- 내용을 읽고 핵심 성공요인이나 유치실적을 1~2가지 구체적으로 짚어줘.
- 2~4문장, 친근하고 격하게, 이모지 살짝.
- 직원 이름을 넣어 불러주고, 군더더기 설명 없이 답글 문구만 출력.

# 메시지
{text}
"""
    client = anthropic.Anthropic(api_key=ANTHROPIC_API_KEY)
    resp = client.messages.create(
        model=MODEL, max_tokens=500,
        system=PRAISE_SYSTEM,
        messages=[{"role": "user", "content": prompt}],
    )
    return resp.content[0].text.strip()


def reply(message_id, text):
    requests.post(
        f"{TG}/sendMessage",
        json={"chat_id": SOURCE_CHAT_ID, "text": text,
              "reply_parameters": {"message_id": message_id}},
        timeout=30,
    )


def main():
    msgs = fetch_messages()
    done = 0
    for m in msgs:
        if not looks_like_success(m["text"]):
            continue
        out = make_praise(m["text"])
        if not out or out.upper().startswith("SKIP"):
            continue
        reply(m["id"], out)
        done += 1
    print(f"확인 {len(msgs)}건 · 칭찬 답글 {done}건")


if __name__ == "__main__":
    main()
