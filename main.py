# -*- coding: utf-8 -*-
"""高雄國外業務職缺雷達
每天從 104 抓取職缺 → 產生靜態網頁 (docs/index.html) → 寄出新職缺通知信。
只用 Python 標準函式庫，不需要安裝任何套件。
"""
import json
import os
import re
import smtplib
import ssl
import time
from math import asin, cos, radians, sin, sqrt
from datetime import datetime, timedelta
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText
from email.utils import formataddr
from pathlib import Path
from urllib.parse import urlencode
from urllib.request import Request, urlopen
from zoneinfo import ZoneInfo

# ============================ 設定區 ============================
JOBCATS = ["2005003005"]          # 職務類別:國外業務。可加 "2005003002"(國外業務主管)
AREAS = ["6001016000"]            # 地區:高雄市
KEYWORD = ""                      # 額外關鍵字,留空 = 只用職務類別過濾
EXCLUDE_TITLE = r"國內(?!外)|工程師|[Ee]ngineer"  # 職稱排除:國內(非國內外)、工程師職
KEEP_DAYS = 30                    # 網頁只顯示最近 N 天內刊登/更新的職缺
STATE_PRUNE_DAYS = 120            # 超過 N 天沒再出現的職缺,從記錄中清除
SEND_WHEN_EMPTY = False           # 今日沒有新職缺時是否仍寄信
SITE_TITLE = "高雄・國外業務職缺雷達"
GD_LAT, GD_LON = 22.665621, 120.303256   # 出發點:捷運巨蛋站(R14)
ORIGIN_NAME = "捷運巨蛋站"
PEAK_H, PEAK_M = 8, 0             # 通勤時間以「下一個平日 08:00 出發」計算(Google 模式)
GOOGLE_MAX_PER_RUN = 200          # Google API 每次執行最多補算的職缺數(每筆約 2 次呼叫)
# ===============================================================

ROOT = Path(__file__).resolve().parent
DATA = ROOT / "data"
DOCS = ROOT / "docs"
TZ = ZoneInfo("Asia/Taipei")

UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
      "(KHTML, like Gecko) Chrome/126.0.0.0 Safari/537.36")
API = "https://www.104.com.tw/jobs/search/api/jobs"

EDU_MAP = {1: "學歷不拘", 2: "高中以上", 3: "專科以上", 4: "大學以上", 5: "碩士以上", 6: "博士"}
LANG_MAP = {1: "英文", 2: "日文", 3: "德文", 4: "法文", 5: "西班牙文", 6: "韓文",
            7: "義大利文", 8: "葡萄牙文", 9: "俄文", 10: "阿拉伯文", 11: "泰文",
            12: "越南文", 13: "印尼文", 14: "馬來文"}
ABILITY_MAP = {8: "精通", 4: "中等", 2: "略懂", 1: "略懂"}
SALARY_UNIT = {30: "時薪", 40: "日薪", 50: "月薪", 60: "年薪"}


def fetch_page(page: int, pagesize: int = 20) -> dict:
    params = {
        "jobsource": "index_s", "mode": "s", "order": "16",  # order=16: 依日期新→舊
        "jobcat": ",".join(JOBCATS), "area": ",".join(AREAS),
        "page": page, "pagesize": pagesize,  # pagesize 上限 20,超過會 400
    }
    if KEYWORD:
        params["keyword"] = KEYWORD
    req = Request(API + "?" + urlencode(params), headers={
        "User-Agent": UA, "Referer": "https://www.104.com.tw/jobs/search/", "Accept": "application/json",
    })
    for attempt in range(4):  # 連續請求太快會被 403 限流,退避重試
        try:
            with urlopen(req, timeout=30) as r:
                return json.load(r)
        except Exception:
            if attempt == 3:
                raise
            time.sleep(5 * (attempt + 1))


def fetch_all(max_pages: int = 20) -> list[dict]:
    jobs, page = [], 1
    while True:
        d = fetch_page(page)
        rows = d.get("data", [])
        jobs.extend(rows)
        meta = d.get("metadata", {}).get("pagination", {})
        if not rows or page >= min(meta.get("lastPage", 1), max_pages):
            break
        page += 1
        time.sleep(2)  # 溫和抓取,避免被限流
    return jobs


def parse_date(s: str) -> str:
    s = str(s)
    return f"{s[:4]}-{s[4:6]}-{s[6:8]}" if len(s) == 8 else s


