"""
매장 지역별 날씨 알림 (기상청 버전, 1회 실행용)
─────────────────────────────────────────────
- 날씨   : 기상청 단기예보(오늘·주간 앞부분) + 중기예보(주간 뒷부분)
- 미세먼지: Open-Meteo (기상청엔 없어서 그대로 사용, 키 불필요)
- 월요일 : 지역별 주간(월~토)  /  화~토 : 지역별 오늘 날씨
- 모든 날짜는 한국시간(KST) 기준. 네트워크 일시 오류 시 재시도.

비밀값(Secrets):
  TELEGRAM_TOKEN_3 : 날씨 봇 토큰
  TARGET_CHAT_ID_3 : 날씨를 올릴 방 ID
  KMA_SERVICE_KEY  : 기상청 서비스키 (공공데이터포털 '일반 인증키 Decoding')
"""

import os
import re
import traceback
import time
import random
import html as _html
import xml.etree.ElementTree as ET
from urllib.parse import quote
from email.utils import parsedate_to_datetime
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

import requests

TELEGRAM_TOKEN = os.environ["TELEGRAM_TOKEN_3"]
TARGET_CHAT_ID = int(os.environ["TARGET_CHAT_ID_3"])
KMA_KEY        = os.environ["KMA_SERVICE_KEY"]
KST = ZoneInfo("Asia/Seoul")
TG  = f"https://api.telegram.org/bot{TELEGRAM_TOKEN}"

# 지역: 기상청 격자(nx,ny) + 미세먼지용 위경도 + 중기예보 지역코드
REGIONS = [
    {"name": "수도권 · 광진/구리", "nx": 62, "ny": 127, "lat": 37.596, "lon": 127.100,
     "mid_land": "11B00000", "mid_ta": "11B10101"},
    {"name": "수도권 · 경기북부", "nx": 61, "ny": 130, "lat": 37.711, "lon": 127.055,
     "mid_land": "11B00000", "mid_ta": "11B10101"},
    {"name": "강원 · 강릉/동해",  "nx": 93, "ny": 131, "lat": 37.75,  "lon": 128.90,
     "mid_land": "11D20000", "mid_ta": "11D20501"},
    {"name": "강원 · 원주",       "nx": 76, "ny": 122, "lat": 37.34,  "lon": 127.92,
     "mid_land": "11D10000", "mid_ta": "11D10501"},
    {"name": "강원 · 춘천",       "nx": 73, "ny": 134, "lat": 37.88,  "lon": 127.73,
     "mid_land": "11D10000", "mid_ta": "11D10301"},
]

RAIN_WARN = 60
HEAT_WARN = 33
COLD_WARN = -12
WIND_WARN = 14
RAIN_HEAVY = 30
SNOW_HEAVY = 5
WDAY = "월화수목금토일"

SKY_EMOJI = {"1": "☀️ 맑음", "3": "⛅ 구름많음", "4": "☁️ 흐림"}
PTY_EMOJI = {"1": "🌧️ 비", "2": "🌨️ 비/눈", "3": "❄️ 눈", "4": "🌦️ 소나기",
             "5": "🌧️ 빗방울", "6": "🌨️ 진눈깨비", "7": "🌨️ 눈날림"}

# ── 응원 멘트 (그날 날씨에 맞춰 랜덤) ─────────────────────
CHEERS = {
    "clear": [
        "☀️ 날도 화창하네요. 오늘도 활기차게, 우리 지사 파이팅입니다!",
        "🌞 맑은 하늘처럼 산뜻하게! 오늘 하루도 힘차게 가봅시다.",
        "☀️ 좋은 날씨엔 손님도 기분 좋게! 오늘도 우리 지사 화이팅!",
    ],
    "cloudy": [
        "☁️ 날은 좀 흐려도 우리 지사는 활기차게! 오늘도 힘내요.",
        "🌥️ 하늘은 흐려도 마음은 맑게! 오늘 하루도 파이팅입니다.",
        "☁️ 흐린 날일수록 서로 웃으며 힘내봐요. 오늘도 잘 부탁드려요!",
    ],
    "rain": [
        "☔ 비 소식 있어요. 우산 챙기시고 오늘도 안전하게 파이팅!",
        "🌧️ 궂은 날씨에도 우리 지사는 따뜻하게! 오늘 하루도 힘내요.",
        "☔ 비 오는 날, 발걸음 조심하시고 오늘도 화이팅입니다!",
    ],
    "snow": [
        "❄️ 눈 소식 있어요. 미끄럼 조심하시고 오늘도 안전 파이팅!",
        "🌨️ 길 미끄러우니 천천히! 오늘도 우리 지사 힘내요.",
    ],
    "heat": [
        "🥵 무더위 조심하세요. 수분 충전 잊지 마시고 오늘도 파이팅!",
        "🌡️ 더운 날엔 컨디션 관리가 최고! 시원하게 오늘도 힘내요.",
    ],
    "cold": [
        "🥶 쌀쌀하니 따뜻하게 입으세요. 오늘 하루도 화이팅입니다!",
        "🧣 추운 날, 따뜻한 차 한잔하시고 오늘도 우리 지사 파이팅!",
    ],
    "dust": [
        "😷 미세먼지 있는 날이에요. 환기 주의하시고 오늘도 좋은 하루!",
        "😷 마스크 챙기시고, 그래도 마음은 상쾌하게! 오늘도 파이팅.",
    ],
    "default": [
        "💪 오늘도 좋은 하루 시작해요. 우리 지사 파이팅!",
        "🔥 새로운 하루, 기분 좋게 시작해봅시다. 오늘도 잘 부탁드려요!",
        "✨ 오늘도 우리 지사가 최고! 힘차게 가봅시다.",
        "👏 좋은 기운으로 하루 열어요. 오늘도 모두 화이팅!",
    ],
}


