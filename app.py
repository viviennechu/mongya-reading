import os
import json
from functools import wraps
from werkzeug.security import generate_password_hash, check_password_hash
from google.oauth2 import service_account
from googleapiclient.discovery import build

from flask import (
    Flask, request, abort, redirect, url_for,
    session, jsonify, render_template,
)

from models import db, Member, Reading, PointTransaction, Reward, Redemption
from parser import parse_chat_export, compute_content_hash

# ── Flask 初始化 ──────────────────────────────────────────────
app = Flask(__name__)
app.secret_key = os.environ["FLASK_SECRET_KEY"]

database_url = os.environ["DATABASE_URL"]
if database_url.startswith("postgres://"):
    database_url = database_url.replace("postgres://", "postgresql://", 1)
app.config["SQLALCHEMY_DATABASE_URI"] = database_url
app.config["SQLALCHEMY_TRACK_MODIFICATIONS"] = False

db.init_app(app)


@app.cli.command("init-db")
def init_db():
    """建立資料表（部署後首次執行）。"""
    with app.app_context():
        db.create_all()
        print("資料表建立完成。")


with app.app_context():
    db.create_all()


# ── 管理員驗證（admin authentication / 管理員驗證）────────────────
ADMIN_PASSWORD = os.environ.get("ADMIN_PASSWORD", "")
SHEETS_ID = "1a3OiKsBxSJt-YXkvNcnoTfP39ySL1aCG8Wk6Xh3dSGY"

def get_drive_service():
    sa_json = os.environ.get("GOOGLE_SERVICE_ACCOUNT_JSON")
    scopes = [
        "https://www.googleapis.com/auth/drive.readonly",
        "https://www.googleapis.com/auth/spreadsheets.readonly",
    ]
    if sa_json:
        info = json.loads(sa_json)
        creds = service_account.Credentials.from_service_account_info(info, scopes=scopes)
    else:
        creds = service_account.Credentials.from_service_account_file(
            os.path.expanduser("~/Downloads/monya-492013-4490bf83f33c.json"), scopes=scopes
        )
    return creds


def admin_required(f):
    @wraps(f)
    def decorated(*args, **kwargs):
        if not session.get("admin_authenticated"):
            if request.path.startswith("/api/"):
                return jsonify({"error": "未登入"}), 401
            return redirect(url_for("admin_login"))
        return f(*args, **kwargs)
    return decorated


@app.route("/admin/login", methods=["GET"])
def admin_login():
    error = request.args.get("error")
    return render_template("admin_login.html", error=error)


@app.route("/admin/login", methods=["POST"])
def admin_login_post():
    pwd = request.form.get("password", "")
    if pwd == ADMIN_PASSWORD and ADMIN_PASSWORD:
        session["admin_authenticated"] = True
        return redirect(url_for("admin_panel"))
    return redirect(url_for("admin_login") + "?error=1")


@app.route("/admin/logout")
def admin_logout():
    session.clear()
    return redirect(url_for("admin_login"))


# ── 管理員頁面 ────────────────────────────────────────────────
@app.route("/admin")
@admin_required
def admin_panel():
    return render_template("admin.html")


# ── 聊天記錄匯入（import_records / auto-award point on import）───
def import_records(records: list) -> dict:
    """
    逐筆去重後插入，並對每筆新記錄給 member +1 點。
    回傳 import result summary。
    """
    inserted = 0
    skipped_duplicate = 0

    for rec in records:
        h = compute_content_hash(rec)

        if Reading.query.filter_by(content_hash=h).first():
            skipped_duplicate += 1
            continue

        member_number = rec.get("member_number")
        member = None
        if member_number is not None:
            member = Member.query.filter_by(member_number=member_number).first()
            if member is None:
                member = Member(
                    member_number=member_number,
                    display_name=rec.get("sender_name", ""),
                )
                db.session.add(member)
                db.session.flush()
            elif rec.get("sender_name") and member.display_name != rec["sender_name"]:
                member.display_name = rec["sender_name"]

        reading = Reading(
            sent_at=rec.get("sent_at"),
            month=rec["month"],
            book_title=rec.get("book_title", ""),
            content=rec.get("content", ""),
            sender_name=rec.get("sender_name", ""),
            member_number=member_number,
            member_id=member.id if member else None,
            content_hash=h,
        )
        db.session.add(reading)

        # auto-award point on import — 每月每人最多 +1 點
        if member is not None:
            month_label = f"{rec['month']}月共讀"
            already_awarded = PointTransaction.query.filter(
                PointTransaction.member_id == member.id,
                PointTransaction.reason.like(f"{rec['month']}月共讀%"),
                PointTransaction.delta > 0,
            ).first()
            if not already_awarded:
                tx = PointTransaction(
                    member_id=member.id,
                    delta=1,
                    reason=month_label,
                )
                db.session.add(tx)

        inserted += 1

    db.session.commit()
    return {
        "total_parsed": len(records),
        "inserted": inserted,
        "skipped_duplicate": skipped_duplicate,
    }


