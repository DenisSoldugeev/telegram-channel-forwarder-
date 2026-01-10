from datetime import datetime

from sqlalchemy import (
    BigInteger,
    Boolean,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    LargeBinary,
    String,
    Text,
    UniqueConstraint,
)
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, relationship


class Base(DeclarativeBase):
    """Base class for all models."""

    pass


class User(Base):
    """Telegram user who uses the bot."""

    __tablename__ = "users"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True)  # Telegram user_id
    phone: Mapped[str | None] = mapped_column(String(255), nullable=True)  # Encrypted
    state: Mapped[str] = mapped_column(String(50), default="idle", nullable=False)
    is_active: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime, default=datetime.utcnow, nullable=False
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime, default=datetime.utcnow, onupdate=datetime.utcnow, nullable=False
    )

    # Relationships
    session: Mapped["Session | None"] = relationship(
        "Session", back_populates="user", uselist=False, cascade="all, delete-orphan"
    )
    sources: Mapped[list["Source"]] = relationship(
        "Source", back_populates="user", cascade="all, delete-orphan"
    )
    destination: Mapped["Destination | None"] = relationship(
        "Destination", back_populates="user", uselist=False, cascade="all, delete-orphan"
    )
    delivery_logs: Mapped[list["DeliveryLog"]] = relationship(
        "DeliveryLog", back_populates="user", cascade="all, delete-orphan"
    )

    __table_args__ = (Index("idx_users_state", "state"), Index("idx_users_active", "is_active"))


class Session(Base):
    """Encrypted Pyrogram session for a user."""

    __tablename__ = "sessions"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    user_id: Mapped[int] = mapped_column(
        BigInteger, ForeignKey("users.id", ondelete="CASCADE"), unique=True, nullable=False
    )
    session_data: Mapped[bytes] = mapped_column(LargeBinary, nullable=False)  # Encrypted
    session_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    is_valid: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime, default=datetime.utcnow, nullable=False
    )
    last_used_at: Mapped[datetime] = mapped_column(
        DateTime, default=datetime.utcnow, nullable=False
    )
    expires_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)

    # Relationships
    user: Mapped["User"] = relationship("User", back_populates="session")

    __table_args__ = (Index("idx_sessions_valid", "is_valid", "user_id"),)


class Source(Base):
    """Channel source to monitor for new messages."""

    __tablename__ = "sources"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    user_id: Mapped[int] = mapped_column(
        BigInteger, ForeignKey("users.id", ondelete="CASCADE"), nullable=False
    )
    channel_id: Mapped[int] = mapped_column(BigInteger, nullable=False)
    channel_username: Mapped[str | None] = mapped_column(String(255), nullable=True)
    channel_title: Mapped[str] = mapped_column(String(255), nullable=False)
    is_active: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)
    last_message_id: Mapped[int] = mapped_column(BigInteger, default=0, nullable=False)
    added_at: Mapped[datetime] = mapped_column(
        DateTime, default=datetime.utcnow, nullable=False
    )
    last_checked_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)

    # Relationships
    user: Mapped["User"] = relationship("User", back_populates="sources")
    delivery_logs: Mapped[list["DeliveryLog"]] = relationship(
        "DeliveryLog", back_populates="source", cascade="all, delete-orphan"
    )

    __table_args__ = (
        UniqueConstraint("user_id", "channel_id", name="uq_source_user_channel"),
        Index("idx_sources_active", "user_id", "is_active"),
    )


class Destination(Base):
    """Target channel where messages are forwarded."""

    __tablename__ = "destinations"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    user_id: Mapped[int] = mapped_column(
        BigInteger, ForeignKey("users.id", ondelete="CASCADE"), unique=True, nullable=False
    )
    channel_id: Mapped[int] = mapped_column(BigInteger, nullable=False)
    channel_username: Mapped[str | None] = mapped_column(String(255), nullable=True)
    channel_title: Mapped[str] = mapped_column(String(255), nullable=False)
    is_active: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)
    configured_at: Mapped[datetime] = mapped_column(
        DateTime, default=datetime.utcnow, nullable=False
    )

    # Relationships
    user: Mapped["User"] = relationship("User", back_populates="destination")
    delivery_logs: Mapped[list["DeliveryLog"]] = relationship(
        "DeliveryLog", back_populates="destination", cascade="all, delete-orphan"
    )


class ForwardingRule(Base):
    """Rule connecting source to destination (for future filtering)."""

    __tablename__ = "forwarding_rules"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    user_id: Mapped[int] = mapped_column(
        BigInteger, ForeignKey("users.id", ondelete="CASCADE"), nullable=False
    )
    source_id: Mapped[int] = mapped_column(
        Integer, ForeignKey("sources.id", ondelete="CASCADE"), nullable=False
    )
    destination_id: Mapped[int] = mapped_column(
        Integer, ForeignKey("destinations.id", ondelete="CASCADE"), nullable=False
    )
    is_enabled: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)
    filters: Mapped[str | None] = mapped_column(Text, nullable=True)  # JSON for future filters
    created_at: Mapped[datetime] = mapped_column(
        DateTime, default=datetime.utcnow, nullable=False
    )

    __table_args__ = (
        UniqueConstraint("source_id", "destination_id", name="uq_rule_source_dest"),
    )


class DeliveryLog(Base):
    """Log of message forwarding attempts."""

    __tablename__ = "delivery_logs"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    user_id: Mapped[int] = mapped_column(
        BigInteger, ForeignKey("users.id", ondelete="CASCADE"), nullable=False
    )
    source_id: Mapped[int | None] = mapped_column(
        Integer, ForeignKey("sources.id", ondelete="SET NULL"), nullable=True
    )
    destination_id: Mapped[int | None] = mapped_column(
        Integer, ForeignKey("destinations.id", ondelete="SET NULL"), nullable=True
    )
    original_message_id: Mapped[int] = mapped_column(BigInteger, nullable=False)
    forwarded_message_id: Mapped[int | None] = mapped_column(BigInteger, nullable=True)
    status: Mapped[str] = mapped_column(String(20), default="pending", nullable=False)
    error_message: Mapped[str | None] = mapped_column(Text, nullable=True)
    retry_count: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime, default=datetime.utcnow, nullable=False
    )
    completed_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)

    # Relationships
    user: Mapped["User"] = relationship("User", back_populates="delivery_logs")
    source: Mapped["Source | None"] = relationship("Source", back_populates="delivery_logs")
    destination: Mapped["Destination | None"] = relationship(
        "Destination", back_populates="delivery_logs"
    )

    __table_args__ = (
        Index("idx_delivery_user_status", "user_id", "status"),
        Index("idx_delivery_dedup", "user_id", "source_id", "original_message_id"),
        Index("idx_delivery_created", "created_at"),
    )


class AuthAttempt(Base):
    """Temporary storage for authentication flow."""

    __tablename__ = "auth_attempts"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    user_id: Mapped[int] = mapped_column(BigInteger, nullable=False)
    phone: Mapped[str] = mapped_column(String(255), nullable=False)  # Encrypted
    phone_code_hash: Mapped[str | None] = mapped_column(String(255), nullable=True)
    attempt_count: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    stage: Mapped[str] = mapped_column(String(20), default="phone", nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime, default=datetime.utcnow, nullable=False
    )
    expires_at: Mapped[datetime] = mapped_column(DateTime, nullable=False)

    __table_args__ = (Index("idx_auth_user", "user_id"),)
