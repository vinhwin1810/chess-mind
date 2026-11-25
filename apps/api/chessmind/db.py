import os
from datetime import datetime, timezone
from uuid import UUID, uuid4

from sqlalchemy import DateTime, ForeignKey, Index, JSON, String, Text, UniqueConstraint, create_engine
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, sessionmaker


def utcnow():
    return datetime.now(timezone.utc)


class Base(DeclarativeBase):
    type_annotation_map = {datetime: DateTime(timezone=True)}


class User(Base):
    __tablename__ = "users"
    id: Mapped[UUID] = mapped_column(primary_key=True, default=uuid4)
    email: Mapped[str] = mapped_column(String(254), unique=True)
    display_name: Mapped[str] = mapped_column(String(100))
    created_at: Mapped[datetime] = mapped_column(default=utcnow)


class Game(Base):
    __tablename__ = "games"
    __table_args__ = (
        UniqueConstraint("owner_id", "canonical_hash"),
        Index("ix_games_owner_created", "owner_id", "created_at", "id"),
    )
    id: Mapped[UUID] = mapped_column(primary_key=True, default=uuid4)
    owner_id: Mapped[UUID] = mapped_column(ForeignKey("users.id"))
    source: Mapped[str] = mapped_column(String(16), default="upload")
    source_game_url: Mapped[str | None] = mapped_column(Text)
    external_id: Mapped[str | None] = mapped_column(Text)
    pgn: Mapped[str] = mapped_column(Text)
    white_username: Mapped[str | None] = mapped_column(Text)
    black_username: Mapped[str | None] = mapped_column(Text)
    user_color: Mapped[str | None] = mapped_column(String(5))
    time_class: Mapped[str | None] = mapped_column(String(20))
    time_control: Mapped[str | None] = mapped_column(Text)
    result: Mapped[str] = mapped_column(String(7))
    played_at: Mapped[datetime | None]
    created_at: Mapped[datetime] = mapped_column(default=utcnow)
    canonical_hash: Mapped[str] = mapped_column(String(64))


class Move(Base):
    __tablename__ = "moves"
    __table_args__ = (UniqueConstraint("game_id", "ply"),)
    id: Mapped[UUID] = mapped_column(primary_key=True, default=uuid4)
    game_id: Mapped[UUID] = mapped_column(ForeignKey("games.id", ondelete="CASCADE"))
    ply: Mapped[int]
    uci: Mapped[str] = mapped_column(String(8))
    san: Mapped[str] = mapped_column(String(24))
    fen_before: Mapped[str] = mapped_column(Text)
    fen_after: Mapped[str] = mapped_column(Text)


class Job(Base):
    __tablename__ = "analysis_jobs"
    __table_args__ = (
        UniqueConstraint("requesting_user_id", "game_id", "idempotency_key"),
        Index("ix_jobs_owner_status", "requesting_user_id", "status"),
        Index("ix_jobs_status_lease", "status", "lease_until"),
    )
    id: Mapped[UUID] = mapped_column(primary_key=True, default=uuid4)
    requesting_user_id: Mapped[UUID] = mapped_column(ForeignKey("users.id"))
    game_id: Mapped[UUID] = mapped_column(ForeignKey("games.id", ondelete="CASCADE"), index=True)
    status: Mapped[str] = mapped_column(String(16), default="queued")
    attempt_count: Mapped[int] = mapped_column(default=0)
    engine_version: Mapped[str | None] = mapped_column(Text)
    depth: Mapped[int]
    error_code: Mapped[str | None] = mapped_column(String(40))
    created_at: Mapped[datetime] = mapped_column(default=utcnow)
    completed_at: Mapped[datetime | None]
    lease_until: Mapped[datetime | None]
    next_dispatch_at: Mapped[datetime | None]
    claim_token: Mapped[UUID | None]
    idempotency_key: Mapped[str] = mapped_column(String(80))


class Evaluation(Base):
    __tablename__ = "move_evaluations"
    __table_args__ = (UniqueConstraint("analysis_job_id", "ply"),)
    id: Mapped[UUID] = mapped_column(primary_key=True, default=uuid4)
    game_id: Mapped[UUID] = mapped_column(ForeignKey("games.id", ondelete="CASCADE"), index=True)
    analysis_job_id: Mapped[UUID] = mapped_column(ForeignKey("analysis_jobs.id", ondelete="CASCADE"))
    ply: Mapped[int]
    engine_version: Mapped[str] = mapped_column(Text)
    depth: Mapped[int]
    score_before: Mapped[int | None]
    score_after: Mapped[int | None]
    mate_before: Mapped[int | None]
    mate_after: Mapped[int | None]
    best_move_uci: Mapped[str | None] = mapped_column(String(8))
    pv_uci: Mapped[list[str]] = mapped_column(JSON)
    cp_loss: Mapped[int | None]
    position_metadata: Mapped[dict] = mapped_column(JSON)


engine = create_engine(os.getenv("DATABASE_URL", "sqlite:///./chessmind.db"), pool_pre_ping=True)
Session = sessionmaker(engine, expire_on_commit=False)