@app.route("/api/admin/import", methods=["POST"])
@admin_required
def api_admin_import():
    if "file" not in request.files:
        return jsonify({"error": "請上傳檔案"}), 400

    f = request.files["file"]
    if not f.filename.lower().endswith(".txt"):
        return jsonify({"error": "請上傳 .txt 格式的 LINE 聊天記錄"}), 400

    # raw file not persisted — 只在記憶體處理
    text = f.read().decode("utf-8", errors="replace")
    records = parse_chat_export(text)
    result = import_records(records)
    return jsonify(result)


# ── 搜尋 API（Keyword search across reading records）─────────────
@app.route("/api/search")
def api_search():
    q = request.args.get("q", "").strip()
    month = request.args.get("month", "").strip()
    try:
        page = max(1, int(request.args.get("page", 1)))
    except ValueError:
        page = 1
    per_page = 20

    query = Reading.query

    if month:
        query = query.filter(Reading.month == month)

    if q:
        like = f"%{q}%"
        query = query.filter(
            db.or_(
                Reading.book_title.ilike(like),
                Reading.content.ilike(like),
            )
        )

    total = query.count()
    readings = (
        query.order_by(Reading.sent_at.desc().nullslast(), Reading.created_at.desc())
        .offset((page - 1) * per_page)
        .limit(per_page)
        .all()
    )

    return jsonify({
        "total": total,
        "page": page,
        "results": [r.to_dict() for r in readings],
    })


# ── 成員點數 API（Member point balance query）────────────────────
@app.route("/api/member/<int:member_number>/points")
def api_member_points(member_number: int):
    # 只允許本人查詢
    if session.get("member_number") != member_number:
        return jsonify({"error": "請先登入"}), 401
    member = Member.query.filter_by(member_number=member_number).first()
    if not member:
        return jsonify({"error": "找不到此 M 編號"}), 404

    txs = (
        PointTransaction.query
        .filter_by(member_id=member.id)
        .order_by(PointTransaction.created_at.desc())
        .limit(50)
        .all()
    )
    redemptions = (
        Redemption.query
        .filter_by(member_id=member.id)
        .order_by(Redemption.created_at.desc())
        .limit(20)
        .all()
    )

    return jsonify({
        "member_number": member.member_number,
        "display_name": member.display_name,
        "points": member.points,
        "transactions": [t.to_dict() for t in txs],
        "redemptions": [r.to_dict() for r in redemptions],
    })


# ── 獎品 API（list available rewards）────────────────────────────
@app.route("/api/rewards")
def api_rewards():
    rewards = (
        Reward.query
        .filter_by(is_active=True)
        .order_by(Reward.points_required)
        .all()
    )
    return jsonify([r.to_dict() for r in rewards])


# ── 兌換 API（self-service redemption）───────────────────────────
@app.route("/api/redeem/<int:reward_id>", methods=["POST"])
def api_redeem(reward_id: int):
    data = request.get_json(force=True) or {}
    member_number = data.get("member_number")
    if member_number is None:
        return jsonify({"error": "請提供 member_number"}), 400
    if session.get("member_number") != int(member_number):
        return jsonify({"error": "請先登入"}), 401

    member = Member.query.filter_by(member_number=int(member_number)).first()
    if not member:
        return jsonify({"error": "找不到此 M 編號"}), 404

    # SELECT ... FOR UPDATE 防止 race condition on last unit
    reward = (
        db.session.query(Reward)
        .filter_by(id=reward_id)
        .with_for_update()
        .first()
    )
    if not reward:
        abort(404)

    if not reward.is_active:
        return jsonify({"error": "此獎品已下架"}), 400

    if reward.stock >= 0 and reward.remaining <= 0:
        return jsonify({"error": "此獎品已兌換完畢"}), 400

    if member.points < reward.points_required:
        return jsonify({
            "error": f"點數不足（需要 {reward.points_required} 點，目前 {member.points} 點）"
        }), 400

    tx = PointTransaction(
        member_id=member.id,
        delta=-reward.points_required,
        reason=f"兌換《{reward.name}》",
    )
    db.session.add(tx)

    redemption = Redemption(
        member_id=member.id,
        reward_id=reward.id,
        points_used=reward.points_required,
        redemption_url=reward.redemption_url or "",
    )
    db.session.add(redemption)
    db.session.commit()

    return jsonify({
        "ok": True,
        "message": "兌換成功！",
        "redemption_url": reward.redemption_url or "",
    })


