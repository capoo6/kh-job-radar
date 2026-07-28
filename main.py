# -*- coding: utf-8 -*-
"""高雄國外業務職缺雷達
每天從 104 抓取職缺 → 產生靜態網頁 (docs/index.html) → 寄出新職缺通知信。
只用 Python 標準函式庫，不需要安裝任何套件。
"""
import difflib
import hashlib
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
from urllib.error import HTTPError
from urllib.parse import urlencode
from urllib.request import (HTTPCookieProcessor, HTTPSHandler, Request,
                            build_opener, urlopen)
from zoneinfo import ZoneInfo

# ============================ 設定區 ============================
JOBCATS = ["2005003005"]          # 職務類別:國外業務。可加 "2005003002"(國外業務主管)
AREAS = ["6001016000"]            # 地區:高雄市
KEYWORD = ""                      # 額外關鍵字,留空 = 只用職務類別過濾
EXCLUDE_TITLE = r"國內(?!外)|工程師|[Ee]ngineer"  # 職稱排除:國內(非國內外)、工程師職
MAX_EDU = 4                       # 學歷上限:4=大學。要求碩士(5)/博士(6)以上的職缺排除
EXCLUDE_INDUSTRIES = [            # 行業別含這些字樣的職缺不顯示(比對 104 的產業別欄位)
    "補習班", "書籍出版", "醫療器材製造", "食品什貨批發", "飲料店",
    "不動產經營", "其他教育服務", "旅館", "證券及期貨", "攝影沖印", "診所",
]
TECH_INDUSTRY = ["電子", "半導體", "光電", "電腦", "軟體", "網路", "資訊",
                 "通訊", "精密", "自動化"]          # 行業含這些字樣 = 科技業(第二優先)
FOREIGN_COMPANY = (r"(美|日|德|法|英|荷|瑞士|瑞典|丹麥|芬蘭|韓|港|澳|義|西班牙|"
                   r"比利時|奧地利|加拿大|新加坡|香港)商|台灣分公司|外商")  # 公司名含這些 = 外商(最優先)
KEEP_DAYS = 30                    # 網頁只顯示最近 N 天內刊登/更新的職缺
STATE_PRUNE_DAYS = 120            # 超過 N 天沒再出現的職缺,從記錄中清除
SEND_WHEN_EMPTY = False           # 今日沒有新職缺時是否仍寄信
SITE_TITLE = "高雄・國外業務職缺雷達"
GD_LAT, GD_LON = 22.665621, 120.303256   # 出發點:捷運巨蛋站(R14)
ORIGIN_NAME = "捷運巨蛋站"
PEAK_WEEKDAY = 0                  # 通勤時間的出發日:0=週一,1=週二,...,6=週日
PEAK_H, PEAK_M = 7, 30            # 出發時刻:07:30(Google 模式以「下一個週一 07:30」計算)
GOOGLE_MAX_PER_RUN = 200          # Google API 每次執行最多補算的職缺數(每筆約 2 次呼叫)
LI_KEYWORDS = ["international sales", "export sales", "overseas sales",
               "國外業務", "外銷業務", "國際業務", "business development"]  # LinkedIn 搜尋關鍵字
LI_MAX_AGE_DAYS = 60              # LinkedIn 職缺只收 N 天內刊登的(它有萬年舊缺)
LI_TITLE_INCLUDE = r"(?i)sales|business development|account manager|業務|外銷|商務開發"  # LinkedIn 職稱必須符合
MOL_MAX_PER_RUN = 60              # 勞動部違規查詢:每次執行最多查幾家公司
MOL_REFRESH_DAYS = 30             # 每家公司的違規紀錄多久重查一次
MOL_RECENT_YEARS = 3              # 只統計近 N 年的處分
# ===============================================================

ROOT = Path(__file__).resolve().parent
DATA = ROOT / "data"
DOCS = ROOT / "docs"
TZ = ZoneInfo("Asia/Taipei")

UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
      "(KHTML, like Gecko) Chrome/126.0.0.0 Safari/537.36")
API = "https://www.104.com.tw/jobs/search/api/jobs"

