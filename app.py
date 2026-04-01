import os
from functools import wraps

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
    )
    db.session.add(redemption)
    db.session.commit()

    return jsonify({"ok": True, "message": "兌換成功！管理員將盡快為你處理 🎁"})


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
    return render_template("member.html")


if __name__ == "__main__":
    port = int(os.environ.get("PORT", 5000))
    app.run(host="0.0.0.0", port=port)