def salary_info(low, high, s10):
    low, high = int(low or 0), int(high or 0)
    if (low == 0 and high == 0) or s10 == 10:
        return "面議(經常性薪資達4萬元)" if s10 == 10 and low == 0 else "面議", "negotiable", 0
    unit = SALARY_UNIT.get(s10, "")
    if high >= 9_999_999:
        return f"{unit} {low:,} 元以上", "salary", low
    if low == high:
        return f"{unit} {low:,} 元", "salary", low
    return f"{unit} {low:,}~{high:,} 元", "salary", low


def normalize(raw: dict) -> dict | None:
    name = raw.get("jobName", "")
    if EXCLUDE_TITLE and re.search(EXCLUDE_TITLE, name):
        return None
    desc = raw.get("description") or ""
    s_text, s_class, s_low = salary_info(raw.get("salaryLow"), raw.get("salaryHigh"), raw.get("s10"))
    edu = raw.get("optionEdu") or []
    langs = []
    for lr in raw.get("languageRequirements") or []:
        lname = LANG_MAP.get(lr.get("language"))
        if not lname:
            continue
        ab = lr.get("ability") or {}
        level = ABILITY_MAP.get(max(ab.values(), default=0), "")
        langs.append(f"{lname}{('(' + level + ')') if level else ''}")
    period = int(raw.get("period") or 0)
    trip = bool(re.search(r"出差|外派|駐外|海外派駐", desc))
    return {
        "no": str(raw.get("jobNo")),
        "name": name,
        "company": raw.get("custName", ""),
        "url": (raw.get("link") or {}).get("job", ""),
        "co_url": (raw.get("link") or {}).get("cust", ""),
        "area": raw.get("jobAddrNoDesc", ""),
        "industry": raw.get("coIndustryDesc", ""),
        "date": parse_date(raw.get("appearDate", "")),
        "apply": int(raw.get("applyCnt") or 0),
        "salary": s_text, "salary_class": s_class, "salary_low": s_low,
        "period": "經歷不拘" if period == 0 else f"{period} 年以上經歷",
        "edu": EDU_MAP.get(min(edu), "學歷不拘") if edu else "學歷不拘",
        "employees": int(raw.get("employeeCount") or 0),
        "langs": langs,
        "trip": trip,
        "remote": int(raw.get("remoteWorkType") or 0) > 0,
        "desc": re.sub(r"\s+", " ", desc)[:160],
        "lat": float(raw.get("lat") or 0) or None,
        "lon": float(raw.get("lon") or 0) or None,
    }


# ---------------------------- 距離/交通 ----------------------------

def haversine_km(lat1, lon1, lat2, lon2) -> float:
    lat1, lon1, lat2, lon2 = map(radians, (lat1, lon1, lat2, lon2))
    h = sin((lat2 - lat1) / 2) ** 2 + cos(lat1) * cos(lat2) * sin((lon2 - lon1) / 2) ** 2
    return 2 * 6371 * asin(sqrt(h))


def load_stations() -> list[dict]:
    p = DATA / "mrt_stations.json"
    return json.loads(p.read_text(encoding="utf-8")) if p.exists() else []


def enrich_location(j: dict, stations: list[dict]):
    """直線距離、最近捷運/輕軌站、Google Maps 路線連結。"""
    j["dist_km"] = j["drive_km"] = j["drive_min"] = j["transit_min"] = None
    j["near_st"] = j["near_line"] = ""
    j["walk_m"] = None
    j["peak"] = False  # True = 開車時間為 Google 尖峰路況,False = OSRM 無路況估計
    if not (j["lat"] and j["lon"]):
        return
    d = haversine_km(GD_LAT, GD_LON, j["lat"], j["lon"])
    if d > 80:  # 超出高雄市範圍 = 104 給的座標有誤,當作沒有座標
        j["lat"] = j["lon"] = None
        return
    j["dist_km"] = round(d, 1)
    if stations:
        best = min(stations, key=lambda s: haversine_km(s["lat"], s["lon"], j["lat"], j["lon"]))
        m = haversine_km(best["lat"], best["lon"], j["lat"], j["lon"]) * 1000
        if m <= 1500:  # 步行可達範圍內才顯示
            j["near_st"], j["near_line"] = best["name"], best["line"]
            j["walk_m"] = int(round(m / 50) * 50)
    base = f"https://www.google.com/maps/dir/?api=1&origin={GD_LAT},{GD_LON}&destination={j['lat']},{j['lon']}"
    j["gmap_drive"] = base + "&travelmode=driving"
    j["gmap_transit"] = base + "&travelmode=transit"


