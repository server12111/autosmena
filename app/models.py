import time
import uuid

from sqlalchemy import (
    BigInteger,
    Boolean,
    CheckConstraint,
    ForeignKey,
    Integer,
    String,
    Text,
    UniqueConstraint,
)
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column


def uid():
    return uuid.uuid4().hex


def now():
    return int(time.time())


class Base(DeclarativeBase):
    pass


class User(Base):
    __tablename__ = "users"
    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=False)
    username: Mapped[str] = mapped_column(String(128), default="")
    balance: Mapped[int] = mapped_column(BigInteger, default=0)
    blocked: Mapped[bool] = mapped_column(Boolean, default=False)
    created_at: Mapped[int] = mapped_column(BigInteger, default=now)
    __table_args__ = (CheckConstraint("balance >= 0"),)


class Product(Base):
    __tablename__ = "products"
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    supplier_code: Mapped[str] = mapped_column(String(128), unique=True)
    name: Mapped[str] = mapped_column(String(128))
    country: Mapped[str] = mapped_column(String(64))
    kind: Mapped[str] = mapped_column(String(16), default="new")
    cost: Mapped[int] = mapped_column(BigInteger)
    markup_bps: Mapped[int] = mapped_column(Integer, default=3000)
    fixed_price: Mapped[int | None] = mapped_column(BigInteger, nullable=True)
    enabled: Mapped[bool] = mapped_column(Boolean, default=True)
    __table_args__ = (
        CheckConstraint("cost >= 0"),
        CheckConstraint("markup_bps >= 0"),
        CheckConstraint("fixed_price IS NULL OR fixed_price > 0"),
    )


class Order(Base):
    __tablename__ = "orders"
    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=uid)
    request_key: Mapped[str] = mapped_column(String(100), unique=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id"), index=True)
    product_id: Mapped[int] = mapped_column(ForeignKey("products.id"))
    price: Mapped[int] = mapped_column(BigInteger)
    cost: Mapped[int] = mapped_column(BigInteger)
    status: Mapped[str] = mapped_column(String(32), default="reserved", index=True)
    supplier_id: Mapped[str | None] = mapped_column(String(128), unique=True, nullable=True)
    phone: Mapped[str] = mapped_column(String(32), default="")
    sms: Mapped[str] = mapped_column(Text, default="")
    supplier_payload: Mapped[str] = mapped_column(Text, default="{}")
    parent_id: Mapped[str | None] = mapped_column(ForeignKey("orders.id"), nullable=True)
    error: Mapped[str] = mapped_column(String(256), default="")
    created_at: Mapped[int] = mapped_column(BigInteger, default=now)
    updated_at: Mapped[int] = mapped_column(BigInteger, default=now)


class Payment(Base):
    __tablename__ = "payments"
    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=uid)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id"), index=True)
    provider: Mapped[str] = mapped_column(String(24))
    external_id: Mapped[str | None] = mapped_column(String(128), nullable=True)
    amount: Mapped[int] = mapped_column(BigInteger)
    crypto_amount: Mapped[str] = mapped_column(String(64), default="")
    asset: Mapped[str] = mapped_column(String(12), default="USD")
    status: Mapped[str] = mapped_column(String(24), default="creating", index=True)
    url: Mapped[str] = mapped_column(Text, default="")
    reference: Mapped[str] = mapped_column(String(64), unique=True, default=uid)
    transaction_hash: Mapped[str | None] = mapped_column(String(128), unique=True, nullable=True)
    created_at: Mapped[int] = mapped_column(BigInteger, default=now)
    expires_at: Mapped[int] = mapped_column(BigInteger)
    paid_at: Mapped[int | None] = mapped_column(BigInteger, nullable=True)
    __table_args__ = (UniqueConstraint("provider", "external_id"), CheckConstraint("amount > 0"))


class Ledger(Base):
    __tablename__ = "ledger"
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id"), index=True)
    delta: Mapped[int] = mapped_column(BigInteger)
    event: Mapped[str] = mapped_column(String(128), unique=True)
    note: Mapped[str] = mapped_column(String(256), default="")
    created_at: Mapped[int] = mapped_column(BigInteger, default=now)


class Audit(Base):
    __tablename__ = "audit"
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    action: Mapped[str] = mapped_column(String(128))
    detail: Mapped[str] = mapped_column(Text)
    created_at: Mapped[int] = mapped_column(BigInteger, default=now)


class AdminSession(Base):
    __tablename__ = "admin_sessions"
    token_hash: Mapped[str] = mapped_column(String(64), primary_key=True)
    csrf: Mapped[str] = mapped_column(String(64))
    expires_at: Mapped[int] = mapped_column(BigInteger)


class Notice(Base):
    __tablename__ = "notices"
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id"))
    event: Mapped[str] = mapped_column(String(128), unique=True)
    text: Mapped[str] = mapped_column(Text)
    sent: Mapped[bool] = mapped_column(Boolean, default=False, index=True)