# ── 管理員 API ────────────────────────────────────────────────
@app.route("/api/admin/users")
@admin_required
def api_admin_users():
    members = Member.query.order_by(Member.member_number).all()
    return jsonify([m.to_dict() for m in members])


@app.route("/api/admin/point", methods=["POST"])
@admin_required
def api_admin_point():
    data = request.get_json(force=True) or {}
    member_number = data.get("member_number")
    delta = data.get("delta")
    note = data.get("note", "")

    if member_number is None or delta is None:
        return jsonify({"error": "缺少 member_number 或 delta"}), 400
    try:
        delta = int(delta)
    except (ValueError, TypeError):
        return jsonify({"error": "delta 必須為整數"}), 400
    if delta == 0:
        return jsonify({"error": "delta 不可為 0"}), 400

    member = Member.query.filter_by(member_number=int(member_number)).first()
    if not member:
        return jsonify({"error": "找不到此 M 編號"}), 404

    tx = PointTransaction(
        member_id=member.id,
        delta=delta,
        reason="管理員手動調整",
        admin_note=note,
    )
    db.session.add(tx)
    db.session.commit()
    return jsonify({"ok": True, "new_points": member.points})


@app.route("/api/admin/rewards", methods=["GET"])
@admin_required
def api_admin_rewards_list():
    rewards = Reward.query.order_by(Reward.points_required).all()
    return jsonify([r.to_dict() for r in rewards])


@app.route("/api/admin/rewards", methods=["POST"])
@admin_required
def api_admin_reward_create():
    data = request.get_json(force=True) or {}
    name = data.get("name", "").strip()
    pts = data.get("points_required")
    if not name:
        return jsonify({"error": "請填寫獎品名稱"}), 400
    try:
        pts = int(pts)
    except (ValueError, TypeError):
        return jsonify({"error": "points_required 必須為正整數"}), 400
    if pts <= 0:
        return jsonify({"error": "points_required 必須為正整數"}), 400

    reward = Reward(
        name=name,
        description=data.get("description", ""),
        points_required=pts,
        stock=int(data.get("stock", -1)),
        is_active=bool(data.get("is_active", True)),
        redemption_url=data.get("redemption_url", ""),
    )
    db.session.add(reward)
    db.session.commit()
    return jsonify(reward.to_dict())


@app.route("/api/admin/rewards/<int:reward_id>", methods=["PATCH"])
@admin_required
def api_admin_reward_update(reward_id: int):
    reward = db.session.get(Reward, reward_id)
    if not reward:
        abort(404)
    data = request.get_json(force=True) or {}
    if "name" in data:
        reward.name = data["name"]
    if "description" in data:
        reward.description = data["description"]
    if "points_required" in data:
        pts = int(data["points_required"])
        if pts <= 0:
            return jsonify({"error": "points_required 必須為正整數"}), 400
        reward.points_required = pts
    if "stock" in data:
        reward.stock = int(data["stock"])
    if "is_active" in data:
        reward.is_active = bool(data["is_active"])
    if "redemption_url" in data:
        reward.redemption_url = data["redemption_url"]
    db.session.commit()
    return jsonify(reward.to_dict())


@app.route("/api/admin/redemptions")
@admin_required
def api_admin_redemptions():
    redemptions = (
        Redemption.query
        .order_by(Redemption.created_at.desc())
        .limit(100)
        .all()
    )
    return jsonify([r.to_dict() for r in redemptions])


