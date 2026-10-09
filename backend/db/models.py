"""Tables for the single application database.

Secrets are never stored in a readable form: user PINs, admin passwords
and MCP client secrets are bcrypt hashes, and an external agent's auth
header value is encrypted (Fernet, key FIELD_ENCRYPTION_KEY) because it
has to be sent on to that agent, so a one way hash would not do.
"""

from datetime import datetime, timezone
from typing import Optional

from sqlalchemy import Boolean, DateTime, Float, ForeignKey, Integer, String, Text
from sqlalchemy.orm import Mapped, mapped_column

from db.base import Base


def utcnow() -> datetime:
    return datetime.now(timezone.utc)


class User(Base):
    """An existing customer, found by their landline number and confirmed
    with a PIN (see backend/auth for why a number alone is not enough)."""

    __tablename__ = "users"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    phone_e164: Mapped[str] = mapped_column(String(16), unique=True, index=True)
    display_name: Mapped[str] = mapped_column(String(80))
    pin_hash: Mapped[str] = mapped_column(String(100))
    preferred_language: Mapped[Optional[str]] = mapped_column(String(8), nullable=True)
    failed_attempts: Mapped[int] = mapped_column(Integer, default=0)
    locked_until: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    last_login_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True), nullable=True)

    # Where one time codes go (backend/otp). Both optional: a customer
    # without contact details is treated like an unknown number.
    mobile_e164: Mapped[Optional[str]] = mapped_column(String(16), nullable=True)
    email: Mapped[Optional[str]] = mapped_column(String(254), nullable=True)


class Admin(Base):
    __tablename__ = "admins"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    username: Mapped[str] = mapped_column(String(64), unique=True, index=True)
    password_hash: Mapped[str] = mapped_column(String(100))
    failed_attempts: Mapped[int] = mapped_column(Integer, default=0)
    locked_until: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    last_login_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True), nullable=True)


class ExternalAgent(Base):
    """Another chat agent, reached over HTTP, whose text replies this
    assistant speaks. At most one row has is_active set; none active means
    the built in Orchestrator answers."""

    __tablename__ = "external_agents"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    name: Mapped[str] = mapped_column(String(80), unique=True)
    url: Mapped[str] = mapped_column(String(500))
    method: Mapped[str] = mapped_column(String(8), default="POST")
    auth_header_name: Mapped[Optional[str]] = mapped_column(String(80), nullable=True)
    auth_header_value_encrypted: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    # JSON text with {text} (and optionally {user_name}, {language})
    # placeholders, for example {"message": "{text}"}.
    body_template: Mapped[str] = mapped_column(Text, default='{"message": "{text}"}')
    # Dotted path to the reply text in the JSON response, e.g. "reply" or
    # "choices.0.message.content".
    response_path: Mapped[str] = mapped_column(String(200), default="reply")
    timeout_seconds: Mapped[float] = mapped_column(Float, default=15.0)
    # Server Sent Events streaming: each event's data is a text piece, or
    # JSON where response_path points at the piece.
    streaming: Mapped[bool] = mapped_column(Boolean, default=False)
    is_active: Mapped[bool] = mapped_column(Boolean, default=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, onupdate=utcnow)


class McpClient(Base):
    """A machine client allowed to call this assistant's MCP server. The
    secret is shown once at creation and only its hash is kept."""

    __tablename__ = "mcp_clients"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    client_id: Mapped[str] = mapped_column(String(64), unique=True, index=True)
    name: Mapped[str] = mapped_column(String(80))
    secret_hash: Mapped[str] = mapped_column(String(100))
    # Space separated, e.g. "knowledge:read assistant:ask".
    scopes: Mapped[str] = mapped_column(String(200))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    last_used_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True), nullable=True)
    revoked_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True), nullable=True)


class AuditLog(Base):
    """Who called what and how it went. Deliberately holds no user
    content (no questions, no replies), only what is needed to review use."""

    __tablename__ = "audit_log"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, index=True)
    actor_type: Mapped[str] = mapped_column(String(20))  # "mcp_client" | "admin"
    actor_id: Mapped[str] = mapped_column(String(64))
    action: Mapped[str] = mapped_column(String(80))  # tool name or admin action
    outcome: Mapped[str] = mapped_column(String(20))  # "ok" | "denied" | "error"
    detail: Mapped[Optional[str]] = mapped_column(String(300), nullable=True)
    duration_ms: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)
    mcp_client_id: Mapped[Optional[int]] = mapped_column(
        ForeignKey("mcp_clients.id", ondelete="SET NULL"), nullable=True
    )


class OtpChallenge(Base):
    """One requested one time code. Holds only keyed hashes (backend/otp/
    hashing.py), never a phone number, address, destination or code.
    user_id is NULL for a decoy challenge (unknown number, anti enumeration)."""

    __tablename__ = "otp_challenges"

    id: Mapped[str] = mapped_column(String(32), primary_key=True)
    user_id: Mapped[Optional[int]] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), nullable=True)
    phone_hash: Mapped[str] = mapped_column(String(64), index=True)
    code_hash: Mapped[str] = mapped_column(String(64))
    destination_hash: Mapped[str] = mapped_column(String(64))
    channel: Mapped[str] = mapped_column(String(8))  # "sms" | "email"
    attempts: Mapped[int] = mapped_column(Integer, default=0)
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    consumed_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, index=True)
    client_ip_hash: Mapped[str] = mapped_column(String(64), index=True)
    # Hub OTP backend only: Fernet sealed {phone, hub session id}.
    hub_ref: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    # Verification wizard (revision 0003): "verify" for wizard codes, NULL for /auth/otp.
    purpose: Mapped[Optional[str]] = mapped_column(String(16), nullable=True)
    wizard_ref: Mapped[Optional[str]] = mapped_column(String(64), nullable=True)


# Registers the verification tables on Base.metadata for Alembic and create_all.
import verification.models  # noqa: E402,F401
