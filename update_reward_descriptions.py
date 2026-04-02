"""
一次性腳本：爬取每個康軒獎品資料夾內的 PDF 檔名，
擷取有意義的描述後更新資料庫 description 欄位。

使用方式：
  DATABASE_PUBLIC_URL=... python update_reward_descriptions.py
  (或在 .env 設好 DATABASE_PUBLIC_URL)
"""

import os
import re
import sys

import psycopg2
from google.oauth2 import service_account
from googleapiclient.discovery import build

# ── 設定 ──────────────────────────────────────────────────────────
SERVICE_ACCOUNT_FILE = os.path.expanduser("~/Downloads/monya-492013-4490bf83f33c.json")
SCOPES = ["https://www.googleapis.com/auth/drive.readonly"]

DATABASE_URL = (
    os.environ.get("DATABASE_PUBLIC_URL")
    or os.environ.get("DATABASE_URL")
    or ""
)
if DATABASE_URL.startswith("postgres://"):
    DATABASE_URL = DATABASE_URL.replace("postgres://", "postgresql://", 1)


def folder_id_from_url(url: str) -> str | None:
    """從 Drive 資料夾 URL 擷取 folder ID。"""
    m = re.search(r"/folders/([a-zA-Z0-9_-]+)", url)
    return m.group(1) if m else None


def extract_description(filename: str) -> str:
    """
    從 PDF 檔名擷取有意義的描述。
    例：蒙芽七月份主題延伸_交通小書.pdf → 交通小書
        蒙芽主題延伸康軒151_交通小書_v2.pdf → 交通小書_v2
    策略：去掉副檔名後，取最後一個底線後的內容；
          若無底線，取整個去副檔名的字串。
    """
    name = os.path.splitext(filename)[0]   # 去掉 .pdf
    parts = name.split("_")
    if len(parts) >= 2:
        # 丟棄第一個片段（通常是「蒙芽XXX主題延伸」），合併剩餘部分
        return "_".join(parts[1:])
    return name


def list_pdfs_in_folder(service, folder_id: str) -> list[dict]:
    """列出資料夾（含共用雲端硬碟）中的所有 PDF 檔案。"""
    results = []
    page_token = None
    while True:
        resp = service.files().list(
            q=f"'{folder_id}' in parents and mimeType='application/pdf' and trashed=false",
            fields="nextPageToken, files(id, name)",
            supportsAllDrives=True,
            includeItemsFromAllDrives=True,
            pageToken=page_token,
        ).execute()
        results.extend(resp.get("files", []))
        page_token = resp.get("nextPageToken")
        if not page_token:
            break
    return results


def main():
    if not DATABASE_URL:
        print("錯誤：請設定 DATABASE_PUBLIC_URL 環境變數。")
        sys.exit(1)

    # 初始化 Drive 服務
    creds = service_account.Credentials.from_service_account_file(
        SERVICE_ACCOUNT_FILE, scopes=SCOPES
    )
    service = build("drive", "v3", credentials=creds)

    # 連線資料庫
    conn = psycopg2.connect(DATABASE_URL)
    cur = conn.cursor()

    # 抓取有 redemption_url 的康軒獎品
    cur.execute("""
        SELECT id, name, description, redemption_url
        FROM rewards
        WHERE redemption_url IS NOT NULL AND redemption_url != ''
        ORDER BY id
    """)
    rows = cur.fetchall()
    print(f"找到 {len(rows)} 筆有連結的獎品")

    updated = 0
    failed = 0

    for reward_id, name, current_desc, url in rows:
        folder_id = folder_id_from_url(url)
        if not folder_id:
            print(f"  [跳過] {name} — 無法解析 folder ID from {url}")
            failed += 1
            continue

        try:
            files = list_pdfs_in_folder(service, folder_id)
        except Exception as e:
            print(f"  [錯誤] {name} (id={reward_id}) — Drive API: {e}")
            failed += 1
            continue

        if not files:
            print(f"  [空] {name} — 資料夾內無 PDF")
            continue

        # 合併所有 PDF 描述（通常只有一個）
        descriptions = [extract_description(f["name"]) for f in files]
        new_desc = "、".join(descriptions)

        print(f"  [更新] {name} → {new_desc}")
        cur.execute(
            "UPDATE rewards SET description = %s WHERE id = %s",
            (new_desc, reward_id)
        )
        updated += 1

    conn.commit()
    cur.close()
    conn.close()

    print(f"\n完成：更新 {updated} 筆，失敗 {failed} 筆")


if __name__ == "__main__":
    main()