def pick_cheer(cond="", pop=0, rain=0, snow=0, tmax=0, tmin=99, pm=""):
    if snow >= SNOW_HEAVY or "눈" in cond:
        key = "snow"
    elif pop >= RAIN_WARN or rain >= RAIN_HEAVY or "비" in cond or "소나기" in cond:
        key = "rain"
    elif tmax >= HEAT_WARN:
        key = "heat"
    elif tmin <= COLD_WARN:
        key = "cold"
    elif "나쁨" in pm:
        key = "dust"
    elif "흐림" in cond or "구름" in cond:
        key = "cloudy"
    elif "맑음" in cond:
        key = "clear"
    else:
        key = "default"
    return random.choice(CHEERS.get(key, CHEERS["default"]))


def fetch_json(url, params, tries=3, timeout=25):
    """JSON 호출. 실패 시 응답 원문 일부를 로그로 남긴다(원인 파악용)."""
    last = None
    for i in range(tries):
        try:
            r = requests.get(url, params=params, timeout=timeout)
            try:
                return r.json()
            except ValueError:
                # JSON이 아닌 응답(기상청은 키·사용량 오류 시 XML을 준다)
                txt = (r.text or "")[:400]
                print(f"[기상청] JSON 아님 · status={r.status_code} · 응답={txt!r}")
                for kw, msg in (
                    ("LIMITED_NUMBER_OF_SERVICE_REQUESTS", "일일 사용량 초과"),
                    ("SERVICE_KEY_IS_NOT_REGISTERED", "서비스키 미등록/오류"),
                    ("SERVICE_ACCESS_DENIED", "해당 API 활용신청 필요"),
                    ("DEADLINE_HAS_EXPIRED", "활용기간 만료"),
                    ("UNREGISTERED_IP", "미등록 IP"),
                ):
                    if kw in txt:
                        print(f"[기상청] ★ 원인: {msg} ★ 공공데이터포털에서 확인하세요")
                        raise RuntimeError(msg)
                raise RuntimeError("기상청 응답 형식 오류")
        except RuntimeError:
            raise                      # 키·사용량 문제는 재시도해도 소용없음
        except Exception as e:
            last = e
            print(f"[기상청] 호출 실패({i + 1}/{tries}): {e!r}")
            time.sleep(2 * (i + 1))
    raise last


# ── 기상청 호출 ──────────────────────────────────────────
def kma_items(url, params):
    base = {"serviceKey": KMA_KEY, "dataType": "JSON", "numOfRows": 1000, "pageNo": 1}
    d = fetch_json(url, {**base, **params})
    header = d.get("response", {}).get("header", {})
    code = header.get("resultCode")
    if code not in (None, "00"):
        print(f"[기상청] 오류 응답 · resultCode={code}"
              f" · resultMsg={header.get('resultMsg')!r}")
        raise RuntimeError(f"기상청 오류 {code}")
    return d["response"]["body"]["items"]["item"]


def base_fullday(now):
    """오늘 일자료(최저·최고기온 포함)를 받기 위한 단기예보 발표시각."""
    if now.hour >= 3:
        return now.strftime("%Y%m%d"), "0200"
    y = now - timedelta(days=1)
    return y.strftime("%Y%m%d"), "2300"


def fetch_vilage(rg, bdate, btime):
    return kma_items(
        "https://apis.data.go.kr/1360000/VilageFcstInfoService_2.0/getVilageFcst",
        {"base_date": bdate, "base_time": btime, "nx": rg["nx"], "ny": rg["ny"]},
    )


def parse_amt(v):
    if not v or "없음" in v:
        return 0.0
    m = re.search(r"[\d.]+", v)
    return float(m.group()) if m else 0.0


def cond_emoji(sky, pty):
    if pty and pty != "0":
        return PTY_EMOJI.get(pty, "🌧️ 비")
    return SKY_EMOJI.get(sky, "☁️ 흐림")


def day_data(items, date_str):
    """해당 날짜의 요약: 오전/오후 날씨, 강수확률, 최저/최고, 강수·적설·풍속."""
    sky, pty, tmps = {}, {}, []
    pop = wind = rain = snow = 0.0
    tmn = tmx = None
    for it in items:
        if it["fcstDate"] != date_str:
            continue
        c, t, v = it["category"], it["fcstTime"], it["fcstValue"]
        if c == "POP": pop = max(pop, float(v))
        elif c == "TMN": tmn = float(v)
        elif c == "TMX": tmx = float(v)
        elif c == "TMP": tmps.append(float(v))
        elif c == "WSD": wind = max(wind, float(v))
        elif c == "SKY": sky[t] = v
        elif c == "PTY": pty[t] = v
        elif c == "PCP": rain = max(rain, parse_amt(v))
        elif c == "SNO": snow = max(snow, parse_amt(v))
    if tmn is None and tmps: tmn = min(tmps)
    if tmx is None and tmps: tmx = max(tmps)
    return {
        "am":  cond_emoji(sky.get("0900"), pty.get("0900")),
        "aft": cond_emoji(sky.get("1500"), pty.get("1500")),
        "pop": int(pop), "wind": wind, "rain": rain, "snow": snow,
        "tmin": round(tmn) if tmn is not None else 0,
        "tmax": round(tmx) if tmx is not None else 0,
    }


# ── 중기예보 (주간 뒷부분) ───────────────────────────────
def mid_tmfc(now):
    if now.hour < 6:
        return (now - timedelta(days=1)).strftime("%Y%m%d") + "1800"
    if now.hour < 18:
        return now.strftime("%Y%m%d") + "0600"
    return now.strftime("%Y%m%d") + "1800"


def fetch_mid(rg, tmfc):
    ta = kma_items("https://apis.data.go.kr/1360000/MidFcstInfoService/getMidTa",
                   {"regId": rg["mid_ta"], "tmFc": tmfc})[0]
    land = kma_items("https://apis.data.go.kr/1360000/MidFcstInfoService/getMidLandFcst",
                     {"regId": rg["mid_land"], "tmFc": tmfc})[0]
    return ta, land


