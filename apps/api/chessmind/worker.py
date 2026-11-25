import logging
import os
from datetime import timedelta
from uuid import UUID, uuid4

import chess
import chess.engine
from celery import Celery
from sqlalchemy import and_, or_, select, update

from .db import Evaluation, Job, Move, Session, utcnow
from .engine import evaluate

logger = logging.getLogger(__name__)
broker_url = os.getenv("REDIS_URL", "redis://localhost:6379/0")
celery = Celery("chessmind", broker=broker_url)
celery.conf.update(
    broker_connection_retry_on_startup=True,
    broker_connection_timeout=2,
    task_publish_retry=False,
    task_ignore_result=True,
    worker_prefetch_multiplier=1,
    task_time_limit=900,
    beat_schedule={"recover-analysis": {"task": "chessmind.dispatch_pending", "schedule": 30}},
)
MAX_ATTEMPTS = 3
LEASE = timedelta(minutes=16)
DISPATCH_INTERVAL = timedelta(minutes=2)


def runnable(now):
    return or_(Job.status == "queued", and_(Job.status == "running", Job.lease_until <= now))


def due_for_dispatch(now):
    return and_(runnable(now), Job.attempt_count < MAX_ATTEMPTS,
                or_(Job.next_dispatch_at.is_(None), Job.next_dispatch_at <= now))


def publish(job_id):
    now = utcnow()
    reservation = now + DISPATCH_INTERVAL
    with Session.begin() as db:
        reserved = db.execute(update(Job).where(Job.id == job_id, due_for_dispatch(now))
                              .values(next_dispatch_at=reservation)).rowcount
    if not reserved:
        return
    try:
        analyze_job.delay(str(job_id))
    except Exception:
        logger.warning("Analysis publish failed for job %s; periodic dispatch will retry", job_id, exc_info=True)
        with Session.begin() as db:
            db.execute(update(Job).where(Job.id == job_id, Job.next_dispatch_at == reservation)
                       .values(next_dispatch_at=None))


@celery.task(name="chessmind.dispatch_pending")
def dispatch_pending():
    now = utcnow()
    with Session.begin() as db:
        db.execute(update(Job).where(runnable(now), Job.attempt_count >= MAX_ATTEMPTS).values(
            status="failed", error_code="attempts_exhausted", completed_at=now, claim_token=None, lease_until=None,
        ))
        job_ids = db.scalars(select(Job.id).where(due_for_dispatch(now)).order_by(Job.created_at).limit(100)).all()
    for job_id in job_ids:
        publish(job_id)


def finish(job_id, token, values, evaluations=()):
    with Session.begin() as db:
        changed = db.execute(update(Job).where(Job.id == job_id, Job.claim_token == token).values(
            claim_token=None, lease_until=None, **values,
        )).rowcount
        if changed:
            db.add_all(evaluations)


@celery.task(name="chessmind.analyze_job")
def analyze_job(raw_id):
    job_id, token = UUID(raw_id), uuid4()
    now = utcnow()
    with Session.begin() as db:
        claimed = db.execute(update(Job).where(
            Job.id == job_id, runnable(now), Job.attempt_count < MAX_ATTEMPTS,
        ).values(status="running", attempt_count=Job.attempt_count + 1, claim_token=token, next_dispatch_at=None,
                 lease_until=now + LEASE)).rowcount
        if not claimed:
            return
        job = db.get(Job, job_id)
        moves = db.scalars(select(Move).where(Move.game_id == job.game_id).order_by(Move.ply)).all()

    engine = None
    evaluations = []
    try:
        seconds = float(os.getenv("ENGINE_SECONDS", "0.2"))
        if not 0.01 <= seconds <= 1:
            raise ValueError("ENGINE_SECONDS must be between 0.01 and 1")
        engine = chess.engine.SimpleEngine.popen_uci(os.getenv("STOCKFISH_PATH", "stockfish"), timeout=5)
        engine.configure({"Threads": 1, "Hash": int(os.getenv("ENGINE_HASH_MB", "32"))})
        version = engine.id["name"]
        evaluations = [Evaluation(game_id=job.game_id, analysis_job_id=job_id, engine_version=version, **row)
                       for row in evaluate(engine, moves, job.depth, seconds)]
        values = dict(status="completed", engine_version=version, completed_at=utcnow(), error_code=None)
    except (chess.engine.EngineTerminatedError, TimeoutError):
        logger.exception("Engine interrupted for job %s", job_id)
        retry = job.attempt_count < MAX_ATTEMPTS
        values = dict(status="queued" if retry else "failed", error_code="engine_interrupted",
                      completed_at=None if retry else utcnow())
    except Exception:
        logger.exception("Analysis failed for job %s", job_id)
        values = dict(status="failed", error_code="engine_error", completed_at=utcnow())
    finally:
        if engine is not None:
            try:
                engine.quit()
            except (chess.engine.EngineError, TimeoutError):
                engine.close()
    finish(job_id, token, values, evaluations)