LANG_MAP = {1: "英文", 2: "日文", 3: "德文", 4: "法文", 5: "西班牙文", 6: "韓文",
            7: "義大利文", 8: "葡萄牙文", 9: "俄文", 10: "阿拉伯文", 11: "泰文",
            12: "越南文", 13: "印尼文", 14: "馬來文"}
ABILITY_MAP = {8: "精通", 4: "中等", 2: "略懂", 1: "略懂"}
SALARY_UNIT = {30: "時薪", 40: "日薪", 50: "月薪", 60: "年薪"}


def _get_json(url: str, referer: str = "https://www.104.com.tw/jobs/search/") -> dict:
    req = Request(url, headers={"User-Agent": UA, "Referer": referer, "Accept": "application/json"})
    for attempt in range(4):  # 連續請求太快會被 403 限流,退避重試
        try:
            with urlopen(req, timeout=30) as r:
                return json.load(r)
        except Exception:
            if attempt == 3:
                raise
            time.sleep(5 * (attempt + 1))


def fetch_page(page: int, pagesize: int = 20) -> dict:
    params = {
        "jobsource": "index_s", "mode": "s", "order": "16",  # order=16: 依日期新→舊
        "jobcat": ",".join(JOBCATS), "area": ",".join(AREAS),
        "page": page, "pagesize": pagesize,  # pagesize 上限 20,超過會 400
    }
    if KEYWORD:
        params["keyword"] = KEYWORD
    return _get_json(API + "?" + urlencode(params))


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


def monthly_equiv(low, high, s10) -> int:
    """換算成「月薪當量」供排序/篩選:年薪÷12、日薪×22、時薪×174;面議依法≥4萬,當作 40,001。"""
    low, high = int(low or 0), int(high or 0)
    if (low == 0 and high == 0) or s10 == 10:
        return 40001
    base = low or high
    if s10 == 60:
        return round(base / 12)
    if s10 == 40:
        return base * 22
    if s10 == 30:
        return base * 174
    return base


