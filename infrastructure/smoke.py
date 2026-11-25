"""Verify a running local Compose stack with real Redis and Stockfish."""

import json
import os
import time
from pathlib import Path
from urllib.error import URLError
from urllib.request import Request, urlopen

BASE = os.environ.get("CHESSMIND_API_URL", "http://localhost:8000")
FIXTURE = Path(__file__).resolve().parents[1] / "apps/api/tests/fixtures/fools-mate.pgn"


def request(path, data=None):
    body = json.dumps(data).encode() if data is not None else None
    req = Request(BASE + path, data=body, headers={"Content-Type": "application/json"})
    with urlopen(req, timeout=5) as response:
        return json.load(response)


def main():
    for _ in range(30):
        try:
            if request("/health")["status"] == "ok":
                break
        except URLError:
            time.sleep(1)
    else:
        raise SystemExit("API health endpoint did not become ready.")

    imported = request("/api/v1/games/import/pgn", {"pgn": FIXTURE.read_text()})
    game = f"/api/v1/games/{imported['game_ids'][0]}"
    assert request(game)["moves"][-1]["san"] == "Qh4#"

    job = request(game + "/analysis", {"depth": 4})
    for _ in range(90):
        job = request(f"/api/v1/analysis-jobs/{job['id']}")
        if job["status"] == "failed":
            raise SystemExit(f"Analysis failed: {job['error_code']}")
        if job["status"] == "completed":
            break
        time.sleep(1)
    else:
        raise SystemExit("Analysis did not complete in 90 seconds.")

    report = request(f"{game}/analysis?job_id={job['id']}")
    assert len(report["evaluations"]) == 4
    assert report["evaluations"][0]["pv_uci"]
    final = report["evaluations"][-1]
    assert final["mate_after"] == 0 and final["cp_loss"] is None
    assert final["position_metadata"]["mover"] == "black"
    print(f"Smoke test passed using {job['engine_version']}")


if __name__ == "__main__":
    main()
