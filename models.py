from datetime import datetime, timezone, timedelta
from flask_sqlalchemy import SQLAlchemy

db = SQLAlchemy()
TW = timezone(timedelta(hours=8))


def tw_now():
    return datetime.now(TW)


class Member(db.Model):
    """以 M 編號識別的社群成員。"""
    __tablename__ = "members"

    id = db.Column(db.Integer, primary_key=True)
    member_number = db.Column(db.Integer, unique=True, nullable=False)
    display_name = db.Column(db.String(128), default="")
    password_hash = db.Column(db.String(256), nullable=True)   # 會員自設密碼
    created_at = db.Column(db.DateTime(timezone=True), default=tw_now)

    transactions = db.relationship("PointTransaction", backref="member", lazy="dynamic")
    redemptions = db.relationship("Redemption", backref="member", lazy="dynamic")

    @property
    def points(self):
        result = db.session.query(
            db.func.coalesce(db.func.sum(PointTransaction.delta), 0)
        ).filter(PointTransaction.member_id == self.id).scalar()
        return int(result)

    def to_dict(self):
        return {
            "id": self.id,
            "member_number": self.member_number,
            "display_name": self.display_name,
            "points": self.points,
        }


class Reading(db.Model):
    """共讀紀錄，從 LINE 聊天記錄解析而來。"""
    __tablename__ = "readings"

    id = db.Column(db.Integer, primary_key=True)
    created_at = db.Column(db.DateTime(timezone=True), default=tw_now)   # 匯入時間
    sent_at = db.Column(db.DateTime(timezone=True), nullable=True)        # 訊息原始日期
    month = db.Column(db.String(4), nullable=False)                       # "1"–"12"
    book_title = db.Column(db.String(256), default="")
    content = db.Column(db.Text, default="")
    sender_name = db.Column(db.String(128), default="")                   # LINE 顯示名稱原文
    member_number = db.Column(db.Integer, nullable=True)                  # 從名稱提取，可為 NULL
    member_id = db.Column(db.Integer, db.ForeignKey("members.id"), nullable=True)
    content_hash = db.Column(db.String(64), unique=True, nullable=False)  # 去重用 SHA-256

    member = db.relationship("Member", backref=db.backref("readings", lazy="dynamic"))

    def to_dict(self):
        return {
            "id": self.id,
            "created_at": self.sent_at.strftime("%Y-%m-%d") if self.sent_at else (
                self.created_at.strftime("%Y-%m-%d") if self.created_at else ""
            ),
            "month": self.month,
            "book_title": self.book_title or "",
            "content": self.content or "",
            "display_name": self.sender_name or "",
            "member_number": self.member_number,
        }


class PointTransaction(db.Model):
    """點數異動紀錄（正數=增加，負數=扣除）。"""
    __tablename__ = "point_transactions"

    id = db.Column(db.Integer, primary_key=True)
    created_at = db.Column(db.DateTime(timezone=True), default=tw_now)
    member_id = db.Column(db.Integer, db.ForeignKey("members.id"), nullable=False)
    delta = db.Column(db.Integer, nullable=False)
    reason = db.Column(db.String(256), default="")
    admin_note = db.Column(db.String(256), default="")

    def to_dict(self):
        return {
            "id": self.id,
            "created_at": self.created_at.strftime("%Y-%m-%d %H:%M") if self.created_at else "",
            "delta": self.delta,
            "reason": self.reason,
        }


class Reward(db.Model):
    """可兌換獎品。"""
    __tablename__ = "rewards"

    id = db.Column(db.Integer, primary_key=True)
    name = db.Column(db.String(128), nullable=False)
    description = db.Column(db.Text, default="")
    points_required = db.Column(db.Integer, nullable=False)
    stock = db.Column(db.Integer, default=-1)      # -1 = 無限量
    is_active = db.Column(db.Boolean, default=True, nullable=False)
    redemption_url = db.Column(db.Text, default="")   # 兌換成功後顯示的連結

    redemptions = db.relationship("Redemption", backref="reward", lazy="dynamic")

    @property
    def remaining(self):
        if self.stock < 0:
            return -1
        used = self.redemptions.count()
        return max(0, self.stock - used)

    def to_dict(self):
        return {
            "id": self.id,
            "name": self.name,
            "description": self.description or "",
            "points_required": self.points_required,
            "stock": self.stock,
            "remaining": self.remaining,
            "is_active": self.is_active,
            "redemption_url": self.redemption_url or "",
        }


class Redemption(db.Model):
    """兌換紀錄。"""
    __tablename__ = "redemptions"

    id = db.Column(db.Integer, primary_key=True)
    created_at = db.Column(db.DateTime(timezone=True), default=tw_now)
    member_id = db.Column(db.Integer, db.ForeignKey("members.id"), nullable=False)
    reward_id = db.Column(db.Integer, db.ForeignKey("rewards.id"), nullable=False)
    points_used = db.Column(db.Integer, nullable=False)
    fulfilled = db.Column(db.Boolean, default=False, nullable=False)
    redemption_url = db.Column(db.Text, default="")   # 兌換當下的連結快照

    def to_dict(self):
        return {
            "id": self.id,
            "created_at": self.created_at.strftime("%Y-%m-%d %H:%M") if self.created_at else "",
            "member_number": self.member.member_number if self.member else None,
            "display_name": self.member.display_name if self.member else "",
            "reward_name": self.reward.name if self.reward else "",
            "points_used": self.points_used,
            "fulfilled": self.fulfilled,
            "redemption_url": self.redemption_url or "",
        }
