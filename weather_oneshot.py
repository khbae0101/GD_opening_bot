"""
매장 지역별 날씨 알림 (1회 실행용)
─────────────────────────────────────────────
- 월요일      : 지역별 '주간(월~토)' 날씨
- 화~토요일   : 지역별 '오늘' 날씨 (+ 특보 자동판단, 내방영향)
- Open-Meteo(무료, API 키 불필요) 사용. 모든 날짜는 한국시간(KST) 기준.
- cron-job.org가 매일 아침(월~토) 정해진 시각에 이 워크플로를 호출해 실행.
 
비밀값(Secrets):
  TELEGRAM_TOKEN_3 : 날씨 봇 토큰
  TARGET_CHAT_ID_3 : 날씨를 올릴 방 ID
"""
 
import os
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
 
RAIN_WARN = 60      # 강수확률(%) 이상 내방 주의
HEAT_WARN = 33      # 최고기온(°C) 이상 폭염 수준
COLD_WARN = -12     # 최저기온(°C) 이하 한파 수준
WIND_WARN = 14      # 최대풍속(m/s) 이상 강풍 수준
RAIN_HEAVY = 30     # 일 강수량(mm) 이상 호우 수준
SNOW_HEAVY = 5      # 일 강설량(cm) 이상 대설 수준
 
WMO = {
    0: "맑음", 1: "대체로 맑음", 2: "구름조금", 3: "흐림",
    45: "안개", 48: "안개", 51: "이슬비", 53: "이슬비", 55: "이슬비",
    61: "비", 63: "비", 65: "강한 비", 66: "어는 비", 67: "어는 비",
    71: "눈", 73: "눈", 75: "강한 눈", 77: "싸락눈",
    80: "소나기", 81: "소나기", 82: "강한 소나기",
    85: "눈", 86: "강한 눈", 95: "뇌우", 96: "뇌우", 99: "뇌우",
}
WDAY = "월화수목금토일"
 
 
def pm_grade(pm10, pm25):
    def g10(v): return 1 if v <= 30 else 2 if v <= 80 else 3 if v <= 150 else 4
    def g25(v): return 1 if v <= 15 else 2 if v <= 35 else 3 if v <= 75 else 4
    grade = max(g10(pm10 or 0), g25(pm25 or 0))
    return {1: "좋음", 2: "보통", 3: "나쁨", 4: "매우나쁨"}[grade]
 
 
def fetch_daily(lat, lon, start, end):
    """start~end(한국날짜)의 일별 날씨를 가져온다."""
    return requests.get(
        "https://api.open-meteo.com/v1/forecast",
        params={
            "latitude": lat, "longitude": lon,
            "hourly": "weather_code",
            "daily": "temperature_2m_max,temperature_2m_min,precipitation_sum,"
                     "snowfall_sum,wind_speed_10m_max,weather_code,precipitation_probability_max",
            "timezone": "Asia/Seoul", "wind_speed_unit": "ms",
            "start_date": start, "end_date": end,
        }, timeout=30,
    ).json()
 
 
def fetch_airquality(lat, lon, day):
    aq = requests.get(
        "https://air-quality-api.open-meteo.com/v1/air-quality",
        params={"latitude": lat, "longitude": lon, "hourly": "pm10,pm2_5",
                "timezone": "Asia/Seoul", "start_date": day, "end_date": day},
        timeout=30,
    ).json()
    pm10 = [x for x in aq["hourly"]["pm10"] if x is not None]
    pm25 = [x for x in aq["hourly"]["pm2_5"] if x is not None]
    a10 = sum(pm10) / max(1, len(pm10))
    a25 = sum(pm25) / max(1, len(pm25))
    return pm_grade(a10, a25)
 
 
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
    if pm in ("나쁨", "매우나쁨"): reasons.append("미세먼지")
    return f"주의 ({'·'.join(reasons)})" if reasons else "양호"
 
 
