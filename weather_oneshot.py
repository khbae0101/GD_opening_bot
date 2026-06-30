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
    last = None
    for i in range(tries):
        try:
            return requests.get(url, params=params, timeout=timeout).json()
        except Exception as e:
            last = e
            time.sleep(2 * (i + 1))
    raise last
 
 
# ── 기상청 호출 ──────────────────────────────────────────
def kma_items(url, params):
    base = {"serviceKey": KMA_KEY, "dataType": "JSON", "numOfRows": 1000, "pageNo": 1}
    d = fetch_json(url, {**base, **params})
    body = d["response"]["body"]
    return body["items"]["item"]
 
 
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
 
 
def main():
    weekday = datetime.now(KST).weekday()
    if weekday == 6:
        print("일요일은 게시하지 않습니다.")
        return
    text = build_weekly() if weekday == 0 else build_today()
 
    # 뉴스 (실패해도 날씨는 정상 게시)
    try:
        news = build_news_html()
    except Exception as e:
        print(f"[뉴스] build_news_html 예외: {e!r}")
        news = ""
    print(f"[뉴스] 최종 뉴스블록 길이: {len(news)}")
 
    body = _html.escape(text)          # 날씨 본문(특수문자 안전 처리)
    if news:
        body = body + "\n\n" + news    # 뉴스는 이미 HTML이라 그대로 붙임
 
    requests.post(
        f"{TG}/sendMessage",
        json={"chat_id": TARGET_CHAT_ID, "text": body,
              "parse_mode": "HTML",
              "link_preview_options": {"is_disabled": True}},
        timeout=30,
    )
    print("날씨 게시 완료")
 
 
if __name__ == "__main__":
    main()
 