def fetch_drive_routes(jobs: list[dict], cache: dict):
    """用 OSRM 距離矩陣算「巨蛋站→公司」實際開車距離與時間,已算過的直接用快取。"""
    todo = [j for j in jobs if j["lat"] and j["no"] not in cache]
    for i in range(0, len(todo), 80):
        chunk = todo[i:i + 80]
        coords = f"{GD_LON},{GD_LAT};" + ";".join(f"{j['lon']},{j['lat']}" for j in chunk)
        dests = ";".join(str(k + 1) for k in range(len(chunk)))
        url = (f"https://router.project-osrm.org/table/v1/driving/{coords}"
               f"?sources=0&destinations={dests}&annotations=duration,distance")
        try:
            req = Request(url, headers={"User-Agent": UA})
            with urlopen(req, timeout=60) as r:
                d = json.load(r)
            if d.get("code") != "Ok":
                continue
            dist, dur = d["distances"][0], d["durations"][0]
            for k, j in enumerate(chunk):
                if dist[k] is not None:
                    cache[j["no"]] = {"km": round(dist[k] / 1000, 1), "min": max(1, round(dur[k] / 60))}
        except Exception as e:
            print(f"OSRM 查詢失敗(明天會再試): {e}")
        time.sleep(1)
    for j in jobs:
        r = cache.get(j["no"])
        if r and j["lat"]:
            if r.get("km") is not None:
                j["drive_km"], j["drive_min"] = r["km"], r["min"]
            if r.get("g_min"):  # Google 尖峰路況版本優先
                j["drive_km"], j["drive_min"], j["peak"] = r.get("g_km", r.get("km")), r["g_min"], True
            if r.get("transit_min"):
                j["transit_min"] = r["transit_min"]


def fetch_google_routes(jobs: list[dict], cache: dict, now: datetime):
    """(選用)有設 GOOGLE_MAPS_API_KEY 時,用 Google Routes API 補兩件事:
    1. 開車時間升級成「平日早上尖峰出發」的塞車估計(TRAFFIC_AWARE)
    2. 大眾運輸通勤分鐘數
    以「下一個平日 08:00 出發」計算。每次執行最多處理 GOOGLE_MAX_PER_RUN 筆職缺
    (每筆約 2 次呼叫);算過的存進 routes.json 快取,同一筆職缺不會重複呼叫。"""
    key = os.environ.get("GOOGLE_MAPS_API_KEY", "")
    if not key:
        return
    dep = now.replace(hour=PEAK_H, minute=PEAK_M, second=0, microsecond=0) + timedelta(days=1)
    while dep.weekday() >= 5:
        dep += timedelta(days=1)
    dep_utc = dep.astimezone(ZoneInfo("UTC")).strftime("%Y-%m-%dT%H:%M:%SZ")

    def route(j, mode):
        body = {
            "origin": {"location": {"latLng": {"latitude": GD_LAT, "longitude": GD_LON}}},
            "destination": {"location": {"latLng": {"latitude": j["lat"], "longitude": j["lon"]}}},
            "travelMode": mode, "departureTime": dep_utc,
        }
        if mode == "DRIVE":
            body["routingPreference"] = "TRAFFIC_AWARE"
        req = Request("https://routes.googleapis.com/directions/v2:computeRoutes",
                      data=json.dumps(body).encode(), headers={
                          "Content-Type": "application/json", "X-Goog-Api-Key": key,
                          "X-Goog-FieldMask": "routes.duration,routes.distanceMeters",
                      })
        try:
            with urlopen(req, timeout=30) as r:
                d = json.load(r)
            top = (d.get("routes") or [{}])[0]
            sec = int(top.get("duration", "0s").rstrip("s") or 0)
            km = round(int(top.get("distanceMeters", 0)) / 1000, 1)
            return (km, max(1, round(sec / 60))) if sec else None
        except Exception:
            return None

    todo = [j for j in jobs if j["lat"] and (
        cache.get(j["no"], {}).get("g_min") is None
        or cache.get(j["no"], {}).get("transit_min") is None)][:GOOGLE_MAX_PER_RUN]
    for j in todo:
        c = cache.setdefault(j["no"], {})
        if c.get("g_min") is None:
            r = route(j, "DRIVE")
            if r:
                c["g_km"], c["g_min"] = r
                j["drive_km"], j["drive_min"], j["peak"] = r[0], r[1], True
        if c.get("transit_min") is None:
            r = route(j, "TRANSIT")
            if r:
                c["transit_min"] = r[1]
                j["transit_min"] = r[1]
        time.sleep(0.2)


