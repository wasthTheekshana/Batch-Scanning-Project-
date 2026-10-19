"""SQLAlchemy models for the central service (spec: Data model)."""
from datetime import datetime, timezone
from typing import Optional

from sqlalchemy import (Boolean, DateTime, ForeignKey, Integer, String, Text,
                        UniqueConstraint)
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column


def utcnow() -> datetime:
    """Naive UTC: SQLite drops tzinfo, so every column stores naive UTC."""
    return datetime.now(timezone.utc).replace(tzinfo=None)


class Base(DeclarativeBase):
    pass


class Pc(Base):
    __tablename__ = "pcs"
    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    display_name: Mapped[str] = mapped_column(String(200), unique=True)
    api_key_hash: Mapped[Optional[str]] = mapped_column(String(64), index=True)
    registered_at: Mapped[Optional[datetime]] = mapped_column(DateTime)
    last_seen_at: Mapped[Optional[datetime]] = mapped_column(DateTime)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)
    # what the PC last reported about itself (fleet monitoring); counts and versions only
    app_version: Mapped[Optional[str]] = mapped_column(String(32))
    reported_at: Mapped[Optional[datetime]] = mapped_column(DateTime)
    # what it says about its queue of documents not yet reported here (counts and a time only)
    docs_waiting: Mapped[Optional[int]] = mapped_column(Integer)
    docs_refused: Mapped[Optional[int]] = mapped_column(Integer)
    docs_oldest_at: Mapped[Optional[datetime]] = mapped_column(DateTime)


class EnrollmentCode(Base):
    __tablename__ = "enrollment_codes"
    code_hash: Mapped[str] = mapped_column(String(64), primary_key=True)
    pc_id: Mapped[str] = mapped_column(ForeignKey("pcs.id"))
    expires_at: Mapped[datetime] = mapped_column(DateTime)
    used_at: Mapped[Optional[datetime]] = mapped_column(DateTime)


class AdminUser(Base):
    __tablename__ = "admin_users"
    id: Mapped[int] = mapped_column(Integer, primary_key=True,
                                    autoincrement=True)
    username: Mapped[str] = mapped_column(String(100), unique=True)
    password_hash: Mapped[str] = mapped_column(String(100))
    role: Mapped[str] = mapped_column(String(20), default="admin")
    failed_count: Mapped[int] = mapped_column(Integer, default=0)
    locked_until: Mapped[Optional[datetime]] = mapped_column(DateTime)
    session_version: Mapped[int] = mapped_column(Integer, default=0)


class Project(Base):
    __tablename__ = "projects"
    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    name: Mapped[str] = mapped_column(String(200))
    config_json: Mapped[str] = mapped_column(Text, default="{}")
    current_version: Mapped[int] = mapped_column(Integer, default=0)


class Template(Base):
    __tablename__ = "project_templates"
    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    name: Mapped[str] = mapped_column(String(200))
    category: Mapped[str] = mapped_column(String(100), default="")
    config_json: Mapped[str] = mapped_column(Text, default="{}")
    built_in: Mapped[bool] = mapped_column(Boolean, default=False)
    current_version: Mapped[int] = mapped_column(Integer, default=0)


class ConfigVersion(Base):
    """Append-only history; enables the admin history view and rollback."""
    __tablename__ = "config_versions"
    __table_args__ = (UniqueConstraint("kind", "owner_id", "version"),)
    id: Mapped[int] = mapped_column(Integer, primary_key=True,
                                    autoincrement=True)
    kind: Mapped[str] = mapped_column(String(10))  # "project" | "template"
    owner_id: Mapped[str] = mapped_column(String(36))
    version: Mapped[int] = mapped_column(Integer)
    config_json: Mapped[str] = mapped_column(Text)
    created_by: Mapped[str] = mapped_column(String(100))
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)


class PcAssignment(Base):
    __tablename__ = "pc_assignments"
    pc_id: Mapped[str] = mapped_column(ForeignKey("pcs.id"),
                                       primary_key=True)
    project_id: Mapped[str] = mapped_column(ForeignKey("projects.id"),
                                            primary_key=True)


class PcAppliedProject(Base):
    """The settings version of a project a PC says it is running (from its heartbeat). Not a
    foreign key to projects: a PC may still report a project that was deleted here."""
    __tablename__ = "pc_applied"
    pc_id: Mapped[str] = mapped_column(ForeignKey("pcs.id"), primary_key=True)
    project_id: Mapped[str] = mapped_column(String(64), primary_key=True)
    version: Mapped[int] = mapped_column(Integer)
    reported_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)


class PcDailyStat(Base):
    """One PC's running totals for one UTC day: how much it processed. Counts only."""
    __tablename__ = "pc_daily_stats"
    pc_id: Mapped[str] = mapped_column(ForeignKey("pcs.id"), primary_key=True)
    day: Mapped[str] = mapped_column(String(10), primary_key=True)     # YYYY-MM-DD
    documents: Mapped[int] = mapped_column(Integer, default=0)
    pages: Mapped[int] = mapped_column(Integer, default=0)
    batches: Mapped[int] = mapped_column(Integer, default=0)
    errors: Mapped[int] = mapped_column(Integer, default=0)


