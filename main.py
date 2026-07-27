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
EXCLUDE_TITLE = r"國內(?!外)"     # 職稱含「國內」(但不是「國內外」)的排除
KEEP_DAYS = 30                    # 網頁只顯示最近 N 天內刊登/更新的職缺
STATE_PRUNE_DAYS = 120            # 超過 N 天沒再出現的職缺,從記錄中清除
SEND_WHEN_EMPTY = False           # 今日沒有新職缺時是否仍寄信
SITE_TITLE = "高雄・國外業務職缺雷達"
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
    }


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
    display.sort(key=lambda j: (j["date"], j["is_new"], j["salary_low"]), reverse=True)
    new_jobs = [j for j in display if j["is_new"]]

    first_run = not (DATA / "state.json").exists()
    DATA.mkdir(exist_ok=True)
    (DATA / "state.json").write_text(json.dumps(state, ensure_ascii=False, indent=1), encoding="utf-8")
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
    return f"""
    <tr><td style="padding:12px 16px;border-bottom:1px solid #e5e7eb">
      <a href="{j['url']}" style="font-size:16px;font-weight:700;color:#1d4ed8;text-decoration:none">{j['name']}</a>
      <div style="color:#374151;margin-top:2px">{j['company']}<span style="color:#9ca3af">({j['industry']})</span></div>
      <div style="margin-top:4px;font-weight:700;color:{color}">{j['salary']}</div>
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
      <p style="color:#6b7280;margin:0 0 16px">{now.strftime('%Y-%m-%d')}|今日新增 {len(new_jobs)} 筆|追蹤中共 {total} 筆</p>
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