def mid_emoji(text):
    if not text: return "☁️ 흐림"
    if "눈" in text: return "❄️ 눈"
    if "소나기" in text: return "🌦️ 소나기"
    if "비" in text: return "🌧️ 비"
    if "흐림" in text: return "☁️ 흐림"
    if "구름많" in text: return "⛅ 구름많음"
    if "맑음" in text: return "☀️ 맑음"
    return "☁️ " + text


# ── 특보/내방영향 ────────────────────────────────────────
def alerts_for(tmax, tmin, rain, snow, wind):
    out = []
    if rain >= RAIN_HEAVY: out.append("호우 주의 수준")
    if snow >= SNOW_HEAVY: out.append("대설 주의 수준")
    if tmax >= HEAT_WARN: out.append("폭염 주의 수준")
    if tmin <= COLD_WARN: out.append("한파 주의 수준")
    if wind >= WIND_WARN: out.append("강풍 주의 수준")
    return out


def visit_impact(pop, rain, snow, tmax, tmin, pm):
    reasons = []
    if pop >= RAIN_WARN or rain >= RAIN_HEAVY: reasons.append("비")
    if snow >= SNOW_HEAVY: reasons.append("눈")
    if tmax >= HEAT_WARN: reasons.append("폭염")
    if tmin <= COLD_WARN: reasons.append("한파")
    if "나쁨" in pm: reasons.append("미세먼지")
    return f"주의 ({'·'.join(reasons)})" if reasons else "양호"


# ── 미세먼지 (Open-Meteo) ────────────────────────────────
def pm_grade(pm10, pm25):
    def g10(v): return 1 if v <= 30 else 2 if v <= 80 else 3 if v <= 150 else 4
    def g25(v): return 1 if v <= 15 else 2 if v <= 35 else 3 if v <= 75 else 4
    g = max(g10(pm10 or 0), g25(pm25 or 0))
    return {1: "🟢 좋음", 2: "🟡 보통", 3: "🟠 나쁨", 4: "🔴 매우나쁨"}[g]


def fetch_airquality(lat, lon, day):
    try:
        aq = fetch_json(
            "https://air-quality-api.open-meteo.com/v1/air-quality",
            {"latitude": lat, "longitude": lon, "hourly": "pm10,pm2_5",
             "timezone": "Asia/Seoul", "start_date": day, "end_date": day},
            tries=2,
        )
        pm10 = [x for x in aq["hourly"]["pm10"] if x is not None]
        pm25 = [x for x in aq["hourly"]["pm2_5"] if x is not None]
        return pm_grade(sum(pm10) / max(1, len(pm10)), sum(pm25) / max(1, len(pm25)))
    except Exception:
        return "⚪ 정보없음"


# ── 오늘 날씨 (화~토) ────────────────────────────────────
def build_today():
    now = datetime.now(KST)
    today = now.strftime("%Y%m%d")
    bdate, btime = base_fullday(now)
    lines = [f"🌤 {now.month}/{now.day}({WDAY[now.weekday()]}) 매장 날씨", ""]

    regions = []
    for rg in REGIONS:
        try:
            items = fetch_vilage(rg, bdate, btime)
            d = day_data(items, today)
            d["name"] = rg["name"]; d["ok"] = True
            d["pm"] = fetch_airquality(rg["lat"], rg["lon"], now.strftime("%Y-%m-%d"))
            regions.append(d)
        except Exception:
            regions.append({"name": rg["name"], "ok": False})

    al = []
    for r in regions:
        if not r.get("ok"):
            continue
        for a in alerts_for(r["tmax"], r["tmin"], r["rain"], r["snow"], r["wind"]):
            al.append(f"· {r['name'].split(' · ')[-1]} {a}")
    if al:
        lines += ["⚠️ 기상특보(자동판단)"] + al + [""]

    for r in regions:
        lines.append(f"[{r['name']}]")
        if not r.get("ok"):
            lines.append("날씨 정보를 일시적으로 불러오지 못했어요")
            lines.append("")
            continue
        lines.append(f"오전 {r['am']} → 오후 {r['aft']}")
        lines.append(f"강수확률 {r['pop']}% · 기온 {r['tmin']}~{r['tmax']}°C")
        lines.append(f"미세먼지 {r['pm']} · 내방영향: "
                     f"{visit_impact(r['pop'], r['rain'], r['snow'], r['tmax'], r['tmin'], r['pm'])}")
        lines.append("")

    rep = next((r for r in regions if r.get("ok")), None)
    if rep:
        cheer = pick_cheer(rep["am"] + rep["aft"], rep["pop"], rep["rain"],
                           rep["snow"], rep["tmax"], rep["tmin"], rep["pm"])
    else:
        cheer = random.choice(CHEERS["default"])
    lines += ["────────", cheer]
    return "\n".join(lines).strip()


