"""
매장 지역별 날씨 알림 (1회 실행용)
─────────────────────────────────────────────
- 월요일      : 지역별 '주간(월~토)' 날씨
- 화~토요일   : 지역별 '오늘' 날씨 (+ 특보 자동판단, 내방영향)
- 맨 아래 그날 날씨에 맞는 응원 멘트를 매번 다르게 한 줄 추가
- Open-Meteo(무료, API 키 불필요) 사용. 모든 날짜는 한국시간(KST) 기준.
- 네트워크 일시 오류에 대비해 재시도, 미세먼지는 실패해도 넘어감.
 
비밀값(Secrets): TELEGRAM_TOKEN_3 / TARGET_CHAT_ID_3
"""
 
import os
import time
import random
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo
 
import requests
 
TELEGRAM_TOKEN = os.environ["TELEGRAM_TOKEN_3"]
TARGET_CHAT_ID = int(os.environ["TARGET_CHAT_ID_3"])
KST = ZoneInfo("Asia/Seoul")
TG = f"https://api.telegram.org/bot{TELEGRAM_TOKEN}"
 
REGIONS = [
    {"name": "수도권 · 서울/구리/남양주/의정부", "lat": 37.55, "lon": 127.05},
    {"name": "강원 · 강릉/동해",                 "lat": 37.75, "lon": 128.90},
    {"name": "강원 · 원주",                      "lat": 37.34, "lon": 127.92},
    {"name": "강원 · 춘천",                      "lat": 37.88, "lon": 127.73},
]
 
RAIN_WARN = 60
HEAT_WARN = 33
COLD_WARN = -12
WIND_WARN = 14
RAIN_HEAVY = 30
SNOW_HEAVY = 5
 
WMO = {
    0: "☀️ 맑음", 1: "🌤️ 대체로 맑음", 2: "⛅ 구름조금", 3: "☁️ 흐림",
    45: "🌫️ 안개", 48: "🌫️ 안개", 51: "🌦️ 이슬비", 53: "🌦️ 이슬비", 55: "🌦️ 이슬비",
    61: "🌧️ 비", 63: "🌧️ 비", 65: "🌧️ 강한 비", 66: "🌧️ 어는 비", 67: "🌧️ 어는 비",
    71: "🌨️ 눈", 73: "🌨️ 눈", 75: "❄️ 강한 눈", 77: "🌨️ 싸락눈",
    80: "🌦️ 소나기", 81: "🌦️ 소나기", 82: "⛈️ 강한 소나기",
    85: "🌨️ 눈", 86: "❄️ 강한 눈", 95: "⛈️ 뇌우", 96: "⛈️ 뇌우", 99: "⛈️ 뇌우",
}
WDAY = "월화수목금토일"
 
# ── 응원 멘트 (그날 날씨에 맞춰 랜덤으로 하나 선택) ──────────
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
    elif pop >= RAIN_WARN or rain >= RAIN_HEAVY or "비" in cond or "소나기" in cond or "뇌우" in cond:
        key = "rain"
    elif tmax >= HEAT_WARN:
        key = "heat"
    elif tmin <= COLD_WARN:
        key = "cold"
    elif "나쁨" in pm:
        key = "dust"
    elif "흐림" in cond or "구름" in cond or "안개" in cond:
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
 
 
def pm_grade(pm10, pm25):
    def g10(v): return 1 if v <= 30 else 2 if v <= 80 else 3 if v <= 150 else 4
    def g25(v): return 1 if v <= 15 else 2 if v <= 35 else 3 if v <= 75 else 4
    grade = max(g10(pm10 or 0), g25(pm25 or 0))
    return {1: "🟢 좋음", 2: "🟡 보통", 3: "🟠 나쁨", 4: "🔴 매우나쁨"}[grade]
 
 
