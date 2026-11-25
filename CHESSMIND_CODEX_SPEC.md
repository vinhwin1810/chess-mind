# ChessMind AI — Codex Master Implementation Specification

You are Codex, acting as a senior software engineer working INSIDE the current VS Code repository. Implement this product rather than merely describing it. Work incrementally, run tests, diagnose failures, and leave the repo in a runnable state.

## Absolute product requirements

- Product name: **ChessMind AI**. A production-oriented chess coaching web app and SWE/AI engineering portfolio project.
- **Chess.com ONLY** for online player/game integration. **Do not add Lichess integration anywhere.** Support manual PGN import as well.
- A user enters a Chess.com username, imports the user's public games, chooses controls such as rapid/blitz, requests engine analysis, reviews games and mistakes, receives grounded AI explanations, and gets a personalized training plan.
- The chess engine (Stockfish) supplies chess evaluations and principal variations; LLMs explain and synthesize evidence. Never fabricate engine moves, centipawn loss, evaluations or player metrics.
- Scope as a modular monolith plus separate asynchronous worker(s), not premature microservices.
- Build real code, migrations, tests, environment configuration, local developer scripts and useful documentation. No fake code or TODO-only implementations masquerading as done.

## Chess.com integration — mandatory

Official public API host: `https://api.chess.com`.
- `GET /pub/player/{username}` — public profile.
- `GET /pub/player/{username}/stats` — public stats.
- `GET /pub/player/{username}/games/archives` — monthly archive URL list.
- `GET /pub/player/{username}/games/{YYYY}/{MM}` — monthly game JSON containing `games`, often with `pgn`, `url`, `time_class`, `end_time`, players and metadata.
- `GET /pub/player/{username}/games/{YYYY}/{MM}/pgn` — monthly PGN export.
Docs: https://support.chess.com/en/articles/9650547-what-is-the-pubapi-and-how-do-i-use-it ; https://www.chess.com/news/view/published-data-api

Treat PubAPI as public/read-only; entering a username does NOT prove account ownership. Distinguish a linked/imported public Chess.com username from verified ownership. Don't invent Chess.com OAuth or private APIs. Handle 404, 429, 5xx, request timeouts, retries/backoff, monthly archive pagination/traversal, API throttling, absent fields and eventual archive freshness. Send a descriptive configurable User-Agent; do not scrape pages or circumvent access restrictions. Fetch newest archives first; deduplicate games using canonical Chess.com game URL (or stable composite fallback). Don't assume a direct single-game endpoint exists. Avoid SSRF: never fetch arbitrary URLs supplied by a user; construct API URLs from validated usernames/archive identifiers and verify trusted host. Respect official API terms and response caching policies. For imports, allow `limit`, `time_class`, and optional date range. Handle user playing either White or Black, result parsing, incomplete/aborted games, zero games, and imported PGN duplicates. Provide fixtures/mocks so tests run without live Chess.com requests.

## System architecture

- Frontend: Next.js + TypeScript + a maintained chessboard UI component, responsive design, game viewer with move navigation, engine evaluation graph, player analytics, coach chat and training plan UI.
- Backend: Python FastAPI + Pydantic + SQLAlchemy 2 async or equivalent, Alembic migrations, PostgreSQL 16+, pgvector.
- Chess domain: python-chess for PGN, SAN/UCI/FEN, replay and legality; Stockfish through UCI as CPU-heavy analysis.
- Jobs: Redis and Celery workers; API responds 202 with job ID for expensive analysis; job status endpoint with polling first (SSE optional later).
- AI: provider adapter with an optional API key; deterministic game reports work with no LLM configured. RAG embeddings and retrieval, agent tool calling, MCP server added only after a functioning core.
- Containers: Docker Compose for API, web, db, redis, worker; Stockfish installed in worker image; configurable resource limits/concurrency.
- Tests: pytest, FastAPI API integration tests, engine tests (skip gracefully or bundle engine in CI), frontend checks, GitHub Actions.

## Domain schema and data model