# ── 주간 날씨 (월요일) ───────────────────────────────────
def build_weekly():
    now = datetime.now(KST)
    bdate, btime = base_fullday(now)
    tmfc = mid_tmfc(now)
    sat = now + timedelta(days=5)
    lines = [f"📅 {now.month}/{now.day}~{sat.month}/{sat.day} 주간 날씨", ""]

    rep0 = {}
    for rg in REGIONS:
        lines.append(f"[{rg['name']}]")
        try:
            items = fetch_vilage(rg, bdate, btime)
            # 월·화·수 (단기예보 D0~D2)
            for i in range(3):
                dt = now + timedelta(days=i)
                d = day_data(items, dt.strftime("%Y%m%d"))
                rain_mark = " ☔" if d["pop"] >= RAIN_WARN else ""
                lines.append(f"{WDAY[dt.weekday()]} {dt.month}/{dt.day} · {d['aft']}{rain_mark} · "
                             f"{d['tmin']}~{d['tmax']}°C · 강수 {d['pop']}%")
                if rg is REGIONS[0] and i == 0:
                    rep0 = d
            # 목·금·토 (중기예보 D3~D5)
            ta, land = fetch_mid(rg, tmfc)
            for n in (3, 4, 5):
                dt = now + timedelta(days=n)
                wf = land.get(f"wf{n}Pm") or land.get(f"wf{n}") or ""
                rn = land.get(f"rnSt{n}Pm") or land.get(f"rnSt{n}") or 0
                tmin = ta.get(f"taMin{n}"); tmax = ta.get(f"taMax{n}")
                rain_mark = " ☔" if int(rn or 0) >= RAIN_WARN else ""
                lines.append(f"{WDAY[dt.weekday()]} {dt.month}/{dt.day} · {mid_emoji(wf)}{rain_mark} · "
                             f"{tmin}~{tmax}°C · 강수 {rn}%")
        except Exception:
            lines.append("날씨 정보를 일시적으로 불러오지 못했어요")
        lines.append("")

    cheer = pick_cheer(rep0.get("am", "") + rep0.get("aft", ""), rep0.get("pop", 0),
                       rep0.get("rain", 0), rep0.get("snow", 0),
                       rep0.get("tmax", 0), rep0.get("tmin", 99))
    lines += ["────────", cheer]
    return "\n".join(lines).strip()


# ── 업계 뉴스 (구글 뉴스 RSS 후보 → 앤트로픽 AI 선별) ────
TELECOM_KEYWORDS = ["KT", "SKT", "LG유플러스", "통신사", "이동통신"]
DEVICE_KEYWORDS  = ["삼성 갤럭시", "애플 아이폰", "갤럭시", "아이폰", "스마트폰"]


def split_source(title):
    if " - " in title:
        t, src = title.rsplit(" - ", 1)
        return t.strip(), src.strip()
    return title, ""


def _collect(keywords, seen, cap):
    """주어진 키워드들로 어제 기사 후보를 모은다(seen으로 전역 중복 제거)."""
    today = datetime.now(KST).date()
    yest = today - timedelta(days=1)
    cand = []
    for kw in keywords:
        url = (f"https://news.google.com/rss/search?q={quote(kw)}+when:1d"
               f"&hl=ko&gl=KR&ceid=KR:ko")
        try:
            r = requests.get(url, timeout=20, headers={"User-Agent": "Mozilla/5.0"})
            root = ET.fromstring(r.content)
        except Exception as e:
            print(f"[뉴스] '{kw}' RSS 실패: {e!r}")
            continue
        for it in root.iter("item"):
            title = (it.findtext("title") or "").strip()
            link = (it.findtext("link") or "").strip()
            try:
                dt = parsedate_to_datetime(it.findtext("pubDate") or "").astimezone(KST).date()
            except Exception:
                dt = today
            if not title or not link or dt < yest:
                continue
            t, _ = split_source(title)
            if t in seen:
                continue
            seen.add(t)
            cand.append({"title": title, "link": link})
    print(f"[뉴스] {keywords[0]}… 그룹 후보 {len(cand)}건")
    return cand[:cap]


def collect_candidates():
    """통신사·제조사 후보를 따로 모아 합친다(각 그룹이 AI에 균형있게 전달되도록)."""
    seen = set()
    tele = _collect(TELECOM_KEYWORDS, seen, 25)
    dev = _collect(DEVICE_KEYWORDS, seen, 25)
    cand = tele + dev
    print(f"[뉴스] 전체 후보 {len(cand)}건 (통신 {len(tele)} / 제조 {len(dev)})")
    return cand


def pick_news_with_ai(cand):
    """AI가 통신사 2개·제조사 2개를 선별해 인덱스로 반환."""
    api_key = os.environ.get("ANTHROPIC_API_KEY")
    if not api_key:
        print("[뉴스] ANTHROPIC_API_KEY 없음 - AI 선별 불가")
        return None
    import anthropic
    listing = "\n".join(f"{i}. {c['title']}" for i, c in enumerate(cand))
    prompt = f"""아래는 어제 올라온 뉴스 기사 제목 목록(번호 포함)이야.

# 분류 기준
- '통신사': KT, SKT, LG유플러스 등 국내 이동통신사의 사업·요금제·정책·실적·서비스 관련 기사.
- '제조사': 삼성 갤럭시, 애플 아이폰 등 스마트폰 단말기의 출시·가격·신제품·업데이트 관련 기사.
- 야구(KT위즈 등), 증권/주가, 연예, 단순 광고/홍보, 통신·스마트폰과 무관한 기사는 제외.

# 할 일
- 통신사 동향에 가장 적합한 기사 2개, 제조사 동향에 가장 적합한 기사 2개를 골라.
- 가능한 최신·핵심 위주로. 적합한 게 부족하면 있는 만큼만.

# 출력 (JSON만, 설명 없이)
{{"telecom": [번호, 번호], "device": [번호, 번호]}}

# 기사 목록
{listing}
"""
    try:
        client = anthropic.Anthropic(api_key=api_key)
        resp = client.messages.create(
            model="claude-sonnet-4-6", max_tokens=200,
            system="너는 뉴스 제목을 분류해 JSON만 출력하는 도구야.",
            messages=[{"role": "user", "content": prompt}],
        )
        text = resp.content[0].text.strip()
        if "{" in text:
            text = text[text.index("{"):text.rindex("}") + 1]
        import json
        data = json.loads(text)
        print(f"[뉴스] AI 선별 결과: {data}")
        return data
    except Exception as e:
        print(f"[뉴스] AI 선별 실패: {e!r}")
        return None


