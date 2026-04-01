# LINE 共讀小幫手

偵測社群中「X月共讀」格式訊息，自動記錄到 Google Sheets，並提供關鍵字搜尋網頁。

---

## 部署前準備

### 1. LINE Developers Console

1. 前往 https://developers.line.biz → 建立 Provider → 建立 Messaging API Channel
2. 在 Channel settings 頁面取得：
   - **Channel Secret**
   - **Channel Access Token**（長期）
3. 在 Messaging API 設定中關閉「Auto-reply messages」

### 2. Google Sheets + Service Account

1. 在 Google Cloud Console 建立專案 → 啟用 **Google Sheets API**
2. 建立 **Service Account** → 下載 JSON 金鑰檔案
3. 建立一個新的 Google 試算表 → 把 Service Account 的 email 加為「編輯者」
4. 試算表 ID 就是網址中 `/d/` 和 `/edit` 之間那段

### 3. 部署到 Railway

1. 把這個資料夾推到 GitHub repo
2. 在 Railway 建立新專案 → 連結 GitHub repo
3. 在 Railway 的 Variables 頁面設定以下環境變數：

```
LINE_CHANNEL_SECRET=...
LINE_CHANNEL_ACCESS_TOKEN=...
GOOGLE_SHEETS_ID=...
GOOGLE_SERVICE_ACCOUNT_JSON={"type":"service_account",...}  ← 整個 JSON 貼成一行
```

4. 部署完成後取得 Railway 給的網址（例如 `https://xxx.railway.app`）

### 4. 設定 LINE Webhook

1. 回到 LINE Developers Console
2. Webhook URL 填入：`https://xxx.railway.app/callback`
3. 開啟「Use webhook」
4. 點「Verify」確認連線成功

### 5. 把機器人加入 LINE 社群

- 在機器人的 Messaging API 設定中，確認 **Group chat** 已開啟
- 用 QR Code 或連結邀請機器人進入社群

---

## 使用方式

成員在群組貼出含有以下格式的訊息，機器人就會自動記錄：

```
【2月共讀】
內文...
```

或

```
[3月共讀] 書名
內文...
```

機器人會回覆「✅ 已記錄！」確認。

**搜尋網頁**：`https://xxx.railway.app/`

---

## Google Sheets 欄位說明

| 欄位 | 說明 |
|------|------|
| 時間戳記 | 台灣時間 |
| 月份 | 1–12 |
| 書名 | 標籤後第一行（可空白） |
| 內文 | 完整訊息本文 |
| 發送者名稱 | LINE 顯示名稱 |
| 發送者ID | LINE user ID |
| 訊息ID | 用於去重，避免重複記錄 |