Use UUID primary keys for user-facing resources and timestamps with timezone. Model:
- users(id, email unique, display_name, created_at)
- chesscom_profiles(id, user_id, chesscom_username, connected_at, ownership_verified=false) — this is a public profile reference NOT an OAuth connection; if authentication isn't yet implemented, delay this table or make it safe for demo mode.
- games(id, owner_id, source='chesscom'|'upload', source_game_url nullable, external_id nullable, pgn, white_username, black_username, user_color nullable, time_class, time_control, result, played_at, created_at, canonical hash); sensible uniqueness and indexes, handle public games imported by multiple users without leaking one user's private analyses.
- moves(id, game_id, ply unique per game, uci, san, fen_before, fen_after)
- analysis_jobs(id, requesting_user_id, game_id, status, attempt_count, engine_version, depth, error_code, created_at, completed_at, idempotency_key)
- move_evaluations(id, game_id, ply, analysis_job_id, engine_version, depth, score_before, score_after, best_move_uci, pv_uci, cp_loss nullable, mate scores separately, position metadata)
- weakness_reports(id, user_id, filters, summary_json, evidence_json, created_at)
- training_plans(id, user_id, report_id, status, created_at)
- training_exercises(id, plan_id, fen, expected_moves, theme, source_game_id, source_ply, difficulty, completion state)
- knowledge_documents and knowledge_chunks(embedding vector, source metadata, visibility)
- resource_grants and audit_events for permission-aware sharing.

Start with a minimal subset in milestone 1 and add migrations when features are delivered. Constrain roles/actions in the backend, never trust LLM-provided actor IDs. Include proper foreign keys, indexes, uniqueness constraints and stable API response schemas.

## API surface (versioned `/api/v1`)

- `POST /games/import/pgn` — validate and import PGN, return game IDs/error details.
- `POST /integrations/chesscom/import` — import public Chess.com games for username, filters, limit; run asynchronously if import can be slow.
- `GET /integrations/chesscom/players/{username}` — public player summary with caching.
- `GET /games` — list games owned by current user with cursor/page filters.
- `GET /games/{game_id}` — game + moves.
- `POST /games/{game_id}/analysis` — 202 Accepted and idempotent analysis job; validate depth/config.
- `GET /analysis-jobs/{job_id}` — owner-scoped job status.
- `GET /games/{game_id}/analysis` — scored moves & principal variations or not-ready state.
- `GET /analytics/weaknesses` — aggregate actual player metrics (after milestone 2).
- `POST /coaching/reports` and `POST /coach/chat` — grounded coaching after milestone 3.
- `POST /training-plans`, `GET /training-plans/{id}` — authorized plans.
- Implement pagination, structured errors, strict input validation, request timeouts, sensible response codes, and OpenAPI docs.

## Chess engine semantics (very important)

- Parse PGN with python-chess. Reject malformed input gracefully, cap PGN payload size and game/move counts, store correct SAN and FEN before/after.
- For every player move, evaluate the same position BEFORE the move and the resulting position AFTER the move using comparable, documented settings. Use mover's POV consistently. Store principal variation and depth, distinguish centipawn score from mate score. `score()` can be None for mate positions; do NOT silently force a mate into a centipawn metric without explicit policy.
- Centipawn loss = max(0, best-before-score - played-after-score) for numeric centipawn scores from mover POV; clearly document caveats and engine variance. Better engine-based alternatives may be designed later but tests must cover the score sign.
- Never confuse the person whose games are being examined with the opponent. Track the user's color for per-player summaries. Filtering by rapid/blitz is based on Chess.com time_class metadata or documented fallback for PGN.
- Initial heuristics can mark inaccuracies/mistakes/blunders provisionally; label them as configurable heuristics, not as official Chess.com accuracy ratings. Unknown themes remain unclassified rather than hallucinated.
- Make Stockfish CPU/time/depth limits configurable; worker concurrency and engine instance lifecycle explicit; close engine on errors.
- Include regression fixtures for captures, promotions, castling, en passant, checkmate, mate score, result parsing, missing fields, deduplication, and black-side analysis.

## Workers and correctness

- API must not block while analyzing many games. Persist job status, enqueue work to Redis/Celery, and return job ID.
- Race-safe job claiming/idempotency; retries only for transient errors; stable storage transaction for results and completion. Correct handling of crashed workers / stale `running` jobs.
- Cache/reuse equivalent analyses per engine version + analysis settings when safe. Avoid infinite expensive work and enforce per-user quotas.
- Do not use arbitrary Python exceptions as public API error details.