def build_news_html():
    cand = collect_candidates()
    if not cand:
        print("[뉴스] 후보 없음 - 뉴스 블록 생략")
        return ""
    picked = pick_news_with_ai(cand)
    if not picked:
        print("[뉴스] AI 선별 결과 없음 - 뉴스 블록 생략")
        return ""

    def fmt(idx_list):
        out = []
        for i in idx_list or []:
            if isinstance(i, int) and 0 <= i < len(cand):
                t, src = split_source(cand[i]["title"])
                url = _html.escape(cand[i]["link"], quote=True)
                srctxt = f" ({_html.escape(src)})" if src else ""
                out.append(f'· <a href="{url}">{_html.escape(t)}</a>{srctxt}')
        return out

    blocks = []
    tele = fmt(picked.get("telecom"))
    dev = fmt(picked.get("device"))
    if tele:
        blocks.append("[통신사]\n" + "\n".join(tele))
    if dev:
        blocks.append("[제조사]\n" + "\n".join(dev))
    if not blocks:
        return ""
    yest = datetime.now(KST) - timedelta(days=1)
    header = f"📰 어제의 업계 동향 ({yest.month}/{yest.day} 기준)"
    return "━━━━━━━━\n" + header + "\n\n" + "\n\n".join(blocks)


# ── 오늘의 포춘쿠키 ───────────────────────────────
PICK_COUNT   = 3                       # 매일 뽑는 인원
PICK_LOG     = "data/pick_log.csv"     # 최근 선정 이력(골고루 순환용)
FORTUNE_HUMOR_RATE = 0.25              # 유머형 비율(나머지는 운세형)

PICK_SHORT = {
    "도농로": "도농", "구리리맥스": "구리", "자양번영로": "자양", "다산신도시": "다산",
    "건대입구역": "건대", "면목역": "면목", "상봉역": "상봉", "외대역": "외대",
    "금호동": "금호", "진접": "진접", "중계아울렛": "중계", "수유": "수유",
    "의정부로데오": "의정부", "옥정신도시": "옥정", "삼양로": "삼양", "먹골역": "먹골",
    "지행역": "지행", "상계역": "상계", "양주덕계": "덕계", "동해천곡": "동해",
    "석사": "석사", "강릉임당": "임당", "원주무실": "무실", "단구": "단구",
    "강릉유천": "유천", "홍천중앙": "홍천", "후평": "후평", "온의": "온의",
}