@app.route("/api/admin/batch-point", methods=["POST"])
@admin_required
def api_admin_batch_point():
    """批次加點：給指定 M 編號名單加點。"""
    data = request.get_json(force=True) or {}
    raw = data.get("member_numbers", "")
    delta = data.get("delta")
    note = data.get("note", "")
    try:
        delta = int(delta)
    except (ValueError, TypeError):
        return jsonify({"error": "delta 必須為整數"}), 400
    if delta == 0:
        return jsonify({"error": "delta 不可為 0"}), 400

    # 解析 M 編號（逗號、空白、換行分隔）
    import re as _re
    mnos = [int(x) for x in _re.split(r"[\s,，]+", str(raw).strip()) if x.strip().isdigit()]
    if not mnos:
        return jsonify({"error": "請輸入至少一個 M 編號"}), 400

    ok, missing = [], []
    for mno in mnos:
        member = Member.query.filter_by(member_number=mno).first()
        if not member:
            missing.append(mno)
            continue
        db.session.add(PointTransaction(member_id=member.id, delta=delta, reason=note or "批次加點"))
        ok.append(mno)
    db.session.commit()
    return jsonify({"ok": True, "success": len(ok), "missing": missing})


@app.route("/api/bot/award-point", methods=["POST"])
def api_bot_award_point():
    """LINE Bot 呼叫：幫指定 M 編號加點（用 API Secret 驗證）。"""
    secret = os.environ.get("BOT_API_SECRET", "")
    if not secret:
        return jsonify({"error": "未設定 BOT_API_SECRET"}), 500
    auth = request.headers.get("X-Bot-Secret", "")
    if auth != secret:
        return jsonify({"error": "unauthorized"}), 401

    data = request.get_json(force=True) or {}
    member_number = data.get("member_number")
    delta = data.get("delta", 1)
    reason = data.get("reason", "LINE Bot 活動")

    if member_number is None:
        return jsonify({"error": "缺少 member_number"}), 400
    try:
        delta = int(delta)
        member_number = int(member_number)
    except (ValueError, TypeError):
        return jsonify({"error": "參數格式錯誤"}), 400

    member = Member.query.filter_by(member_number=member_number).first()
    if not member:
        return jsonify({"error": "找不到此 M 編號"}), 404

    tx = PointTransaction(member_id=member.id, delta=delta, reason=reason)
    db.session.add(tx)
    db.session.commit()
    return jsonify({"ok": True, "member_number": member_number, "new_points": member.points})


@app.route("/api/bot/member-points", methods=["GET"])
def api_bot_member_points():
    """LINE Bot 呼叫：查詢 M 編號的點數（用 API Secret 驗證）。"""
    secret = os.environ.get("BOT_API_SECRET", "")
    if not secret:
        return jsonify({"error": "未設定 BOT_API_SECRET"}), 500
    auth = request.headers.get("X-Bot-Secret", "")
    if auth != secret:
        return jsonify({"error": "unauthorized"}), 401

    try:
        member_number = int(request.args.get("member_number", 0))
    except (ValueError, TypeError):
        return jsonify({"error": "參數格式錯誤"}), 400

    member = Member.query.filter_by(member_number=member_number).first()
    if not member:
        return jsonify({"error": "找不到此 M 編號"}), 404

    return jsonify({"member_number": member_number, "display_name": member.display_name, "points": member.points})


@app.route("/api/admin/all-point", methods=["POST"])
@admin_required
def api_admin_all_point():
    """全體加點：給所有成員加點。"""
    data = request.get_json(force=True) or {}
    delta = data.get("delta")
    note = data.get("note", "")
    try:
        delta = int(delta)
    except (ValueError, TypeError):
        return jsonify({"error": "delta 必須為整數"}), 400
    if delta == 0:
        return jsonify({"error": "delta 不可為 0"}), 400

    members = Member.query.all()
    for member in members:
        db.session.add(PointTransaction(member_id=member.id, delta=delta, reason=note or "全體加點"))
    db.session.commit()
    return jsonify({"ok": True, "count": len(members)})


