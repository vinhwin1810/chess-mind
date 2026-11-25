import os
from datetime import datetime
from typing import Literal
from uuid import UUID

from fastapi import BackgroundTasks, Depends, FastAPI, Header, HTTPException, Query, Request
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel, ConfigDict, Field, ValidationError
from sqlalchemy import func, select, update

from .db import Evaluation, Game, Job, Move, Session, User
from .pgn import MAX_PGN_BYTES, InvalidPGN, parse_pgn
from .worker import publish

app = FastAPI(title="ChessMind AI", version="1.0")
app.add_middleware(CORSMiddleware, allow_origins=[os.getenv("WEB_ORIGIN", "http://localhost:3000")],
                   allow_methods=["GET", "POST"], allow_headers=["Content-Type", "Idempotency-Key"])
PREFIX = "/api/v1"
DEMO_ID = UUID("00000000-0000-4000-8000-000000000001")


def fail(status, code, message):
    raise HTTPException(status, detail={"code": code, "message": message})


def actor():
    if os.getenv("CHESSMIND_DEMO_MODE") != "true":
        fail(503, "auth_unavailable", "Local demo identity is disabled.")
    return DEMO_ID


def owned_game(db, owner, game_id):
    game = db.scalar(select(Game).where(Game.id == game_id, Game.owner_id == owner))
    if game is None:
        fail(404, "game_not_found", "Game not found.")
    return game


class ResponseModel(BaseModel):
    model_config = ConfigDict(from_attributes=True)


class GameSummary(ResponseModel):
    id: UUID
    source: str
    white_username: str | None
    black_username: str | None
    result: str
    time_control: str | None
    played_at: datetime | None
    created_at: datetime


class MoveData(ResponseModel):
    ply: int
    san: str
    uci: str
    fen_before: str
    fen_after: str


class GameDetail(GameSummary):
    pgn: str
    moves: list[MoveData]


class GamesPage(BaseModel):
    games: list[GameSummary]
    next_offset: int | None


class ImportBody(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)
    pgn: str


class ImportResult(BaseModel):
    game_ids: list[UUID]
    imported: int
    duplicates: int


class AnalysisBody(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)
    depth: int = Field(default=12, ge=4, le=18)


class JobData(ResponseModel):
    id: UUID
    game_id: UUID
    status: str
    attempt_count: int
    engine_version: str | None
    depth: int
    error_code: str | None
    created_at: datetime
    completed_at: datetime | None


class EvaluationData(ResponseModel):
    ply: int
    depth: int
    engine_version: str
    score_before: int | None
    score_after: int | None
    mate_before: int | None
    mate_after: int | None
    best_move_uci: str | None
    pv_uci: list[str]
    cp_loss: int | None
    position_metadata: dict


class AnalysisResult(BaseModel):
    status: str
    job: JobData | None
    evaluations: list[EvaluationData]


@app.get("/health")
def health():
    return {"status": "ok"}


async def read_import(request: Request):
    payload = bytearray()
    async for chunk in request.stream():
        if len(payload) + len(chunk) > MAX_PGN_BYTES * 6 + 1024:
            fail(413, "payload_too_large", "Import request exceeds the size limit.")
        payload.extend(chunk)
    try:
        body = ImportBody.model_validate_json(payload)
        if len(body.pgn.encode()) > MAX_PGN_BYTES:
            fail(413, "pgn_too_large", "PGN exceeds 256 KiB.")
    except (ValidationError, UnicodeError):
        fail(422, "invalid_request", "Provide a JSON object with a PGN string.")
    return body.pgn


def serialize_owner_writes(db, owner):
    # A shared row lock serializes imports and quota checks for this owner.
    changed = db.execute(update(User).where(User.id == owner).values(id=User.id)).rowcount
    if not changed:
        fail(503, "demo_user_missing", "Apply database migrations before using the demo.")


@app.post(PREFIX + "/games/import/pgn", response_model=ImportResult, status_code=201,
          openapi_extra={"requestBody": {"required": True, "content": {
              "application/json": {"schema": ImportBody.model_json_schema()},
          }}})
