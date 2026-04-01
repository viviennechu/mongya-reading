"""
LINE 聊天記錄解析器
支援從 LINE 群組匯出的 .txt 格式中，提取共讀訊息並計算去重 hash。
"""
import re
import hashlib
from datetime import datetime, timezone, timedelta

TW = timezone(timedelta(hours=8))

# ── 共讀格式正則（設計文件：聊天記錄解析策略）──────────────────────
# 每則訊息的第一行依序嘗試，任一命中即解析
_READING_PATTERNS = [
    # 7月共讀分享：書名  /  7月共讀分享:書名
    re.compile(r"^(1[0-2]|[1-9])月共讀分享[：:]\s*(.*)$"),
    # 7月共讀：書名
    re.compile(r"^(1[0-2]|[1-9])月共讀[：:]\s*(.*)$"),
    # 【2月共讀】書名  /  [2月共讀] 書名
    re.compile(r"^[【\[](1[0-2]|[1-9])月共讀[】\]]\s*(.*)$"),
]

# LINE 訊息行格式：  上午/下午HH:MM \t 發送者 \t 內容
_MSG_LINE = re.compile(r"^(上午|下午)\d{1,2}:\d{2}\t")

# 日期標題行格式：2023/11/01（三）
_DATE_LINE = re.compile(r"^(\d{4})/(\d{2})/(\d{2})[（(]")

# M 編號提取：M096_... 或 M96_...
_MEMBER_NUM = re.compile(r"^M0*(\d+)_", re.IGNORECASE)


def _parse_date_line(line: str) -> datetime | None:
    m = _DATE_LINE.match(line)
    if not m:
        return None
    try:
        return datetime(int(m.group(1)), int(m.group(2)), int(m.group(3)),
                        tzinfo=TW)
    except ValueError:
        return None


def _extract_sender_and_content(line: str) -> tuple[str, str] | None:
    """從 '時間\t發送者\t內容' 格式中提取 (sender, first_line_content)。"""
    parts = line.split("\t", 2)
    if len(parts) < 3:
        return None
    sender = parts[1].strip()
    content = parts[2].strip()
    # 去除引號包覆（多行訊息以 " 開頭）
    if content.startswith('"'):
        content = content[1:]
    return sender, content


def _extract_member_number(sender_name: str) -> int | None:
    m = _MEMBER_NUM.match(sender_name)
    return int(m.group(1)) if m else None


def _match_reading(first_line: str) -> tuple[str, str] | None:
    """若第一行符合任一共讀格式，回傳 (month, book_title)；否則 None。"""
    for pattern in _READING_PATTERNS:
        m = pattern.match(first_line.strip())
        if m:
            return m.group(1), m.group(2).strip()
    return None


# ── 主解析函式 ─────────────────────────────────────────────────

def parse_chat_export(text: str) -> list[dict]:
    """
    Parse LINE chat export .txt content.

    Returns list of dicts with keys:
        month, book_title, content, sender_name, member_number, sent_at
    """
    lines = text.splitlines()
    records: list[dict] = []

    current_date: datetime | None = None
    current_sender: str = ""
    current_lines: list[str] = []
    in_message: bool = False

    def flush():
        nonlocal current_sender, current_lines, in_message
        if not in_message or not current_lines:
            current_sender = ""
            current_lines = []
            in_message = False
            return

        first = current_lines[0]
        result = _match_reading(first)
        if result:
            month, book_title = result
            content = "\n".join(current_lines[1:]).strip()
            # 去除末尾引號（LINE 多行訊息結尾會有 "）
            if content.endswith('"'):
                content = content[:-1].strip()
            records.append({
                "month": month,
                "book_title": book_title,
                "content": content,
                "sender_name": current_sender,
                "member_number": _extract_member_number(current_sender),
                "sent_at": current_date,
            })

        current_sender = ""
        current_lines = []
        in_message = False

    for line in lines:
        # 日期標題
        date = _parse_date_line(line)
        if date:
            flush()
            current_date = date
            continue

        # 新訊息行
        if _MSG_LINE.match(line):
            flush()
            parsed = _extract_sender_and_content(line)
            if parsed:
                current_sender, first_content = parsed
                current_lines = [first_content]
                in_message = True
            continue

        # 系統訊息（加入、退出等，無 sender）或空行
        if "\t\t" in line or not line.strip():
            flush()
            continue

        # 訊息延續行
        if in_message:
            stripped = line.strip()
            # 去除末尾引號
            if stripped.endswith('"') and len(current_lines) > 0:
                stripped = stripped[:-1]
            current_lines.append(stripped)

    flush()
    return records


# ── 去重 hash ──────────────────────────────────────────────────

def compute_content_hash(record: dict) -> str:
    """
    SHA-256 hash of (member_number or sender_name) + month + book_title + content[:100].
    確保相同內容不會重複匯入（去重機制）。
    """
    identifier = (
        str(record.get("member_number"))
        if record.get("member_number") is not None
        else record.get("sender_name", "")
    )
    raw = (
        identifier
        + "|" + str(record.get("month", ""))
        + "|" + str(record.get("book_title", ""))
        + "|" + str(record.get("content", ""))[:100]
    )
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()
