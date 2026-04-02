# 蒙芽共讀資料庫

LINE 群組「孩子在蒙芽」的共讀記錄與集點系統。  
管理員每月匯入 LINE 聊天記錄 → 自動解析共讀訊息 → 發點數 → 成員可查詢點數與兌換獎品。

**線上網址**：`https://web-production-47e98.up.railway.app`

---

## 現有功能

### 共讀資料庫（首頁 `/`）
- 關鍵字搜尋（書名、主題、作者）
- 按月份篩選（1–12月）
- 分頁載入

### 我的點數（`/member`）
- 帳號密碼登入（以 M 編號申請帳號）
- 登入後可補填 / 修改 Gmail（用來接收雲端教材）
- 查看點數餘額
- 查看點數明細紀錄（最近 50 筆）
- 查看兌換紀錄（含發放狀態、兌換內容連結）
- 自助兌換獎品（點數足夠才能按）
  - 兌換成功後顯示 Drive 連結
  - 兌換紀錄永久保存連結（防止遺失）

### 管理後台（`/admin`）
- 密碼登入保護（`ADMIN_PASSWORD` 環境變數）

**匯入 tab**
- **同步名單**：從 Google Sheets 讀取最新成員名單（M編號 + 顯示名稱），自動新增或更新，不刪除現有成員
- **匯入 LINE 聊天記錄**：上傳 `.txt` → 自動解析共讀訊息 → 每月每人最多 +1 點
  - 重複匯入自動去重，不重複計點
  - 識別格式：`【X月共讀】`、`[X月共讀]`、`X月共讀：`、`X月共讀分享：`
  - 成員識別：顯示名稱中的 M 編號（如 M097_小羽睿睿媽咪）

**成員點數 tab**
- **批次加點（指定名單）**：輸入多個 M 編號（數字，逗號或換行分隔）+ 點數 + 備註
- **全體加點**：一次給所有成員加點（有確認視窗）
- **單人調整**：搜尋成員 → 按「調整」→ 輸入正負數點數

**獎品 tab**
- 新增 / 編輯獎品（名稱、所需點數、庫存、Drive 兌換連結）

**兌換紀錄 tab**
- 查看所有兌換，標記已發放

---

## 技術架構

| 項目 | 技術 |
|------|------|
| 後端 | Python Flask + SQLAlchemy |
| 資料庫 | PostgreSQL（Railway 托管） |
| 前端 | Jinja2 模板 + 原生 Fetch API |
| 部署 | Railway（GitHub 自動部署） |
| 外部 API | Google Sheets API v4、Google Drive API v3 |

---

## 環境變數

| 變數 | 說明 |
|------|------|
| `DATABASE_URL` | PostgreSQL 連線字串（Railway 自動注入） |
| `FLASK_SECRET_KEY` | Flask Session 加密金鑰 |
| `ADMIN_PASSWORD` | 管理後台登入密碼 |
| `GOOGLE_SERVICE_ACCOUNT_JSON` | Google Service Account JSON（單行，貼入 Railway Variables） |

---

## 每月匯入流程

1. 在 LINE 群組點右上角 → 匯出聊天記錄 → 存成 `.txt`
2. 進入 `/admin` → 輸入密碼登入
3. 選「匯入」tab → 上傳 `.txt` 檔 → 點「開始匯入」
4. 系統會顯示：解析幾則、新增幾筆、跳過重複幾筆

> 每月每位成員最多只會得 1 點，重複匯入不會重複計點。

---

## 成員名單同步（Google Sheets）

- Sheet ID：`1a3OiKsBxSJt-YXkvNcnoTfP39ySL1aCG8Wk6Xh3dSGY`
- 工作表1，欄A = LINE名稱，欄B = M編號（自動編號）
- Service Account 已加入 Sheet 共用（檢視者）：`id-google@monya-492013.iam.gserviceaccount.com`
- 新增成員後，後台按「同步名單」即可更新資料庫

---

## 獎品資料庫（康軒主題延伸）

已批次匯入 89 筆康軒延伸教材（初階版 + 學前版），預設 10 點兌換。  
每筆獎品包含：
- Drive 資料夾連結（兌換後直接取得）
- PDF 描述（從資料夾內的 PDF 檔名自動擷取）

**更新 PDF 描述的方式**（資料夾有新增 PDF 時）：
```bash
DATABASE_PUBLIC_URL="postgresql://..." python3 update_reward_descriptions.py
```
腳本會對每個有 Drive 連結的獎品爬取資料夾內 PDF 檔名，自動更新 `description` 欄位。  
Service account 金鑰（本機用）：`~/Downloads/monya-492013-4490bf83f33c.json`

---

## 成員帳號申請流程

1. 管理員先「同步名單」讓 M 編號進資料庫
2. 成員到 `/member/register` 輸入 M 編號 + 設定密碼（+ 選填 Gmail）
3. 之後用 `/member/login` 登入
4. 登入後可在會員頁隨時更新 Gmail

---

## 待辦 / 未來規劃

- [ ] 遊台灣系列獎品改為 15 點（目前預設 10 點）
- [ ] 兌換時自動將 Drive 資料夾共享給會員 Gmail
- [ ] 後台可直接搜尋 / 刪除特定共讀紀錄
- [ ] 匯入時顯示詳細的成員點數異動摘要
