from datetime import timedelta
from uuid import UUID, uuid4

import chess
import chess.engine
import pytest
from alembic import command
from alembic.config import Config
from fastapi.testclient import TestClient

from chessmind import db, main, worker
from chessmind.pgn import MAX_PGN_BYTES
from test_chess import FIXTURES

PREFIX = "/api/v1"
PGN = (FIXTURES / "fools-mate.pgn").read_text()


class DeterministicEngine:
    id = {"name": "test-engine"}

    def configure(self, options):
        assert options["Threads"] == 1

    def analyse(self, board, limit):
        return {"score": chess.engine.PovScore(chess.engine.Cp(10), board.turn),
                "pv": [next(iter(board.legal_moves))], "depth": limit.depth}

    def quit(self):
        pass


@pytest.fixture
def client(tmp_path, monkeypatch):
    engine = db.create_engine(f"sqlite:///{tmp_path / 'test.db'}")
    monkeypatch.setattr(db, "engine", engine)
    session = db.sessionmaker(engine, expire_on_commit=False)
    monkeypatch.setattr(main, "Session", session)
    monkeypatch.setattr(worker, "Session", session)
    monkeypatch.setenv("CHESSMIND_DEMO_MODE", "true")
    monkeypatch.setattr(main, "publish", lambda job_id: None)
    command.upgrade(Config("alembic.ini"), "head")
    with TestClient(main.app) as client:
        yield client
    engine.dispose()


def import_game(client, pgn=PGN):
    response = client.post(PREFIX + "/games/import/pgn", json={"pgn": pgn})
    assert response.status_code == 201, response.text
    return response.json()


def test_api_import_dedup_and_ownership(client, monkeypatch):
    assert client.get("/health").json() == {"status": "ok"}
    imported = import_game(client)
    duplicate = import_game(client)
    game_id = imported["game_ids"][0]
    assert imported["imported"] == 1
    assert duplicate == {"imported": 0, "duplicates": 1, "game_ids": [game_id]}
    response = client.get(PREFIX + "/games?limit=1").json()
    assert response["next_offset"] is None
    assert [game["id"] for game in response["games"]] == [game_id]
    detail = client.get(f"{PREFIX}/games/{game_id}").json()
    assert detail["moves"][-1]["san"] == "Qh4#"
    owner = uuid4()
    monkeypatch.setitem(main.app.dependency_overrides, main.actor, lambda: owner)
    assert client.get(f"{PREFIX}/games/{game_id}").status_code == 404
    assert client.get(PREFIX + "/games").json()["games"] == []


def test_bad_and_oversized_pgn_and_disabled_identity(client, monkeypatch):
    url = PREFIX + "/games/import/pgn"
    assert client.post(url, json={"pgn": "1. e5"}).json()["detail"]["code"] == "invalid_pgn"
    assert client.post(url, json={"pgn": "a" * (MAX_PGN_BYTES + 1)}).status_code == 413
    assert client.post(url, json={"pgn": 1}).status_code == 422
    monkeypatch.delenv("CHESSMIND_DEMO_MODE")
    assert client.get(PREFIX + "/games").status_code == 503


def test_analysis_lifecycle_idempotency_and_stale_claim(client, monkeypatch):
    monkeypatch.setattr(chess.engine.SimpleEngine, "popen_uci", lambda *args, **kwargs: DeterministicEngine())
    game_id = import_game(client)["game_ids"][0]
    url = f"{PREFIX}/games/{game_id}/analysis"
    assert client.get(url).json()["status"] == "not_requested"
    headers = {"Idempotency-Key": "review"}
    accepted = client.post(url, json={"depth": 4}, headers=headers)
    assert accepted.status_code == 202
    job_id = accepted.json()["id"]
    status_url = f"{PREFIX}/analysis-jobs/{job_id}"
    assert client.get(status_url).json()["status"] == "queued"
    assert client.post(url, json={"depth": 4}, headers=headers).json()["id"] == job_id
    assert client.post(url, json={"depth": 5}, headers=headers).status_code == 409
    stale_token = uuid4()
    with worker.Session.begin() as session:
        job = session.get(db.Job, UUID(job_id))
        job.status, job.claim_token, job.attempt_count = "running", stale_token, 1
        job.lease_until = db.utcnow() - timedelta(minutes=1)
    worker.analyze_job.run(job_id)
    worker.analyze_job.run(job_id)
    worker.finish(UUID(job_id), stale_token, {"status": "failed"})
    assert client.get(status_url).json()["attempt_count"] == 2
    report = client.get(url).json()
    assert report["status"] == "completed"
    assert len(report["evaluations"]) == 4
    assert report["evaluations"][-1]["mate_after"] == 0
    owner = uuid4()
    monkeypatch.setitem(main.app.dependency_overrides, main.actor, lambda: owner)
    assert client.get(status_url).status_code == 404
