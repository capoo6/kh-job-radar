# 高雄・國外業務職缺雷達

每天早上 07:30 自動從 104 抓「高雄市 × 國外業務」職缺，更新成一個網頁，並把**當天新出現的職缺**寄到指定信箱。

- 網頁:職缺卡片列表，含薪資、刊登日期、地區、經歷/學歷要求、語言要求、應徵人數、出差/外派標記，可搜尋、篩選、排序
- 距離:以**捷運巨蛋站**為出發點，顯示實際開車距離與時間（OSRM 路網計算）、最近捷運/輕軌站與步行距離、「紅線直達」標記，並附 Google Maps 開車/大眾運輸路線連結;預設離巨蛋站近的排前面
- 通知信:只寄「今天第一次出現」的職缺，依距離近→遠排序，不會重複轟炸
- 全部跑在 GitHub 免費服務上，電腦不用開機

## 一次性設定步驟

### 1. 建立 GitHub 儲存庫並上傳

1. 到 [github.com](https://github.com) 註冊/登入，按右上角 **+ → New repository**
2. 名稱取 `kh-job-radar`，選 **Public**（GitHub Pages 免費版需要公開），按 **Create repository**
3. 在這個資料夾開終端機執行（把 `你的帳號` 換掉）:

```
git remote add origin https://github.com/你的帳號/kh-job-radar.git
git push -u origin main
```

### 2. 設定 Gmail 應用程式密碼（寄信用）

1. 用要當「寄件者」的 Google 帳號開啟 [myaccount.google.com/apppasswords](https://myaccount.google.com/apppasswords)
   （需先開啟兩步驟驗證）
2. 建立一組應用程式密碼，複製那 16 個字元

### 3. 在 GitHub 填入秘密設定

儲存庫頁面 → **Settings → Secrets and variables → Actions**:

| 類型 | 名稱 | 內容 |
|---|---|---|
| Secret | `GMAIL_USER` | 寄件者 Gmail 地址 |
| Secret | `GMAIL_APP_PASSWORD` | 剛剛的 16 字元應用程式密碼 |
| Secret | `EMAIL_TO` | 收件者信箱，多個用逗號分隔 |
| Secret | `GOOGLE_MAPS_API_KEY` | （選填）填了才會顯示大眾運輸「幾分鐘」，見下方說明 |
| Variable | `SITE_URL` | 網站網址（第 4 步取得後回來填，信裡會附連結） |

### 4. 開啟 GitHub Pages

儲存庫 → **Settings → Pages** → Source 選 **Deploy from a branch** → Branch 選 `main`、資料夾選 `/docs` → Save。
一兩分鐘後網址會是 `https://你的帳號.github.io/kh-job-radar/`，把它填回上面的 `SITE_URL`。

### 5. 測試

儲存庫 → **Actions** → 左邊選「每日職缺更新」→ **Run workflow**。
跑完後網頁會更新;第一次執行只建立基準資料不寄信，第二次開始有新職缺才會寄。

## 調整條件

打開 `main.py` 最上面的「設定區」:

- `JOBCATS`:職務類別（預設國外業務，可加國外業務主管 `2005003002`）
- `AREAS`:地區（預設高雄市，可加台南市 `6001014000` 等）
- `KEYWORD`:額外關鍵字過濾
- `KEEP_DAYS`:網頁顯示最近幾天的職缺
- `GD_LAT / GD_LON / ORIGIN_NAME`:距離計算的出發點（預設捷運巨蛋站），搬家改這裡
- 排程時間改 `.github/workflows/daily.yml` 裡的 cron（UTC 時間 = 台灣時間 −8 小時）

## Google 金鑰（選用）:尖峰開車時間 + 大眾運輸分鐘數

預設的開車時間由 OSRM 計算，**不含路況**（等於路不塞時的車程），尖峰時段會偏樂觀。
大眾運輸則預設提供:最近捷運站+步行距離、「紅線直達」標記、每筆職缺的 Google Maps 路線連結。

填入 Google 金鑰後自動升級兩件事（以「下一個平日 08:00 出發」計算，時間可改 `main.py` 的 `PEAK_H/PEAK_M`）:

1. 開車時間改用 **Google 歷史路況（TRAFFIC_AWARE）**，顯示為「尖峰約 X 分」
2. 網頁與信件直接顯示**大眾運輸分鐘數**

申請方式:到 [console.cloud.google.com](https://console.cloud.google.com) 建專案 → 啟用 **Routes API** → 建 API key
（需綁信用卡，但同一筆職缺算過就存快取不重算，日常每天只查新職缺、幾十次呼叫，
遠低於每月免費額度;擔心的話可在後台把配額上限設成每日 1000 次保險）
→ 把 key 填進 Secret `GOOGLE_MAPS_API_KEY`。
金鑰剛加入時,每次執行最多補算 200 筆既有職缺,跑兩次即可全部補完。

改完 commit + push 即可生效。

## 本機測試

```
python main.py
```

會產生 `docs/index.html`，直接用瀏覽器打開看。不設環境變數就不會寄信。