FORTUNE_B = [   # 운세형
    "오후에 좋은 소식이 들려옵니다", "오늘 첫 손님이 행운을 데려옵니다", "서두르지 않으면 술술 풀리는 날",
    "오늘은 웃는 얼굴이 최고의 무기입니다", "기다리던 연락이 오는 날", "작은 친절이 큰 결과로 돌아옵니다",
    "오늘은 운이 조용히 따라옵니다", "마음 편하게 시작하면 잘 풀립니다", "오후 3시 이후가 특히 좋습니다",
    "오늘 만난 사람이 다시 찾아옵니다", "서두른 만큼 놓치기 쉬운 날, 천천히 가세요", "예상 밖의 손님이 방문합니다",
    "오늘은 첫인상이 좋은 날입니다", "한 번 더 물어보면 답이 나옵니다", "기분 좋은 마무리가 기다립니다",
    "오늘은 준비한 만큼 나오는 날", "조급함만 내려놓으면 완벽한 하루", "뜻밖의 도움을 받게 됩니다",
    "오늘은 목소리가 밝은 날입니다", "포기하려던 순간에 기회가 옵니다", "오늘 하루 컨디션이 좋습니다",
    "먼저 인사하면 좋은 일이 생깁니다", "오늘은 집중력이 살아나는 날", "오전보다 오후가 좋은 날입니다",
    "미뤄둔 일을 오늘 하면 잘 됩니다", "오늘은 설명이 잘 통하는 날", "좋은 타이밍이 저절로 찾아옵니다",
    "오늘은 여유가 무기가 됩니다", "한 통의 전화가 하루를 바꿉니다", "오늘은 흐름을 타는 날입니다",
    "작은 성과가 쌓이는 하루", "오늘은 인복이 좋은 날입니다", "마감 전에 좋은 일이 생깁니다",
    "오늘은 침착함이 빛을 발합니다", "되돌아온 손님이 반가운 날", "오늘은 대화가 술술 풀립니다",
    "기분 좋은 우연이 찾아옵니다", "오늘은 눈에 띄는 하루가 됩니다", "한 걸음만 더 가면 되는 날",
    "오늘은 마음먹은 대로 되는 날", "좋은 소식은 조용히 옵니다", "오늘은 주변에서 챙겨주는 날",
    "차분하게 가면 반드시 됩니다", "오늘은 손님이 먼저 말을 겁니다", "기대하지 않은 곳에서 성과가 납니다",
    "오늘은 정리가 잘 되는 날", "한 번 더 확인하면 좋은 날", "오늘은 신뢰를 얻는 하루입니다",
    "오늘 하루 흐름이 부드럽습니다", "막판 뒤집기가 가능한 날", "오늘은 표정이 좋은 날입니다",
    "천천히 해도 늦지 않은 날", "오늘은 상담이 잘 풀립니다", "반가운 얼굴을 만나게 됩니다",
    "오늘은 마무리가 깔끔한 날", "기회는 오전에 옵니다", "오늘은 설득력이 좋은 날입니다",
    "고민하던 일이 해결됩니다", "오늘은 운이 뒤에서 밀어줍니다", "작은 배려가 오래 기억됩니다",
    "오늘은 컨디션 관리가 중요한 날", "좋은 리듬을 타는 하루", "오늘은 답이 빨리 나옵니다",
    "오늘 하루가 생각보다 짧게 느껴집니다", "기다림이 보상받는 날", "오늘은 손이 바쁜 날입니다",
    "차 한 잔의 여유가 필요한 날", "오늘은 응원받는 하루입니다", "막힌 곳이 뚫리는 날",
    "오늘은 눈치가 빛나는 날", "좋은 인연이 스쳐갑니다", "오늘은 흐트러짐 없는 하루",
    "오늘은 실수가 적은 날입니다", "기분 좋은 소식이 기다립니다", "오늘은 배우는 게 많은 날",
    "주변을 살피면 답이 보입니다", "오늘은 목표에 가까워지는 날", "한마디가 결정적인 날입니다",
    "오늘은 편안하게 흘러갑니다", "좋은 결과가 늦게 옵니다, 기다리세요", "오늘은 자신감이 붙는 날",
    "오늘은 도와주는 사람이 나타납니다", "시작이 좋으면 끝까지 좋은 날", "오늘은 말보다 행동이 통합니다",
    "오늘 하루 운이 고르게 좋습니다", "조용히 성과가 쌓이는 날", "오늘은 인사가 통하는 날입니다",
    "오늘은 기다려온 일이 시작됩니다", "마음이 가벼운 하루", "오늘은 정성이 통하는 날",
    "오늘은 뜻이 잘 전달됩니다", "좋은 흐름을 놓치지 마세요", "오늘은 판단이 정확한 날",
    "오늘은 잊고 있던 게 떠오릅니다", "작은 준비가 큰 차이를 만듭니다", "오늘은 순조로운 하루입니다",
    "오늘은 기분 전환이 필요한 날", "좋은 마무리로 이어집니다", "오늘은 노력이 보이는 날",
    "오늘은 응답이 빠른 날입니다", "기분 좋은 하루가 예상됩니다",
]
FORTUNE_C = [   # 유머형
    "오늘 커피는 얻어 마실 운입니다 ☕", "점심 메뉴 고르는 데 30분 쓸 예정 🍚", "오늘 왠지 칭찬받습니다. 이유는 모릅니다 🤔",
    "오늘 만보기가 놀랄 예정입니다 🚶", "오늘 웃을 일이 최소 3번 있습니다 😄", "오늘 누군가 간식을 사옵니다 🍪",
    "오늘 사진이 유난히 잘 나옵니다 📸", "오늘 노래 한 곡이 하루 종일 맴돕니다 🎵", "오늘 퇴근길이 유난히 가볍습니다 🌙",
    "오늘 배터리 잔량과 사투를 벌입니다 🔋", "오늘 택배가 예상보다 빨리 옵니다 📦", "오늘 날씨 얘기로 대화가 시작됩니다 🌤",
    "오늘 점심이 유난히 맛있습니다 🍽", "오늘 계단 대신 엘리베이터를 기다립니다 🛗", "오늘 지갑이 조금 가벼워집니다 💸",
    "오늘 우산을 챙길지 고민하게 됩니다 ☂️", "오늘 알람보다 먼저 눈이 떠집니다 ⏰", "오늘 누군가 이름을 두 번 부릅니다 📢",
    "오늘 물을 평소보다 많이 마십니다 💧", "오늘 의자가 유난히 편안합니다 🪑", "오늘 문자 답장이 빠릅니다 💬",
    "오늘 시계를 자주 확인하게 됩니다 ⌚", "오늘 주머니에서 잊고 있던 게 나옵니다 🎁", "오늘 하품이 전염됩니다 🥱",
    "오늘 신발끈이 한 번쯤 풀립니다 👟", "오늘 음악 추천이 정확합니다 🎧", "오늘 간식 유혹을 이기기 어렵습니다 🍫",
    "오늘 스마트폰을 두 번 찾습니다 📱", "오늘 웃음 포인트가 낮아집니다 😆", "오늘 커피 향이 유난히 좋습니다 ☕",
    "오늘 정리 욕구가 솟아납니다 🧹", "오늘 누군가와 취향이 맞습니다 🤝", "오늘 사소한 일에 기분이 좋아집니다 ✨",
    "오늘 시간이 빨리 갑니다 ⏳", "오늘 통화가 유난히 잘 들립니다 📞", "오늘 볼펜이 잘 나옵니다 🖊",
    "오늘 자리 정리가 하고 싶어집니다 🗂", "오늘 창밖을 한 번쯤 봅니다 🪟", "오늘 저녁 메뉴가 이미 정해졌습니다 🍜",
    "오늘 잔소리 대신 응원을 듣습니다 📣", "오늘 목이 마릅니다. 물 드세요 🚰", "오늘 스트레칭이 필요합니다 🧘",
    "오늘 인사를 두 번 받습니다 🙌", "오늘 기분 좋은 알림이 옵니다 🔔", "오늘 지나가다 아는 얼굴을 봅니다 👀",
    "오늘 손이 빨라집니다 ⚡", "오늘 무심코 한 말이 통합니다 💡", "오늘 뒷정리가 깔끔합니다 🧼",
    "오늘 하루가 짧게 느껴집니다 🌀", "오늘 퇴근시간이 정확합니다 🎯",
]


def _pick_people(n):
    """1명은 전원 완전 랜덤, 나머지는 미선정자 우선(점장 포함)."""
    import csv
    try:
        with open("data/roster.csv", newline="", encoding="utf-8-sig") as fp:
            rows = list(csv.reader(fp))
        people = [(r[0].strip(), r[1].strip()) for r in rows[1:]
                  if len(r) >= 2 and r[0].strip()]
    except Exception as exc:
        print("[포춘] 명단 로드 실패(%r) - 생략" % (exc,))
        return [], []
    if not people:
        return [], []

    history = []
    try:
        with open(PICK_LOG, newline="", encoding="utf-8-sig") as fp:
            history = [r for r in list(csv.reader(fp))[1:] if len(r) >= 3]
    except FileNotFoundError:
        pass

    last_idx = {}
    for i, r in enumerate(history):
        last_idx[r[1] + " " + r[2]] = i          # 뒤에 있을수록 최근

    picked = [random.choice(people)]             # ① 완전 랜덤 1명

    # ② 나머지: 미선정자 → 오래전 선정자 순
    unseen = [p for p in people if (p[0] + " " + p[1]) not in last_idx]
    random.shuffle(unseen)
    seen = sorted([p for p in people if (p[0] + " " + p[1]) in last_idx],
                  key=lambda p: last_idx[p[0] + " " + p[1]])
    pool = [p for p in (unseen + seen) if p not in picked]
    cand = pool[:max((n - 1) * 6, 20)]
    picked += random.sample(cand, min(n - 1, len(cand)))

    # 같은 매장 중복은 가능하면 피함
    if len(set(p[0] for p in picked)) < len(picked):
        for alt in pool[:60]:
            if len(set(p[0] for p in picked)) == len(picked):
                break
            if alt in picked:
                continue
            for i in range(1, len(picked)):      # 랜덤 1명(0번)은 그대로 둠
                others = [q[0] for j, q in enumerate(picked) if j != i]
                if picked[i][0] in others and alt[0] not in others:
                    picked[i] = alt
                    break
    return picked, history


