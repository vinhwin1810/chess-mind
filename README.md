# ChessMind AI

Milestone 1 implements local PGN import, game replay, and asynchronous Stockfish analysis. The only planned online game source is Chess.com, which is scheduled for Milestone 2.

## Start the demo

Install Docker with Compose v2 and start its daemon. The first build downloads container images and dependencies. Run these commands from the project root.

```sh
cp .env.example .env
openssl rand -hex 24
```

Paste the generated hex value into `POSTGRES_PASSWORD` in `.env`. The sample enables the single local demo identity. The API and web ports are bound to loopback.

```sh
docker compose up --build
```

Open [ChessMind AI](http://localhost:3000) and choose [the demo PGN](apps/api/tests/fixtures/fools-mate.pgn), or paste your own. Select **Import PGN**, open the game, then select **Analyze**. Use **Next** or the move list to see the position and analysis for each ply. On the last move of the demo, Black delivers checkmate and the centipawn loss is unavailable because one of the scores is a mate score.

The API has [OpenAPI docs](http://localhost:8000/docs) and a [health endpoint](http://localhost:8000/health). Use `docker compose logs -f api worker beat` to inspect analysis jobs. Use `docker compose down` to stop the stack while retaining imported games in the database volume.

## Database and worker

The Compose `migrate` service applies Alembic revisions to PostgreSQL 16 before starting the API or workers. Run `docker compose run --rm migrate` to apply migrations again, and `docker compose run --rm migrate alembic current` to inspect the current revision. The first migration creates the demo user. With `CHESSMIND_DEMO_MODE` disabled, game and analysis routes return 503 until real authentication is implemented.

The API persists jobs before returning HTTP 202. Celery workers claim jobs atomically. Beat periodically dispatches queued jobs and recovers expired claims. Retries are bounded and the worker commits results and completion together. A demo user can have up to five queued or running jobs. Requests without an `Idempotency-Key` reuse the same job for a game and depth. Use a new explicit key to reanalyze after changing engine settings or retry a failed job. Results record the engine and actual search depth where available.

Stockfish uses one thread per job. `ENGINE_SECONDS` sets a 0.01–1 second cap per position, and API requests accept depth limits of 4–18. Configure worker concurrency, memory limit, CPU limit and engine hash through `WORKER_CONCURRENCY`, `WORKER_MEMORY`, `WORKER_CPUS` and `ENGINE_HASH_MB`. The default worker uses one CPU, 512 MiB of container memory, one process, and 32 MiB of engine hash.

## Tests and development

For backend tests, install Python 3.11 or 3.12. Install Stockfish separately for the real-engine test, or set `STOCKFISH_PATH` to its executable. That test skips when no executable is available. API integration tests migrate an isolated SQLite database and use a deterministic engine double.

```sh
python3 -m venv .venv
source .venv/bin/activate
cd apps/api
python -m pip install -r requirements-dev.txt
python -m pytest -q
```

With Node.js 22 or newer, run the web checks from `apps/web`.

```sh
npm install
npm run typecheck
npm run build
```

With the Docker stack running, verify PGN import through the API and analysis through real Redis, PostgreSQL, and Stockfish from the project root.

```sh
python3 infrastructure/smoke.py
```

For local backend development, from `apps/api` set `CHESSMIND_DEMO_MODE=true`, set `DATABASE_URL` for PostgreSQL or use the default local SQLite database, and run `alembic upgrade head` followed by `uvicorn chessmind.main:app --reload`. Run Redis, a Celery worker, and a beat process separately with the same database settings. Use `celery -A chessmind.worker:celery worker --concurrency=1` and `celery -A chessmind.worker:celery beat --schedule=/tmp/chessmind-beat`. Set `REDIS_URL` if Redis is listening somewhere other than `localhost:6379`. Run `npm run dev` from `apps/web` for the frontend.

## Analysis and limits

- PGN imports accept standard chess, with a 256 KiB UTF-8 limit, up to 20 games per request, and up to 300 plies per game including variations. The viewer shows the main line. The duplicate key uses starting FEN, mainline moves, players, date, and result.
- Both evaluations use the mover’s perspective. Centipawn loss is `max(0, before − after)` when both scores are numeric. Mate scores are stored separately, and terminal positions use the board’s exact result. Depth and time caps, position complexity, and engine search state can cause variation. The UI displays pawn units for evaluations and centipawns for loss.
- This is a shared local demo. PGN imports have no account ownership or inferred player side. Public Chess.com imports and player analytics belong to Milestone 2. AI coaching and training plans are later milestones.