@app.route("/api/admin/sync-members", methods=["POST"])
@admin_required
def api_admin_sync_members():
    """從 Google Sheets 同步成員名單（欄A=顯示名稱, 欄B=M編號）。"""
    try:
        creds = get_drive_service()
        sheets = build("sheets", "v4", credentials=creds)
        result = sheets.spreadsheets().values().get(
            spreadsheetId=SHEETS_ID,
            range="工作表1!A2:B",  # 跳過標題列
        ).execute()
        rows = result.get("values", [])
    except Exception as e:
        return jsonify({"error": f"讀取 Google Sheets 失敗：{e}"}), 500

    created = 0
    updated = 0
    skipped = 0

    for row in rows:
        if len(row) < 2:
            continue
        display_name = str(row[0]).strip()
        try:
            mno = int(str(row[1]).strip())
        except ValueError:
            continue
        if not display_name or mno <= 0:
            continue

        member = Member.query.filter_by(member_number=mno).first()
        if member is None:
            member = Member(member_number=mno, display_name=display_name)
            db.session.add(member)
            created += 1
        elif member.display_name != display_name:
            member.display_name = display_name
            updated += 1
        else:
            skipped += 1

    db.session.commit()
    return jsonify({"ok": True, "created": created, "updated": updated, "skipped": skipped})


@app.route("/api/admin/redemptions/<int:redemption_id>/fulfill", methods=["POST"])
@admin_required
def api_admin_fulfill(redemption_id: int):
    redemption = db.session.get(Redemption, redemption_id)
    if not redemption:
        abort(404)
    redemption.fulfilled = True   # 冪等：已 fulfilled 也直接設定
    db.session.commit()
    return jsonify({"ok": True})


# ── 網頁路由 ──────────────────────────────────────────────────
@app.route("/")
def index():
    return render_template("index.html")


@app.route("/member")
def member_page():
    mno = session.get("member_number")
    if not mno:
        return redirect(url_for("member_login"))
    member = Member.query.filter_by(member_number=mno).first()
    if not member:
        session.pop("member_number", None)
        return redirect(url_for("member_login"))
    return render_template("member.html", member=member)


@app.route("/member/login", methods=["GET", "POST"])
def member_login():
    error = None
    if request.method == "POST":
        try:
            mno = int(request.form.get("member_number", 0))
        except ValueError:
            mno = 0
        pwd = request.form.get("password", "")
        member = Member.query.filter_by(member_number=mno).first()
        if member and member.password_hash and check_password_hash(member.password_hash, pwd):
            session["member_number"] = mno
            return redirect(url_for("member_page"))
        error = "M 編號或密碼錯誤"
    return render_template("member_login.html", error=error)


@app.route("/member/register", methods=["GET", "POST"])
def member_register():
    error = None
    if request.method == "POST":
        try:
            mno = int(request.form.get("member_number", 0))
        except ValueError:
            mno = 0
        pwd = request.form.get("password", "")
        pwd2 = request.form.get("password2", "")
        if not mno or mno <= 0:
            error = "請輸入有效的 M 編號"
        elif len(pwd) < 4:
            error = "密碼至少 4 個字"
        elif pwd != pwd2:
            error = "兩次密碼不一致"
        else:
            member = Member.query.filter_by(member_number=mno).first()
            if not member:
                error = "找不到此 M 編號，請確認後再試"
            elif member.password_hash:
                error = "此 M 編號已設定過密碼，請直接登入"
            else:
                member.password_hash = generate_password_hash(pwd)
                gmail = request.form.get("gmail", "").strip()
                if gmail:
                    member.gmail = gmail
                db.session.commit()
                session["member_number"] = mno
                return redirect(url_for("member_page"))
    return render_template("member_register.html", error=error)


@app.route("/api/member/gmail", methods=["POST"])
def api_member_update_gmail():
    mno = session.get("member_number")
    if not mno:
        return jsonify({"error": "請先登入"}), 401
    data = request.get_json(force=True) or {}
    gmail = data.get("gmail", "").strip()
    if gmail and "@" not in gmail:
        return jsonify({"error": "Gmail 格式不正確"}), 400
    member = Member.query.filter_by(member_number=mno).first()
    if not member:
        return jsonify({"error": "找不到成員"}), 404
    member.gmail = gmail or None
    db.session.commit()
    return jsonify({"ok": True, "gmail": member.gmail or ""})


@app.route("/member/logout")
def member_logout():
    session.pop("member_number", None)
    return redirect(url_for("member_login"))


if __name__ == "__main__":
    port = int(os.environ.get("PORT", 5000))
    app.run(host="0.0.0.0", port=port)