def _save_pick_log(picked, history, today_str):
    """선정 이력 기록(최근 400줄 유지). 실패해도 메시지는 정상 게시."""
    import csv, os
    try:
        d = os.path.dirname(PICK_LOG)
        if d:
            os.makedirs(d, exist_ok=True)
        rows = [r for r in history if r[0] != today_str]
        rows += [[today_str, s, n] for s, n in picked]
        rows = rows[-400:]
        with open(PICK_LOG, "w", newline="", encoding="utf-8-sig") as fp:
            w = csv.writer(fp)
            w.writerow(["날짜", "매장", "이름"])
            w.writerows(rows)
    except Exception as exc:
        print("[포춘] 이력 저장 실패: %r" % (exc,))


def build_pick_text(today_str):
    """오늘의 포춘쿠키 메시지. 대상 없으면 빈 문자열."""
    picked, history = _pick_people(PICK_COUNT)
    if not picked:
        return ""
    lines = ["🥠 오늘의 포춘쿠키", ""]
    used = set()
    for store, name in picked:
        pool = FORTUNE_C if random.random() < FORTUNE_HUMOR_RATE else FORTUNE_B
        msg = random.choice(pool)
        for _ in range(5):
            if msg not in used:
                break
            msg = random.choice(pool)
        used.add(msg)
        lines.append("· %s %s 님 — %s" % (PICK_SHORT.get(store, store), name, msg))
    _save_pick_log(picked, history, today_str)
    return "\n".join(lines)


def tg_send(payload, label, retries=3):
    """텔레그램 전송(타임아웃·일시 오류 시 재시도). 성공 여부 반환."""
    for i in range(1, retries + 1):
        try:
            r = requests.post(f"{TG}/sendMessage", json=payload, timeout=(10, 60))
            j = r.json()
            if j.get("ok"):
                print(f"[{label}] 게시 완료" + (f" (재시도 {i}회차)" if i > 1 else ""))
                return True
            print(f"[{label}] 텔레그램 거부: {j}")
            return False
        except requests.exceptions.RequestException as exc:
            print(f"[{label}] 전송 실패({i}/{retries}): {exc!r}")
            if i < retries:
                time.sleep(5 * i)
    print(f"[{label}] 전송 최종 실패 - 이미 발송됐을 수 있으니 방을 확인하세요")
    return False


# ── 상권 대항전 (아침 알림) ───────────────────────
BATTLE_ON    = True
BATTLE_START = "2026-09-07"
BATTLE_END   = "2026-09-12"
BATTLE_PRIZES = [2000000, 1000000, 500000]   # 1~3위 시상금
BATTLE_AREAS = {
    "광구": ["도농로", "구리리맥스", "자양번영로", "다산신도시", "건대입구역",
             "면목역", "상봉역", "외대역", "금호동", "진접"],
    "경북": ["중계아울렛", "수유", "의정부로데오", "옥정신도시", "삼양로",
             "먹골역", "지행역", "상계역", "양주덕계"],
    "강원": ["동해천곡", "석사", "강릉임당", "원주무실", "단구",
             "강릉유천", "홍천중앙", "후평", "온의"],
}
B_S2A = {s: a for a, ss in BATTLE_AREAS.items() for s in ss}


def _battle_headcount():
    """상권별 인원수 + 매장수."""
    import csv
    head, stores = {}, {}
    try:
        with open("data/roster.csv", newline="", encoding="utf-8-sig") as fp:
            rows = list(csv.reader(fp))
    except Exception:
        return {}, {}
    for r in rows[1:]:
        if len(r) < 2:
            continue
        a = B_S2A.get(r[0].strip())
        if a:
            head[a] = head.get(a, 0) + 1
            stores.setdefault(a, set()).add(r[0].strip())
    return head, {a: len(v) for a, v in stores.items()}