def load_state() -> dict:
    p = DATA / "state.json"
    if p.exists():
        return json.loads(p.read_text(encoding="utf-8"))
    return {}


def main():
    now = datetime.now(TZ)
    today = now.strftime("%Y-%m-%d")
    cutoff = (now - timedelta(days=KEEP_DAYS)).strftime("%Y-%m-%d")

    raw_jobs = fetch_all()
    seen_nos, jobs = set(), []
    for r in raw_jobs:
        j = normalize(r)
        if j and j["no"] not in seen_nos:
            seen_nos.add(j["no"])
            jobs.append(j)

    # 標記新職缺:第一次出現在記錄中的才算 NEW
    state = load_state()
    for j in jobs:
        if j["no"] not in state:
            state[j["no"]] = {"first_seen": today, "last_seen": today}
        else:
            state[j["no"]]["last_seen"] = today
        j["first_seen"] = state[j["no"]]["first_seen"]
        j["is_new"] = j["first_seen"] == today

    # 清掉太久沒出現的記錄,避免檔案無限成長
    prune = (now - timedelta(days=STATE_PRUNE_DAYS)).strftime("%Y-%m-%d")
    state = {k: v for k, v in state.items() if v["last_seen"] >= prune}

    display = [j for j in jobs if j["date"] >= cutoff]

    # 距離與交通:直線距離、最近捷運站、開車路線(OSRM)、大眾運輸(選用)
    stations = load_stations()
    routes_p = DATA / "routes.json"
    routes = json.loads(routes_p.read_text(encoding="utf-8")) if routes_p.exists() else {}
    for j in display:
        enrich_location(j, stations)
    fetch_drive_routes(display, routes)
    fetch_google_routes(display, routes, now)
    routes = {k: v for k, v in routes.items() if k in state}  # 跟著 state 一起清舊資料

    # 離巨蛋站近的優先(沒有座標的排最後)
    display.sort(key=lambda j: (j["drive_km"] if j["drive_km"] is not None
                                else j["dist_km"] if j["dist_km"] is not None else 9999))
    new_jobs = sorted([j for j in display if j["is_new"]],
                      key=lambda j: (j["drive_km"] if j["drive_km"] is not None else 9999))

    first_run = not (DATA / "state.json").exists()
    DATA.mkdir(exist_ok=True)
    (DATA / "state.json").write_text(json.dumps(state, ensure_ascii=False, indent=1), encoding="utf-8")
    (DATA / "routes.json").write_text(json.dumps(routes, ensure_ascii=False, indent=1), encoding="utf-8")
    (DATA / "jobs.json").write_text(json.dumps(display, ensure_ascii=False, indent=1), encoding="utf-8")

    build_site(display, now)
    print(f"共 {len(display)} 筆職缺(最近 {KEEP_DAYS} 天),今日新增 {len(new_jobs)} 筆")

    # 第一次執行時所有職缺都是「新的」,不寄信以免轟炸信箱
    if first_run:
        print("首次執行,建立基準資料,不寄信。明天開始只通知真正的新職缺。")
        return
    if new_jobs or SEND_WHEN_EMPTY:
        send_email(new_jobs, len(display), now)


# ---------------------------- 網頁產生 ----------------------------

def build_site(jobs: list[dict], now: datetime):
    DOCS.mkdir(exist_ok=True)
    (DOCS / ".nojekyll").write_text("", encoding="utf-8")
    tpl = (ROOT / "template.html").read_text(encoding="utf-8")
    html = (tpl
            .replace("__TITLE__", SITE_TITLE)
            .replace("__UPDATED__", now.strftime("%Y-%m-%d %H:%M"))
            .replace("__DATA__", json.dumps(jobs, ensure_ascii=False)))
    (DOCS / "index.html").write_text(html, encoding="utf-8")


# ---------------------------- 寄信 ----------------------------

