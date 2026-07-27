# 高雄・國外業務職缺雷達

每天早上 07:30 自動從 104 抓「高雄市 × 國外業務」職缺，更新成一個網頁，並把**當天新出現的職缺**寄到指定信箱。

- 網頁:職缺卡片列表，含薪資、刊登日期、地區、經歷/學歷要求、語言要求、應徵人數、出差/外派標記，可搜尋、篩選、排序
- 通知信:只寄「今天第一次出現」的職缺，不會重複轟炸
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
- 排程時間改 `.github/workflows/daily.yml` 裡的 cron（UTC 時間 = 台灣時間 −8 小時）

改完 commit + push 即可生效。

## 本機測試

```
python main.py
```

會產生 `docs/index.html`，直接用瀏覽器打開看。不設環境變數就不會寄信。