def build_battle_text(today_str):
    """대항전 시작 알림(첫날) 또는 중간 현황(이후). 기간 밖이면 빈 문자열."""
    import csv
    if not (BATTLE_ON and BATTLE_START <= today_str <= BATTLE_END):
        return ""
    head, nstore = _battle_headcount()
    if not head:
        print("[대항전] 명단을 읽지 못해 생략")
        return ""

    if today_str == BATTLE_START:                     # 시작 알림
        _d1 = datetime.strptime(BATTLE_START, "%Y-%m-%d")
        _d2 = datetime.strptime(BATTLE_END, "%Y-%m-%d")
        L = [f"🏁 상권 대항전 시작! "
             f"({_d1.month}/{_d1.day}~{_d2.month}/{_d2.day})", ""]
        L.append("이번 주는 상권끼리 겨룹니다!")
        for a in BATTLE_AREAS:
            L.append(f"  · {a} — {nstore.get(a, 0)}개점 {head.get(a, 0)}명")
        L += ["", "📊 기준은 인당 평균 실적!",
              "   인원 수 상관없이 공정하게 겨룹니다.",
              "   딱 1인 1건이면 평균 1.0건이에요 💪", "",
              "🎁 시상금",
              f"   🥇 1위 {BATTLE_PRIZES[0]:,}원",
              f"   🥈 2위 {BATTLE_PRIZES[1]:,}원",
              f"   🥉 3위 {BATTLE_PRIZES[2]:,}원", "",
              "우리 상권, 다 같이 채워봅시다 🔥"]
        return "\n".join(L)

    # 중간 현황 (전일까지 누적)
    total = {a: 0 for a in BATTLE_AREAS}
    try:
        with open("data/daily_sales.csv", newline="", encoding="utf-8-sig") as fp:
            for r in list(csv.reader(fp))[1:]:
                if len(r) < 3 or not (BATTLE_START <= r[0] < today_str):
                    continue
                store = r[1].split(" ", 1)[0] if " " in r[1] else r[1]
                a = B_S2A.get(store.strip())
                if a:
                    try:
                        total[a] += int(r[2])
                    except ValueError:
                        pass
    except FileNotFoundError:
        print("[대항전] 실적 파일이 없어 현황 생략")
        return ""

    rows = sorted(({"area": a, "sum": total[a], "head": head.get(a, 0),
                    "avg": total[a] / head[a] if head.get(a) else 0}
                   for a in BATTLE_AREAS), key=lambda r: -r["avg"])
    d1 = datetime.strptime(BATTLE_START, "%Y-%m-%d").date()
    d2 = datetime.strptime(today_str, "%Y-%m-%d").date()
    day = sum(1 for i in range((d2 - d1).days)
              if (d1 + timedelta(i)).weekday() != 6)     # 전일까지 진행일수

    MEDAL = ["🥇", "🥈", "🥉"]
    L = [f"🏁 상권 대항전 현황 ({day}일차 종료 시점)", ""]
    for i, r in enumerate(rows):
        L.append(f"  {MEDAL[i]} {r['area']}  {r['avg']:.2f}건"
                 f"  ({r['sum']}건 / {r['head']}명)")
    gap = rows[0]["avg"] - rows[-1]["avg"]
    L.append("")
    if gap < 0.3:
        L.append(f"1위와 3위 차이 {gap:.2f}건! 초접전입니다 🔥🔥")
    else:
        L.append(f"1위와 3위 차이 {gap:.2f}건 · 아직 뒤집을 수 있어요 🔥")
    L.append("오늘도 1인 1건, 우리 상권 채워봅시다 💪")
    return "\n".join(L)


def main():
    weekday = datetime.now(KST).weekday()
    if weekday == 6:
        print("일요일은 게시하지 않습니다.")
        return

    FAIL_MARK = "날씨 정보를 일시적으로 불러오지 못했어요"

    def make():
        return build_weekly() if weekday == 0 else build_today()

    text = make()
    n_fail = text.count(FAIL_MARK)
    # 전 지역 실패면 기상청 일시 장애일 가능성 → 60초 뒤 한 번 더 시도
    if n_fail >= 5:
        print(f"[날씨] 전 지역 조회 실패({n_fail}건) · 60초 후 재시도")
        time.sleep(60)
        text = make()
        n_fail = text.count(FAIL_MARK)
        print(f"[날씨] 재시도 결과 실패 {n_fail}건")

    if n_fail >= 5:
        # 그래도 전부 실패하면 날씨는 빼고 뉴스만 게시(깨진 표 방지)
        print("[날씨] 재시도 후에도 전 지역 실패 - 날씨 생략하고 뉴스만 게시합니다")
        text = None

    # 뉴스 (실패해도 날씨는 정상 게시)
    try:
        news = build_news_html()
    except Exception as e:
        print(f"[뉴스] build_news_html 예외: {e!r}")
        news = ""
    print(f"[뉴스] 최종 뉴스블록 길이: {len(news)}")

    if text is None:
        if not news:
            print("[날씨] 게시할 내용이 없어 종료합니다")
            return
        now_ = datetime.now(KST)
        wk = "월화수목금토일"[now_.weekday()]
        body = _html.escape(
            f"🌤 {now_.month}/{now_.day}({wk}) 매장 날씨\n\n"
            "기상청 시스템 점검 등으로 날씨 정보를 가져오지 못했습니다.\n"
            "잠시 후 다시 안내드리겠습니다.") + "\n\n" + news
    else:
        body = _html.escape(text)      # 날씨 본문(특수문자 안전 처리)
        if news:
            body = body + "\n\n" + news   # 뉴스는 이미 HTML이라 그대로 붙임

    tg_send({"chat_id": TARGET_CHAT_ID, "text": body,
             "parse_mode": "HTML",
             "link_preview_options": {"is_disabled": True}}, "날씨")

    # ── 오늘의 포춘쿠키: 날씨 메시지에 이어서 게시 ──
    try:
        print("[포춘] 생성 시작")
        pick = build_pick_text(datetime.now(KST).strftime("%Y-%m-%d"))
        print(f"[포춘] 생성 결과 길이={len(pick)}")
        if pick:
            time.sleep(5)
            tg_send({"chat_id": TARGET_CHAT_ID, "text": pick}, "포춘")
        else:
            print("[포춘] 내용이 비어 게시하지 않음 (명단 확인 필요)")
    except Exception:
        print("[포춘] 처리 실패 - 상세:")
        traceback.print_exc()

    # ── 상권 대항전: 포춘쿠키 다음에 별도 게시
    try:
        bt = build_battle_text(datetime.now(KST).strftime("%Y-%m-%d"))
        if bt:
            time.sleep(5)
            tg_send({"chat_id": TARGET_CHAT_ID, "text": bt}, "대항전")
    except Exception:
        print("[대항전] 처리 실패 - 상세:")
        traceback.print_exc()


if __name__ == "__main__":
    try:
        main()
    except Exception:
        # 어떤 오류가 나도 워크플로 자체는 실패시키지 않고 원인만 남긴다
        print("[치명적 오류] main() 예외 - 상세:")
        traceback.print_exc()