def job_row_html(j: dict) -> str:
    badges = []
    if j["langs"]:
        badges.append("語言:" + "、".join(j["langs"]))
    if j["trip"]:
        badges.append("需出差/外派")
    meta = f'{j["area"]}|{j["period"]}|{j["edu"]}' + ("|" + "|".join(badges) if badges else "")
    color = "#d97706" if j["salary_class"] == "negotiable" else "#059669"
    parts = []
    if j.get("drive_km") is not None:
        peak = "尖峰約" if j.get("peak") else "約"
        parts.append(f"🚗 開車 {j['drive_km']} km・{peak} {j['drive_min']} 分")
    elif j.get("dist_km") is not None:
        parts.append(f"📍 直線約 {j['dist_km']} km")
    if j.get("transit_min"):
        parts.append(f"🚌 大眾運輸約 {j['transit_min']} 分")
    if j.get("near_st"):
        parts.append(f"🚇 近{j['near_line']}{j['near_st']}站(約{j['walk_m']}m)")
    links = ""
    if j.get("gmap_transit"):
        links = (f'|<a href="{j["gmap_drive"]}" style="color:#1d4ed8">開車路線</a>'
                 f'|<a href="{j["gmap_transit"]}" style="color:#1d4ed8">大眾運輸</a>')
    traffic = f'<div style="color:#374151;font-size:13px;margin-top:4px">{"|".join(parts)}{links}</div>' if parts else ""
    return f"""
    <tr><td style="padding:12px 16px;border-bottom:1px solid #e5e7eb">
      <a href="{j['url']}" style="font-size:16px;font-weight:700;color:#1d4ed8;text-decoration:none">{j['name']}</a>
      <div style="color:#374151;margin-top:2px">{j['company']}<span style="color:#9ca3af">({j['industry']})</span></div>
      <div style="margin-top:4px;font-weight:700;color:{color}">{j['salary']}</div>
      {traffic}
      <div style="color:#6b7280;font-size:13px;margin-top:4px">{meta}</div>
      <div style="color:#6b7280;font-size:13px;margin-top:2px">刊登 {j['date']}|{j['apply']} 人應徵</div>
    </td></tr>"""


def send_email(new_jobs: list[dict], total: int, now: datetime):
    user = os.environ.get("GMAIL_USER", "")
    pwd = os.environ.get("GMAIL_APP_PASSWORD", "")
    to = [x.strip() for x in os.environ.get("EMAIL_TO", "").split(",") if x.strip()]
    if not (user and pwd and to):
        print("未設定 GMAIL_USER / GMAIL_APP_PASSWORD / EMAIL_TO,略過寄信")
        return
    site_url = os.environ.get("SITE_URL", "")
    date_str = now.strftime("%m/%d")
    subject = f"【職缺快報 {date_str}】高雄國外業務 今日新增 {len(new_jobs)} 筆"
    rows = "".join(job_row_html(j) for j in new_jobs) or \
        '<tr><td style="padding:16px;color:#6b7280">今天沒有新職缺</td></tr>'
    link = f'<p style="margin:16px 0"><a href="{site_url}" style="color:#1d4ed8">查看全部 {total} 筆職缺 →</a></p>' if site_url else ""
    body = f"""
    <div style="font-family:'Microsoft JhengHei',sans-serif;max-width:640px;margin:0 auto;color:#111827">
      <h2 style="margin:16px 0 4px">高雄・國外業務 職缺快報</h2>
      <p style="color:#6b7280;margin:0 0 16px">{now.strftime('%Y-%m-%d')}|今日新增 {len(new_jobs)} 筆|追蹤中共 {total} 筆|依{ORIGIN_NAME}距離近→遠排序</p>
      <table style="border-collapse:collapse;width:100%;border:1px solid #e5e7eb;border-radius:8px">{rows}</table>
      {link}
      <p style="color:#9ca3af;font-size:12px">資料來源:104 人力銀行|此信由職缺雷達自動發送</p>
    </div>"""
    msg = MIMEMultipart("alternative")
    msg["Subject"] = subject
    msg["From"] = formataddr((SITE_TITLE, user))
    msg["To"] = ", ".join(to)
    msg.attach(MIMEText(body, "html", "utf-8"))
    with smtplib.SMTP_SSL("smtp.gmail.com", 465, context=ssl.create_default_context()) as s:
        s.login(user, pwd)
        s.sendmail(user, to, msg.as_string())
    print(f"已寄出通知信給 {', '.join(to)}")


if __name__ == "__main__":
    main()