# ── 오늘 날씨 (화~토) ────────────────────────────────────
def build_today():
    now = datetime.now(KST)
    today = now.strftime("%Y-%m-%d")
    lines = [f"🌤 {now.month}/{now.day}({WDAY[now.weekday()]}) 매장 날씨", ""]
 
    regions = []
    for rg in REGIONS:
        w = fetch_daily(rg["lat"], rg["lon"], today, today)
        d = w["daily"]
        hours = w["hourly"]["time"]; codes = w["hourly"]["weather_code"]
        def at(hh):
            key = f"{today}T{hh:02d}:00"
            return codes[hours.index(key)] if key in hours else d["weather_code"][0]
        regions.append({
            "name": rg["name"],
            "am": WMO.get(at(9), "흐림"), "pm": WMO.get(at(15), "흐림"),
            "tmax": round(d["temperature_2m_max"][0]), "tmin": round(d["temperature_2m_min"][0]),
            "pop": d["precipitation_probability_max"][0] or 0,
            "rain": d["precipitation_sum"][0] or 0, "snow": d["snowfall_sum"][0] or 0,
            "wind": d["wind_speed_10m_max"][0] or 0,
            "pm": fetch_airquality(rg["lat"], rg["lon"], today),
        })
 
    al = []
    for r in regions:
        for a in alerts_for(r["tmax"], r["tmin"], r["rain"], r["snow"], r["wind"]):
            al.append(f"· {r['name'].split(' · ')[-1]} {a}")
    if al:
        lines += ["⚠️ 기상특보(자동판단)"] + al + [""]
 
    for r in regions:
        lines.append(f"[{r['name']}]")
        lines.append(f"오전 {r['am']} → 오후 {r['pm']}")
        lines.append(f"강수확률 {r['pop']}% · 기온 {r['tmin']}~{r['tmax']}°C")
        lines.append(f"미세먼지 {r['pm']} · 내방영향: "
                     f"{visit_impact(r['pop'], r['rain'], r['snow'], r['tmax'], r['tmin'], r['pm'])}")
        lines.append("")
    return "\n".join(lines).strip()
 
 
# ── 주간 날씨 (월요일) ───────────────────────────────────
def build_weekly():
    now = datetime.now(KST)
    monday = now - timedelta(days=now.weekday())   # 이번 주 월요일
    saturday = monday + timedelta(days=5)          # 토요일
    start = monday.strftime("%Y-%m-%d"); end = saturday.strftime("%Y-%m-%d")
    lines = [f"📅 {monday.month}/{monday.day}~{saturday.month}/{saturday.day} 주간 날씨", ""]
 
    for rg in REGIONS:
        w = fetch_daily(rg["lat"], rg["lon"], start, end)
        d = w["daily"]
        lines.append(f"[{rg['name']}]")
        for i, day in enumerate(d["time"]):
            dt = datetime.strptime(day, "%Y-%m-%d")
            cond = WMO.get(d["weather_code"][i], "흐림")
            rain_mark = " ☔" if (d["precipitation_probability_max"][i] or 0) >= RAIN_WARN else ""
            lines.append(
                f"{WDAY[dt.weekday()]} {dt.month}/{dt.day} · {cond}{rain_mark} · "
                f"{round(d['temperature_2m_min'][i])}~{round(d['temperature_2m_max'][i])}°C · "
                f"강수 {d['precipitation_probability_max'][i] or 0}%"
            )
        lines.append("")
    return "\n".join(lines).strip()
 
 
def main():
    weekday = datetime.now(KST).weekday()  # 월=0 ... 일=6
    if weekday == 6:
        print("일요일은 게시하지 않습니다.")
        return
    text = build_weekly() if weekday == 0 else build_today()
    requests.post(f"{TG}/sendMessage",
                  json={"chat_id": TARGET_CHAT_ID, "text": text}, timeout=30)
    print("날씨 게시 완료")
 
 
if __name__ == "__main__":
    main()
 