def normalize(raw: dict) -> dict | None:
    name = raw.get("jobName", "")
    if EXCLUDE_TITLE and re.search(EXCLUDE_TITLE, name):
        return None
    industry = raw.get("coIndustryDesc", "") or ""
    if any(x in industry for x in EXCLUDE_INDUSTRIES):
        return None
    desc = raw.get("description") or ""
    s_text, s_class, s_low = salary_info(raw.get("salaryLow"), raw.get("salaryHigh"), raw.get("s10"))
    edu = raw.get("optionEdu") or []
    if edu and min(edu) > MAX_EDU:  # 學歷要求超過設定上限(預設大學)的職缺排除
        return None
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
    company = raw.get("custName", "")
    pri = (2 if re.search(FOREIGN_COMPANY, company)
           else 1 if any(x in industry for x in TECH_INDUSTRY) else 0)
    return {
        "no": str(raw.get("jobNo")),
        "name": name,
        "company": company,
        "pri": pri,
        "url": (raw.get("link") or {}).get("job", ""),
        "co_url": (raw.get("link") or {}).get("cust", ""),
        "area": raw.get("jobAddrNoDesc", ""),
        "industry": raw.get("coIndustryDesc", ""),
        "date": parse_date(raw.get("appearDate", "")),
        "apply": int(raw.get("applyCnt") or 0),
        "salary": s_text, "salary_class": s_class,
        "salary_m": monthly_equiv(raw.get("salaryLow"), raw.get("salaryHigh"), raw.get("s10")),
        "period": "經歷不拘" if period == 0 else f"{period} 年以上經歷",
        "employees": int(raw.get("employeeCount") or 0),
        "cust_no": str(raw.get("custNo") or ""),
        "co_jobs": None, "co_other": None, "co_plus": False,
        "vio_lb": None, "vio_osh": None, "vio_oth": None, "vio_latest": "", "vio_items": [],
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
    j["peak"] = False   # True = 開車時間為 Google 尖峰路況,False = OSRM 無路況估計
    j["approx"] = False  # True = 位置是用公司名定位的(LinkedIn 職缺),非精確地址
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
    以「下一個週一 07:30 出發」計算(PEAK_WEEKDAY/PEAK_H/PEAK_M 可調)。
    每次執行最多處理 GOOGLE_MAX_PER_RUN 筆職缺(每筆約 2 次呼叫);
    算過的存進 routes.json 快取,同一筆職缺不會重複呼叫。"""
    key = os.environ.get("GOOGLE_MAPS_API_KEY", "")
    if not key:
        return
    dep = now.replace(hour=PEAK_H, minute=PEAK_M, second=0, microsecond=0)
    while dep.weekday() != PEAK_WEEKDAY or dep <= now:
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


def fetch_company_counts(jobs: list[dict], now: datetime):
    """查每家公司目前總共在徵幾個職缺、其中幾個非業務類(gauge 公司規模與擴編狀況)。
    範圍是該公司「全台」職缺(看整體擴編狀況)。方法:用公司全名當關鍵字搜尋 104,
    只計 custNo 相符的結果;最多翻 2 頁(40 筆),更多顯示為 40+。
    業務職以職稱判斷(含業務/銷售/sales)。結果快取 7 天,每次執行最多查 120 家。"""
    p = DATA / "companies.json"
    cache = json.loads(p.read_text(encoding="utf-8")) if p.exists() else {}
    fresh = (now - timedelta(days=7)).strftime("%Y-%m-%d")
    today = now.strftime("%Y-%m-%d")
    firms = {}
    for j in jobs:
        if j["cust_no"]:
            firms.setdefault(j["cust_no"], j["company"])
    todo = [(no, name) for no, name in firms.items()
            if cache.get(no, {}).get("d", "") < fresh][:120]
    for no, name in todo:
        rows, last_page, page = [], 1, 1
        try:
            while page <= min(last_page, 2):
                d = _get_json(API + "?" + urlencode({
                    "jobsource": "index_s", "mode": "s", "keyword": name,
                    "page": page, "pagesize": 20}))
                last_page = d.get("metadata", {}).get("pagination", {}).get("lastPage", 1)
                rows += [r for r in d.get("data", []) if str(r.get("custNo")) == no]
                page += 1
                time.sleep(1.2)
        except Exception:
            continue  # 這家查失敗就跳過,下次執行再補
        # 用職稱判斷是否為業務職:公司常把專案經理/助理也掛業務「類別」,類別不可靠
        sales = sum(1 for r in rows if re.search(r"業務|銷售|[Ss]ales", r.get("jobName", "")))
        cache[no] = {"t": len(rows), "ns": len(rows) - sales, "plus": last_page > 2, "d": today}
    cache = {k: v for k, v in cache.items() if k in firms}
    p.write_text(json.dumps(cache, ensure_ascii=False, indent=1), encoding="utf-8")
    done = 0
    for j in jobs:
        c = cache.get(j["cust_no"])
        if c:
            j["co_jobs"], j["co_other"], j["co_plus"] = c["t"], c["ns"], c.get("plus", False)
            done += 1
    print(f"公司職缺數:本次查了 {len(todo)} 家,{done}/{len(jobs)} 筆職缺已有公司資料")


# ---------------------------- 勞動部違規紀錄 ----------------------------

def fetch_violations(jobs: list[dict], now: datetime):
    """查勞動部「違反勞動法令事業單位」公開名單(announcement.mol.gov.tw)。
    每家公司下載一份 ODS 清冊,統計近 N 年勞基法群/職安法/其他的處分筆數。
    政府開放資料,查詢合法;結果快取 MOL_REFRESH_DAYS 天。"""
    import http.cookiejar
    import zipfile

    p = DATA / "violations.json"
    cache = json.loads(p.read_text(encoding="utf-8")) if p.exists() else {}
    fresh = (now - timedelta(days=MOL_REFRESH_DAYS)).strftime("%Y-%m-%d")
    today = now.strftime("%Y-%m-%d")
    recent = (now - timedelta(days=365 * MOL_RECENT_YEARS))

    firms = sorted({j["company"] for j in jobs if j["company"]})
    todo = [n for n in firms if cache.get(n, {}).get("d", "") < fresh][:MOL_MAX_PER_RUN]
    if todo:
        # 政府憑證鏈缺 Subject Key Identifier,新版 Python 驗不過;公開唯讀資料,放寬驗證
        ctx = ssl.create_default_context()
        ctx.check_hostname = False
        ctx.verify_mode = ssl.CERT_NONE
        cj = http.cookiejar.CookieJar()
        op = build_opener(HTTPCookieProcessor(cj), HTTPSHandler(context=ctx))
        op.addheaders = [("User-Agent", UA)]
        try:
            home = op.open("https://announcement.mol.gov.tw/", timeout=60).read().decode("utf-8", "ignore")
            token = re.search(r'name="_csrf_token" value="([^"]+)"', home).group(1)
        except Exception as e:
            print(f"勞動部系統連線失敗,本次略過違規查詢: {e}")
            todo = []
        for name in todo:
            data = urlencode({
                "_csrf_token": token, "CITYNO": "", "UNITNAME": name,
                "DOCstartDate": "", "DOCEndDate": "", "REGNUMBER": "", "REGNO": "",
                "FINE": "", "downloadType": "1",
                "sortName1": "", "sortName2": "", "sortName3": "", "sortName4": "", "Page3": "1",
            }).encode()
            try:
                body = op.open(Request("https://announcement.mol.gov.tw/Download/", data=data),
                               timeout=120).read()
            except Exception:
                continue  # 失敗不記快取,下次再試
            if body[:2] != b"PK":
                cache[name] = {"lb": 0, "osh": 0, "oth": 0, "latest": "", "d": today}
                time.sleep(1.5)
                continue
            counts = {"lb": 0, "osh": 0, "oth": 0}
            latest = ""
            items = []
            try:
                xml = zipfile.ZipFile(io_bytes(body)).read("content.xml").decode("utf-8", "ignore")
                for tm in re.finditer(r'<table:table\s[^>]*table:name="([^"]+)"(.*?)</table:table>', xml, re.S):
                    tname, tbody = tm.group(1), tm.group(2)
                    kind = ("lb" if "勞基法" in tname else
                            "osh" if "職業安全" in tname else
                            "oth" if ("就服法" in tname or "勞退" in tname) else None)
                    if not kind:
                        continue
                    for row in re.findall(r"<table:table-row.*?</table:table-row>", tbody, re.S):
                        cells = [c.strip() for c in re.findall(r"<text:p>([^<]*)</text:p>", row)]
                        if not (cells and cells[0].isdigit()):
                            continue
                        dates = [datetime(int(y) + 1911, int(m), int(d), tzinfo=TZ)
                                 for y, m, d in re.findall(r"\b(\d{2,3})/(\d{2})/(\d{2})\b", row)
                                 if 1 <= int(m) <= 12 and 1 <= int(d) <= 31]
                        if not (dates and max(dates) >= recent):
                            continue
                        counts[kind] += 1
                        latest = max(latest, max(dates).strftime("%Y-%m-%d"))
                        # 細項:法條在含「法第」的儲存格,違規敘述通常是它的下一格
                        law = next((c for c in cells if "法第" in c), "")
                        idx = cells.index(law) if law else -1
                        txt = cells[idx + 1] if 0 <= idx < len(cells) - 1 else ""
                        items.append({"date": max(dates).strftime("%Y-%m-%d"),
                                      "law": law[:90], "txt": txt[:110]})
            except Exception:
                continue
            items.sort(key=lambda x: x["date"], reverse=True)
            cache[name] = {**counts, "latest": latest, "items": items[:12], "d": today}
            time.sleep(1.5)
    cache = {k: v for k, v in cache.items() if k in set(firms)}
    p.write_text(json.dumps(cache, ensure_ascii=False, indent=1), encoding="utf-8")
    done = 0
    for j in jobs:
        c = cache.get(j["company"])
        if c:
            j["vio_lb"], j["vio_osh"], j["vio_oth"], j["vio_latest"] = c["lb"], c["osh"], c["oth"], c["latest"]
            j["vio_items"] = c.get("items", [])
            done += 1
    print(f"勞動部違規紀錄:本次查了 {len(todo)} 家,{done}/{len(jobs)} 筆職缺已有資料")


def io_bytes(b: bytes):
    import io as _io
    return _io.BytesIO(b)


# ---------------------------- LinkedIn 外商職缺 ----------------------------

def fetch_linkedin(now: datetime) -> list[dict]:
    """用 LinkedIn 訪客搜尋端點抓高雄外商職缺(每組關鍵字 1 個請求,輕量)。
    結果與 data/linkedin.json 快取合併——哪天被 LinkedIn 擋了,舊資料還在。
    注意:此來源沒有薪資與詳細地址;薪資當量比照面議(40001)。"""
    p = DATA / "linkedin.json"
    cached = {j["no"]: j for j in json.loads(p.read_text(encoding="utf-8"))} if p.exists() else {}
    found = {}
    for kw in LI_KEYWORDS:
        url = ("https://www.linkedin.com/jobs-guest/jobs/api/seeMoreJobPostings/search?"
               + urlencode({"keywords": kw, "location": "Kaohsiung City, Taiwan", "start": 0}))
        try:
            req = Request(url, headers={"User-Agent": UA})
            with urlopen(req, timeout=30) as r:
                page = r.read().decode("utf-8", "ignore")
        except Exception as e:
            print(f"LinkedIn 查詢失敗({kw}),用快取頂替: {e}")
            continue
        for c in re.split(r"<li>", page)[1:]:
            title = re.search(r'base-search-card__title">\s*([^<]+?)\s*<', c)
            comp = re.search(r'hidden-nested-link"[^>]*>\s*([^<]+?)\s*<', c)
            loc = re.search(r'job-search-card__location">\s*([^<]+?)\s*<', c)
            dt = re.search(r'datetime="([^"]+)"', c)
            link = re.search(r'href="(https://[a-z]+\.linkedin\.com/jobs/view/[^"?]+)', c)
            jid = re.search(r"-(\d+)$", link.group(1)) if link else None
            if not (title and comp and loc and dt and jid):
                continue
            if "Kaohsiung" not in loc.group(1):
                continue
            import html as _h
            found["li" + jid.group(1)] = {
                "no": "li" + jid.group(1),
                "name": _h.unescape(title.group(1)),
                "company": _h.unescape(comp.group(1)),
                "url": link.group(1), "co_url": "",
                "area": "高雄市(LinkedIn)", "industry": "外商/LinkedIn", "pri": 2,
                "date": dt.group(1), "apply": None,
                "salary": "薪資未列(見原頁)", "salary_class": "negotiable", "salary_m": 40001,
                "period": "條件詳見原頁", "employees": 0,
                "cust_no": "", "co_jobs": None, "co_other": None, "co_plus": False,
                "vio_lb": None, "vio_osh": None, "vio_oth": None, "vio_latest": "", "vio_items": [],
                "langs": [], "trip": False, "remote": False, "desc": "",
                "lat": None, "lon": None, "source": "li",
            }
        time.sleep(2)
    merged = {**cached, **found}
    cutoff = (now - timedelta(days=LI_MAX_AGE_DAYS)).strftime("%Y-%m-%d")
    merged = {k: v for k, v in merged.items() if v["date"] >= cutoff
              and re.search(LI_TITLE_INCLUDE, v["name"])
              and not re.search(EXCLUDE_TITLE, v["name"])}
    p.write_text(json.dumps(list(merged.values()), ensure_ascii=False, indent=1), encoding="utf-8")
    print(f"LinkedIn 外商職缺:{len(merged)} 筆(本次抓到 {len(found)} 筆)")
    return list(merged.values())


def fetch_li_routes(li_jobs: list[dict], cache: dict, stations: list[dict], now: datetime):
    """LinkedIn 職缺沒有地址,先用 Places API 文字搜尋「公司名+Kaohsiung」定位辦公室,
    再用 Routes API 算通勤。查不到(或 Places API 未啟用)就不顯示距離——寧缺勿假。
    註:直接把公司名丟 Routes 的地址欄位會全部退回市中心座標,不可用。"""
    key = os.environ.get("GOOGLE_MAPS_API_KEY", "")
    dep = now.replace(hour=PEAK_H, minute=PEAK_M, second=0, microsecond=0)
    while dep.weekday() != PEAK_WEEKDAY or dep <= now:
        dep += timedelta(days=1)
    dep_utc = dep.astimezone(ZoneInfo("UTC")).strftime("%Y-%m-%dT%H:%M:%SZ")

    def places_lookup(company):
        req = Request("https://places.googleapis.com/v1/places:searchText",
                      data=json.dumps({"textQuery": f"{company} Kaohsiung Taiwan"}).encode(),
                      headers={"Content-Type": "application/json", "X-Goog-Api-Key": key,
                               "X-Goog-FieldMask": "places.location"})
        try:
            with urlopen(req, timeout=30) as r:
                d = json.load(r)
            loc = (d.get("places") or [{}])[0].get("location") or {}
            return loc.get("latitude"), loc.get("longitude")
        except HTTPError as e:
            if e.code == 403:
                return "blocked", None  # Places API 未啟用:不要記成定位失敗,啟用後會自動補
            return None, None
        except Exception:
            return None, None

    def route(lat, lon, mode):
        body = {
            "origin": {"location": {"latLng": {"latitude": GD_LAT, "longitude": GD_LON}}},
            "destination": {"location": {"latLng": {"latitude": lat, "longitude": lon}}},
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

    places_blocked = False
    places_ok = 0
    for j in li_jobs:
        c = cache.get(j["no"])
        if c is None and key and not places_blocked:
            plat, plon = places_lookup(j["company"])
            if plat == "blocked" and places_ok:  # 已有成功紀錄 → 只是暫時性 403,重試一次
                time.sleep(3)
                plat, plon = places_lookup(j["company"])
            if plat == "blocked":
                if not places_ok:
                    places_blocked = True
                    print("Places API 未啟用,LinkedIn 職缺暫無距離資訊(啟用後會自動補算)")
                continue
            places_ok += 1
            if plat and haversine_km(GD_LAT, GD_LON, plat, plon) <= 80:
                c = {"lat": plat, "lon": plon}
                r = route(plat, plon, "DRIVE")
                if r:
                    c["g_km"], c["g_min"] = r
                rt = route(plat, plon, "TRANSIT")
                if rt:
                    c["transit_min"] = rt[1]
            else:
                c = {"bad": True}  # 定位失敗,記下來避免每天重查
            cache[j["no"]] = c
            time.sleep(0.3)
        if not c or c.get("bad"):
            continue
        j["lat"], j["lon"] = c["lat"], c["lon"]
        enrich_location(j, stations)  # 補最近捷運站與地圖連結
        if c.get("g_min"):
            j["drive_km"], j["drive_min"], j["peak"], j["approx"] = c["g_km"], c["g_min"], True, True
        if c.get("transit_min"):
            j["transit_min"] = c["transit_min"]


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

    li_jobs = fetch_linkedin(now)

    # 標記新職缺。除了職缺編號,還用「職缺指紋」(公司+正規化職稱)抓重新刊登:
    # 萬年缺常換編號重貼、職稱改一兩個字,指紋相同或相似度>=0.88 就視為同一缺。
    state = load_state()
    if "jobs" not in state:  # 舊格式遷移
        state = {"jobs": state, "fps": {}}
    sjobs, fps = state["jobs"], state["fps"]

    def norm_title(s):
        return re.sub(r"[\s\W_]+", "", s.lower())

    for j in jobs + li_jobs:
        cust = j["cust_no"] or j["company"]
        nt = norm_title(j["name"])
        key = hashlib.md5(f"{cust}|{nt}".encode()).hexdigest()[:12]
        fp = fps.get(key)
        if fp is None:  # 沒有完全相同的,模糊比對同公司的舊職稱(容忍改幾個字)
            for k, v in fps.items():
                if v["c"] == cust and difflib.SequenceMatcher(None, nt, v["t"]).ratio() >= 0.88:
                    key, fp = k, v
                    break
        known = j["no"] in sjobs
        if known:
            sjobs[j["no"]]["last_seen"] = today
        else:
            sjobs[j["no"]] = {"first_seen": today, "last_seen": today}
        j["first_seen"] = sjobs[j["no"]]["first_seen"]
        if fp is None:
            fp = {"c": cust, "t": nt, "first": today, "nos": [], "n": 0}
            fps[key] = fp
        if j["no"] not in fp["nos"]:
            fp["nos"] = (fp["nos"] + [j["no"]])[-10:]
            fp["n"] += 1
        fp["last"] = today
        # 同一缺跨日換編號才算重複刊登(同天刊兩筆同職稱可能是真的開兩個名額)
        j["repost"] = fp["n"] if fp["n"] >= 2 and fp["first"] < today else 0
        # NEW = 編號沒看過,且不是舊指紋換編號重貼
        j["is_new"] = (not known) and not (fp["n"] >= 2 and fp["first"] < today)

    # 清掉太久沒出現的記錄,避免檔案無限成長
    prune = (now - timedelta(days=STATE_PRUNE_DAYS)).strftime("%Y-%m-%d")
    state["jobs"] = {k: v for k, v in sjobs.items() if v["last_seen"] >= prune}
    state["fps"] = {k: v for k, v in fps.items() if v.get("last", today) >= prune}

    display = [j for j in jobs if j["date"] >= cutoff] + li_jobs

    # 距離與交通:直線距離、最近捷運站、開車路線(OSRM)、大眾運輸(選用)
    stations = load_stations()
    routes_p = DATA / "routes.json"
    routes = json.loads(routes_p.read_text(encoding="utf-8")) if routes_p.exists() else {}
    for j in display:
        enrich_location(j, stations)
    fetch_drive_routes(display, routes)
    fetch_google_routes(display, routes, now)
    fetch_li_routes(li_jobs, routes, stations, now)
    fetch_company_counts(display, now)
    fetch_violations(display, now)
    routes = {k: v for k, v in routes.items() if k in state["jobs"]}  # 跟著 state 一起清舊資料

    # 外商(2)>科技業(1)>其他(0),各層內離巨蛋站近的優先(沒有座標的排最後)
    def sort_key(j):
        return (-j.get("pri", 0),
                j["drive_km"] if j["drive_km"] is not None
                else j["dist_km"] if j["dist_km"] is not None else 9999)
    display.sort(key=sort_key)
    new_jobs = sorted([j for j in display if j["is_new"]], key=sort_key)

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
    if j.get("vio_lb") or j.get("vio_osh") or j.get("vio_oth"):
        badges.append(f'<span style="color:#dc2626">⚠️ 近3年違規:勞基法 {j["vio_lb"]}・職安 {j["vio_osh"]}・其他 {j["vio_oth"]} 件</span>')
    meta = f'{j["area"]}|{j["period"]}' + ("|" + "|".join(badges) if badges else "")
    color = "#d97706" if j["salary_class"] == "negotiable" else "#059669"
    parts = []
    if j.get("drive_km") is not None:
        peak = "尖峰約" if j.get("peak") else "約"
        approx = "(依公司名定位)" if j.get("approx") else ""
        parts.append(f"🚗 開車 {j['drive_km']} km・{peak} {j['drive_min']} 分{approx}")
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
      {'<span style="background:#e7f0fe;color:#0a66c2;font-size:11px;font-weight:700;border-radius:999px;padding:2px 8px;margin-left:6px">🌐 LinkedIn 外商</span>' if j.get('source') == 'li' else ''}
      <div style="color:#374151;margin-top:2px">{j['company']}<span style="color:#9ca3af">({j['industry']})</span></div>
      <div style="margin-top:4px;font-weight:700;color:{color}">{j['salary']}</div>
      {traffic}
      <div style="color:#6b7280;font-size:13px;margin-top:4px">{meta}</div>
      <div style="color:#6b7280;font-size:13px;margin-top:2px">刊登 {j['date']}{'' if j['apply'] is None else f"|{j['apply']} 人應徵"}</div>
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