class AlertState(Base):
    """An alert the webhook has announced as open. A row exists exactly while the administrators have been
    told the alert is open; the unique key is what stops two server processes announcing it twice."""
    __tablename__ = "alert_state"
    alert_id: Mapped[str] = mapped_column(String(200), primary_key=True)
    severity: Mapped[str] = mapped_column(String(10))
    pc_name: Mapped[str] = mapped_column(String(200), default="")
    message: Mapped[str] = mapped_column(Text, default="")
    opened_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)
    last_sent_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)


class Setting(Base):
    __tablename__ = "settings"
    key: Mapped[str] = mapped_column(String(100), primary_key=True)
    value: Mapped[str] = mapped_column(Text)


class AuditEvent(Base):
    __tablename__ = "audit_events"
    id: Mapped[int] = mapped_column(Integer, primary_key=True,
                                    autoincrement=True)
    at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)
    actor: Mapped[str] = mapped_column(String(150))
    action: Mapped[str] = mapped_column(String(100))
    detail: Mapped[str] = mapped_column(Text, default="{}")


class Document(Base):
    """A document a PC finished, as central knows it: facts about it, not its PDF. The index fields live in
    IndexRevision (encrypted). One row per (PC, the PC's own document id)."""
    __tablename__ = "documents"
    __table_args__ = (UniqueConstraint("pc_id", "source_document_id"),)
    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    pc_id: Mapped[str] = mapped_column(ForeignKey("pcs.id"), index=True)
    source_document_id: Mapped[str] = mapped_column(String(64))
    project_id: Mapped[str] = mapped_column(String(64), index=True)
    batch_id: Mapped[str] = mapped_column(String(64), index=True)
    page_count: Mapped[int] = mapped_column(Integer, default=0)
    validation_status: Mapped[str] = mapped_column(String(30), default="")
    created_at: Mapped[datetime] = mapped_column(DateTime, index=True)       # when the PC made it
    received_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)
    current_revision: Mapped[int] = mapped_column(Integer, default=0)
    # An administrator deleted it: the index data and file details are gone, this row stays as a marker so the
    # station's queue does not bring the document back.
    deleted_at: Mapped[Optional[datetime]] = mapped_column(DateTime)
    deleted_by: Mapped[Optional[str]] = mapped_column(String(100))


class DocumentFile(Base):
    """The PDF's details. The bytes are not kept centrally (file_state "metadata_only"); the file name and
    where it was delivered can carry customer data, so they are stored encrypted."""
    __tablename__ = "document_files"
    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    document_id: Mapped[str] = mapped_column(ForeignKey("documents.id"), index=True)
    size: Mapped[int] = mapped_column(Integer, default=0)
    sha256: Mapped[str] = mapped_column(String(64), default="")
    delivery_status: Mapped[str] = mapped_column(String(30), default="")
    details_enc: Mapped[str] = mapped_column(Text, default="")                # {file_name, delivered_to}
    file_state: Mapped[str] = mapped_column(String(20), default="metadata_only")
    received_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)
    is_current: Mapped[bool] = mapped_column(Boolean, default=True)


class IndexRevision(Base):
    """Append-only: every different set of index fields a PC reports becomes the next revision."""
    __tablename__ = "index_revisions"
    __table_args__ = (UniqueConstraint("document_id", "revision"),)
    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    document_id: Mapped[str] = mapped_column(ForeignKey("documents.id"), index=True)
    revision: Mapped[int] = mapped_column(Integer)
    fields_enc: Mapped[str] = mapped_column(Text)
    fields_hash: Mapped[str] = mapped_column(String(64))                      # keyed, so equal content is recognised
    validation_status: Mapped[str] = mapped_column(String(30), default="")
    project_version: Mapped[Optional[int]] = mapped_column(Integer)
    edited_at: Mapped[Optional[datetime]] = mapped_column(DateTime)
    received_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)
    source: Mapped[str] = mapped_column(String(10), default="pc")
    is_current: Mapped[bool] = mapped_column(Boolean, default=True)


class IndexLookup(Base):
    """The blind index of the current revision's fields: an exact-match search without a plain-text column."""
    __tablename__ = "index_lookup"
    document_id: Mapped[str] = mapped_column(ForeignKey("documents.id"), primary_key=True)
    field_name: Mapped[str] = mapped_column(String(64), primary_key=True)
    blind_hash: Mapped[str] = mapped_column(String(64), index=True)


class DocumentAccessLog(Base):
    """Who searched for or opened which document. Records the kind of filter used, never the value."""
    __tablename__ = "document_access_log"
    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    at: Mapped[datetime] = mapped_column(DateTime, default=utcnow, index=True)
    username: Mapped[str] = mapped_column(String(100))
    document_id: Mapped[Optional[str]] = mapped_column(String(36))
    action: Mapped[str] = mapped_column(String(20))                           # search | view | history
    detail: Mapped[str] = mapped_column(Text, default="{}")
    ip: Mapped[str] = mapped_column(String(64), default="")


class SearcherScope(Base):
    """The projects a `searcher` may see documents of. No rows: none."""
    __tablename__ = "searcher_scope"
    admin_id: Mapped[int] = mapped_column(ForeignKey("admin_users.id"), primary_key=True)
    project_id: Mapped[str] = mapped_column(String(64), primary_key=True)
