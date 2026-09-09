"""
실적 중간 점검 봇 (14시 / 16시 / 18시)
─────────────────────────────────────────────
- 실적공유방 메시지를 읽어 '원본을 즉시 파일에 저장'한 뒤,
  오늘 저장된 전체를 기준으로 상권별 무실적(미공유) 매장을 알린다.
- 저장 파일: data/raw/YYYY-MM-DD.txt  (시상봇도 이 파일을 사용)
- AI를 쓰지 않으므로 추가 비용이 없다.

비밀값(Secrets):
  TELEGRAM_TOKEN_3 : 강동요정봇 토큰
  TARGET_CHAT_ID_3 : 실적공유방 ID
"""

import os
import re
import time
from datetime import datetime
from zoneinfo import ZoneInfo

import requests

TELEGRAM_TOKEN = os.environ["TELEGRAM_TOKEN_3"]
CHAT_ID        = int(os.environ["TARGET_CHAT_ID_3"])
KST = ZoneInfo("Asia/Seoul")
TG  = f"https://api.telegram.org/bot{TELEGRAM_TOKEN}"

RAW_DIR = "data/raw"
SEP = "\n<<<MSG>>>\n"

# ── 상권 구성 ────────────────────────────────────
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
# 매장 인식용 별칭 (모호한 "강릉", "양주"는 제외)
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
ALL_STORES = [s for ss in AREAS.values() for s in ss]

# 실적 보고로 보이는 메시지 판별 (시상봇과 동일 기준)
REPORT_HINTS = ("기변", "신규", "번이", "MNP", "mnp", "요할", "심플", "단할", "공시")


def looks_like_report(t):
    return sum(1 for k in REPORT_HINTS if k in t) >= 2


# ── 원본 저장/로드 ───────────────────────────────
def raw_path(date_str):
    return f"{RAW_DIR}/{date_str}.txt"


def save_raw(msgs, date_str):
    """읽은 원본을 즉시 append 저장(유실 방지)."""
    if not msgs:
        return 0
    os.makedirs(RAW_DIR, exist_ok=True)
    with open(raw_path(date_str), "a", encoding="utf-8") as f:
        for m in msgs:
            f.write(m.replace(SEP.strip(), " ") + SEP)
    return len(msgs)


def load_raw(date_str):
    try:
        with open(raw_path(date_str), encoding="utf-8") as f:
            txt = f.read()
    except FileNotFoundError:
        return []
    return [m.strip() for m in txt.split(SEP) if m.strip()]


# ── 텔레그램 ─────────────────────────────────────
def fetch_messages():
    texts, offset = [], None
    n_up = 0
    while True:
        params = {"timeout": 0, "limit": 100}
        if offset is not None:
            params["offset"] = offset
        try:
            r = requests.get(f"{TG}/getUpdates", params=params, timeout=30).json()
        except Exception as e:
            print(f"[중간] getUpdates 실패: {e!r}")
            break
        if not r.get("ok", False):
            print(f"[중간] API 오류 {r.get('error_code')} {r.get('description')!r}")
            break
        batch = r.get("result", [])
        if not batch:
            break
        n_up += len(batch)
        for u in batch:
            offset = u["update_id"] + 1
            m = u.get("message") or u.get("channel_post")
            if not m or not m.get("text"):
                continue
            if m["chat"]["id"] != CHAT_ID:
                continue
            texts.append(m["text"])
    print(f"[중간] 업데이트 {n_up}건 · 대상방 메시지 {len(texts)}건")
    return texts


RETRY_CODES = (500, 502, 503, 504, 429)


def tg_send(text, retries=3):
    for i in range(1, retries + 1):
        r = None
        try:
            r = requests.post(f"{TG}/sendMessage",
                              json={"chat_id": CHAT_ID, "text": text},
                              timeout=(10, 60))
            j = r.json()
            if j.get("ok"):
                print("[중간] 게시 완료" + (f" (재시도 {i}회차)" if i > 1 else ""))
                return True
            print(f"[중간] 거부 · error_code={j.get('error_code')}"
                  f" · {j.get('description')!r}")
            if j.get("error_code") not in RETRY_CODES:
                return False
        except Exception as exc:
            print(f"[중간] 전송 예외({i}/{retries}): {exc!r}")
        if i < retries:
            time.sleep(10 * i)
    return False


# ── 매장 인식 ────────────────────────────────────
def reported_stores(msgs):
    """메시지에서 실적을 공유한 매장 집합을 뽑는다(규칙 기반, AI 미사용)."""
    done = set()
    for t in msgs:
        for line in t.splitlines():
            line = line.strip()
            if not line or "/" in line:        # 개통 내역 줄은 건너뜀
                continue
            for store, keys in ALIASES.items():
                if store in done:
                    continue
                for k in keys:
                    # 줄 맨 앞에 매장명이 오는 형태(점명 이름 / 점명직영점 …)
                    if line.startswith(k):
                        done.add(store)
                        break
    return done


def build_message(done, now):
    md = f"{now.month}/{now.day}"
    total = len(ALL_STORES)
    n_done = len(done)
    all_clear = n_done == total

    lines = [f"⏰ {now.hour}시 무실적점 현황 ({md})", ""]

    if all_clear:
        lines.append("🎊🎊 전 매장 무실적 일소 완료!! 🎊🎊")
        lines.append("")
        for area in AREAS:
            lines.append(f"  · {area} — 일소 완료 ✅")
        lines.append("")
        lines.append(f"{n_done}/{total}개점 전원 실적 발생!")
        lines.append("오늘 정말 대단합니다 🔥🔥🔥")
        return "\n".join(lines)

    lines.append("📍 아직 실적이 없는 매장")
    for area, stores in AREAS.items():
        miss = [s for s in stores if s not in done]
        if miss:
            lines.append(f"  · {area} — {len(miss)}점 "
                         f"({', '.join(SHORT[s] for s in miss)})")
        else:
            lines.append(f"  · {area} — 🎊 {area} 상권 무실적 일소 완료!")
    lines.append("")
    lines.append(f"현재 {n_done}/{total}개점 무실적 탈출")
    if now.hour >= 18:
        lines.append("마감 전 마지막 점검입니다. 끝까지 포기하지 말고 파이팅! 🔥")
    else:
        lines.append("빠른 무실적 탈출을 응원합니다 💪")
    return "\n".join(lines)


def main():
    now = datetime.now(KST)
    if now.weekday() == 6:
        print("일요일은 게시하지 않습니다.")
        return
    today = now.strftime("%Y-%m-%d")

    # ① 읽자마자 원본 저장 (이후 단계에서 오류가 나도 데이터는 남는다)
    new_msgs = fetch_messages()
    saved = save_raw(new_msgs, today)
    print(f"[중간] 원본 {saved}건 저장 → {raw_path(today)}")

    # ② 오늘 누적 전체로 판정
    all_msgs = load_raw(today)
    reports = [t for t in all_msgs if looks_like_report(t)]
    done = reported_stores(reports)
    print(f"[중간] 오늘 누적 {len(all_msgs)}건 · 실적 보고 {len(reports)}건"
          f" · 공유 매장 {len(done)}곳")

    tg_send(build_message(done, now))


if __name__ == "__main__":
    try:
        main()
    except Exception:
        import traceback
        print("[중간] 치명적 오류:")
        traceback.print_exc()