def fetch_daily(lat, lon, start, end):
    return fetch_json(
        "https://api.open-meteo.com/v1/forecast",
        {
            "latitude": lat, "longitude": lon,
            "hourly": "weather_code",
            "daily": "temperature_2m_max,temperature_2m_min,precipitation_sum,"
                     "snowfall_sum,wind_speed_10m_max,weather_code,precipitation_probability_max",
            "timezone": "Asia/Seoul", "wind_speed_unit": "ms",
            "start_date": start, "end_date": end,
        },
    )
 
 
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
        a10 = sum(pm10) / max(1, len(pm10))
        a25 = sum(pm25) / max(1, len(pm25))
        return pm_grade(a10, a25)
    except Exception:
        return "⚪ 정보없음"
 
 
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
 
 
def build_today():
    now = datetime.now(KST)
    today = now.strftime("%Y-%m-%d")
    lines = [f"🌤 {now.month}/{now.day}({WDAY[now.weekday()]}) 매장 날씨", ""]
 
    regions = []
    for rg in REGIONS:
        try:
            w = fetch_daily(rg["lat"], rg["lon"], today, today)
            d = w["daily"]
            hours = w["hourly"]["time"]; codes = w["hourly"]["weather_code"]
            def at(hh):
                key = f"{today}T{hh:02d}:00"
                return codes[hours.index(key)] if key in hours else d["weather_code"][0]
            regions.append({
                "name": rg["name"], "ok": True,
                "am": WMO.get(at(9), "☁️ 흐림"), "pm": WMO.get(at(15), "☁️ 흐림"),
                "tmax": round(d["temperature_2m_max"][0]), "tmin": round(d["temperature_2m_min"][0]),
                "pop": d["precipitation_probability_max"][0] or 0,
                "rain": d["precipitation_sum"][0] or 0, "snow": d["snowfall_sum"][0] or 0,
                "wind": d["wind_speed_10m_max"][0] or 0,
                "pm": fetch_airquality(rg["lat"], rg["lon"], today),
            })
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
        lines.append(f"오전 {r['am']} → 오후 {r['pm']}")
        lines.append(f"강수확률 {r['pop']}% · 기온 {r['tmin']}~{r['tmax']}°C")
        lines.append(f"미세먼지 {r['pm']} · 내방영향: "
                     f"{visit_impact(r['pop'], r['rain'], r['snow'], r['tmax'], r['tmin'], r['pm'])}")
        lines.append("")
 
    # 응원 멘트 (수도권 기준)
    rep = next((r for r in regions if r.get("ok")), None)
    if rep:
        cheer = pick_cheer(rep["am"] + rep["pm"], rep["pop"], rep["rain"],
                           rep["snow"], rep["tmax"], rep["tmin"], rep["pm"])
    else:
        cheer = random.choice(CHEERS["default"])
    lines += ["────────", cheer]
    return "\n".join(lines).strip()
 
 
def build_weekly():
    now = datetime.now(KST)
    monday = now - timedelta(days=now.weekday())
    saturday = monday + timedelta(days=5)
    start = monday.strftime("%Y-%m-%d"); end = saturday.strftime("%Y-%m-%d")
    lines = [f"📅 {monday.month}/{monday.day}~{saturday.month}/{saturday.day} 주간 날씨", ""]
 
    mon = {}  # 수도권 월요일 (응원 멘트 기준)
    for idx, rg in enumerate(REGIONS):
        lines.append(f"[{rg['name']}]")
        try:
            w = fetch_daily(rg["lat"], rg["lon"], start, end)
            d = w["daily"]
            for i, day in enumerate(d["time"]):
                dt = datetime.strptime(day, "%Y-%m-%d")
                cond = WMO.get(d["weather_code"][i], "☁️ 흐림")
                rain_mark = " ☔" if (d["precipitation_probability_max"][i] or 0) >= RAIN_WARN else ""
                lines.append(
                    f"{WDAY[dt.weekday()]} {dt.month}/{dt.day} · {cond}{rain_mark} · "
                    f"{round(d['temperature_2m_min'][i])}~{round(d['temperature_2m_max'][i])}°C · "
                    f"강수 {d['precipitation_probability_max'][i] or 0}%"
                )
                if idx == 0 and i == 0:  # 수도권 월요일
                    mon = {"cond": cond, "pop": d["precipitation_probability_max"][i] or 0,
                           "rain": d["precipitation_sum"][i] or 0, "snow": d["snowfall_sum"][i] or 0,
                           "tmax": d["temperature_2m_max"][i], "tmin": d["temperature_2m_min"][i]}
        except Exception:
            lines.append("날씨 정보를 일시적으로 불러오지 못했어요")
        lines.append("")
 
    cheer = pick_cheer(mon.get("cond", ""), mon.get("pop", 0), mon.get("rain", 0),
                       mon.get("snow", 0), mon.get("tmax", 0), mon.get("tmin", 99))
    lines += ["────────", cheer]
    return "\n".join(lines).strip()
 
 
def main():
    weekday = datetime.now(KST).weekday()
    if weekday == 6:
        print("일요일은 게시하지 않습니다.")
        return
    text = build_weekly() if weekday == 0 else build_today()
    requests.post(f"{TG}/sendMessage",
                  json={"chat_id": TARGET_CHAT_ID, "text": text}, timeout=30)
    print("날씨 게시 완료")
 
 
if __name__ == "__main__":
    main()
 