def import_games(owner: UUID = Depends(actor), pgn: str = Depends(read_import)):
    try:
        parsed = parse_pgn(pgn)
    except InvalidPGN as error:
        fail(422, "invalid_pgn", str(error))
    game_ids = []
    imported = 0
    with Session.begin() as db:
        serialize_owner_writes(db, owner)
        for fields, moves in parsed:
            game = db.scalar(select(Game).where(Game.owner_id == owner,
                                                Game.canonical_hash == fields["canonical_hash"]))
            if game is None:
                game = Game(owner_id=owner, **fields)
                db.add(game)
                db.flush()
                db.add_all(Move(game_id=game.id, **move) for move in moves)
                imported += 1
            game_ids.append(game.id)
    return ImportResult(game_ids=game_ids, imported=imported, duplicates=len(parsed) - imported)


@app.get(PREFIX + "/games", response_model=GamesPage)
def list_games(owner: UUID = Depends(actor), offset: int = Query(0, ge=0),
               limit: int = Query(25, ge=1, le=100),
               result: Literal["1-0", "0-1", "1/2-1/2", "*"] | None = None):
    with Session() as db:
        query = select(Game).where(Game.owner_id == owner)
        if result is not None:
            query = query.where(Game.result == result)
        games = db.scalars(query.order_by(Game.created_at.desc(), Game.id.desc()).offset(offset).limit(limit + 1)).all()
        return GamesPage(games=games[:limit], next_offset=offset + limit if len(games) > limit else None)


@app.get(PREFIX + "/games/{game_id}", response_model=GameDetail)
def get_game(game_id: UUID, owner: UUID = Depends(actor)):
    with Session() as db:
        game = owned_game(db, owner, game_id)
        moves = db.scalars(select(Move).where(Move.game_id == game.id).order_by(Move.ply)).all()
        return GameDetail(**GameSummary.model_validate(game).model_dump(), pgn=game.pgn, moves=moves)


@app.post(PREFIX + "/games/{game_id}/analysis", response_model=JobData, status_code=202)
def request_analysis(game_id: UUID, body: AnalysisBody, background: BackgroundTasks, owner: UUID = Depends(actor),
                     idempotency_key: str | None = Header(None, max_length=72, min_length=1)):
    key = f"client:{idempotency_key}" if idempotency_key else f"depth:{body.depth}"
    with Session.begin() as db:
        serialize_owner_writes(db, owner)
        owned_game(db, owner, game_id)
        job = db.scalar(select(Job).where(Job.game_id == game_id, Job.requesting_user_id == owner,
                                          Job.idempotency_key == key))
        if job is not None and job.depth != body.depth:
            fail(409, "idempotency_conflict", "This idempotency key already uses a different depth.")
        if job is None:
            pending = db.scalar(select(func.count()).select_from(Job).where(
                Job.requesting_user_id == owner, Job.status.in_(("queued", "running")),
            ))
            if pending >= 5:
                fail(429, "analysis_quota", "Wait for an active analysis to finish.")
            job = Job(game_id=game_id, requesting_user_id=owner, depth=body.depth, idempotency_key=key)
            db.add(job)
            db.flush()
    if job.status == "queued":
        background.add_task(publish, job.id)
    return job


@app.get(PREFIX + "/analysis-jobs/{job_id}", response_model=JobData)
def get_job(job_id: UUID, owner: UUID = Depends(actor)):
    with Session() as db:
        job = db.scalar(select(Job).where(Job.id == job_id, Job.requesting_user_id == owner))
        if job is None:
            fail(404, "job_not_found", "Analysis job not found.")
        return job


@app.get(PREFIX + "/games/{game_id}/analysis", response_model=AnalysisResult)
def get_analysis(game_id: UUID, owner: UUID = Depends(actor), job_id: UUID | None = None):
    with Session() as db:
        owned_game(db, owner, game_id)
        query = select(Job).where(Job.game_id == game_id, Job.requesting_user_id == owner)
        if job_id is not None:
            query = query.where(Job.id == job_id)
        job = db.scalar(query.order_by(Job.created_at.desc(), Job.id.desc()).limit(1))
        if job_id is not None and job is None:
            fail(404, "job_not_found", "Analysis job not found.")
        evaluations = []
        if job and job.status == "completed":
            evaluations = db.scalars(select(Evaluation).where(
                Evaluation.analysis_job_id == job.id,
            ).order_by(Evaluation.ply)).all()
        return AnalysisResult(status=job.status if job else "not_requested", job=job, evaluations=evaluations)