## AI architecture, after deterministic core works

- Orchestrator with read-only tools: `get_player_games`, `get_game_analysis`, `get_weakness_report`, `analyze_position`, `search_chess_knowledge`; state-changing tools: `create_training_plan`, `save_training_plan` and shared-resource management. Each tool enforces permission checks outside the LLM. Log tool calls and outcomes without secrets.
- MCP server implemented using an up-to-date Python MCP SDK; it should wrap the existing domain layer rather than duplicate business logic. `analyze_position` must validate a legal FEN and bounded depth. Authenticated actor context comes from server identity, not free-form tool arguments.
- Skills are scoped task workflows, not just buzzwords: `opening_review`, `tactical_diagnosis`, `endgame_coach`, `weekly_training_planner`; define input/output contracts and tests.
- RAG: ingest only licensed/owned chess materials, chunk with metadata, embed, index via pgvector, filter by authorization before model context, retrieve/rerank, cite relevant source chunks. Use factual engine/game evidence alongside retrieved chess lessons. Persist evidence IDs, FEN, ply and sources so reports can be audited.
- Prevent prompt injection from PGN headers and retrieved content; treat them as untrusted data. Require human approval for sensitive changes/sharing. No imaginary account ownership or privileged access.
- AI disabled or no key should not break PGN/Stockfish/analytics workflows.

## UI requirements

- Clean responsive dashboard. Username search/import with clear public-data notice; upload PGN; list/filter games; select game; chessboard move navigation and evaluations; comparison of player mistakes by phase; coach chat with game citations; training plan page.
- Basic accessibility, loading/error/empty states, polling background jobs without freezing the UI.
- Don't claim metrics are real unless computed from analysis results. Use explicitly labeled mock/demo data where needed.

## Milestones — implement sequentially and run checks

**M1 — Runnable chess analysis MVP (implement FIRST, do not skip):**
1. Inspect the working directory/repo; preserve existing files and conventions. If repository empty, initialize a monorepo `apps/api`, `apps/web`, `services/mcp-server`, `infrastructure`, `docs`.
2. Docker Compose for Postgres, Redis, backend, engine worker; optionally frontend. Sample `.env.example`, no hardcoded credentials or committed secrets.
3. FastAPI health endpoint, SQLAlchemy models, Alembic migration for users, games, moves, jobs/evaluations; safe development auth/mock identity clearly separate from production authentication.
4. PGN import, `python-chess` parsing, Stockfish analysis pipeline and worker, job creation/status/results API, integration tests using PGN fixtures.
5. Basic frontend importing PGN, listing games, stepping through moves on chessboard and displaying engine analysis.
6. `README.md` with prerequisites, `docker compose up --build`, migrations, tests, demo walkthrough and known limitations.
7. Execute tests and report precise results, failures and commands. Do not proceed to the next milestone if M1 does not run unless blocked by unavailable dependencies; explain blockers.

**M2 — Chess.com + analytics:** Public Chess.com API client (no Lichess), newest-first monthly archives, import filters/dedup/rate handling, game metadata, player-side aggregates, charts and dashboard.

**M3 — AI coaching:** RAG with licensed material, coaching report grounded in engine data, MCP tools, agent skills, optional LLM provider, citations, eval/test set.

**M4 — Production hardening:** Real auth, RBAC + resource grants, ownership, audit logs, rate limiting, monitoring, performance tests, security tests and deployment docs.

## Definition of done

A developer can start with documented commands, import a PGN, analyze it in an async worker, inspect moves and evaluations in the UI, and (after M2) enter a Chess.com username to import public games. M3 adds evidence-grounded coaching. M4 adds permission-safe sharing and robust operations. Every implemented milestone is accompanied by migrations, tests and documentation; distinguish implemented vs planned features clearly.

## Your immediate action

1. Check `pwd`, repo status and installed tooling. Don't overwrite user files or discard uncommitted changes.
2. Present a compact plan and the first concrete file changes.
3. Start coding **Milestone 1** now. Do not merely create a plan, diagrams or a scaffold with TODO placeholders. Actually implement a working vertical slice and run tests.
4. At the end state what you created, which commands you ran, pass/fail test outputs, any environment limitations, and the exact next step.
