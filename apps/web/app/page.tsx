"use client";

import dynamic from "next/dynamic";
import { type FormEvent, useEffect, useState } from "react";

const Chessboard = dynamic(() => import("react-chessboard").then((mod) => mod.Chessboard), {
  ssr: false,
  loading: () => <p role="status">Loading board…</p>,
});

type Game = { id: string; white_username: string | null; black_username: string | null; result: string };
type Move = { ply: number; san: string; uci: string; fen_before: string; fen_after: string };
type GameDetail = Game & { moves: Move[] };
type Job = {
  id: string; depth: number; engine_version: string | null; error_code: string | null;
  status: "queued" | "running" | "completed" | "failed";
};
type Evaluation = {
  ply: number; score_before: number | null; score_after: number | null;
  mate_before: number | null; mate_after: number | null; cp_loss: number | null;
  best_move_uci: string | null; pv_uci: string[];
  position_metadata: { mover: string; before_depth: number | null; after_depth: number | null };
};
type Analysis = { status: Job["status"] | "not_requested"; job: Job | null; evaluations: Evaluation[] };
type GamesPage = { games: Game[]; next_offset: number | null };
type ImportResult = { game_ids: string[]; imported: number; duplicates: number };

const base = (process.env.NEXT_PUBLIC_API_URL ?? "http://localhost:8000") + "/api/v1";
const pgnLimit = 256 * 1024;

async function api<T>(path: string, init?: RequestInit): Promise<T> {
  const response = await fetch(base + path, init);
  if (!response.ok) {
    const problem = await response.json().catch(() => null);
    throw new Error(problem?.detail?.message ?? `Request failed (${response.status}).`);
  }
  return response.json();
}

function errorText(error: unknown) {
  return error instanceof Error ? error.message : "The request failed.";
}

function gameTitle(game: Game) {
  return `${game.white_username ?? "White"} vs ${game.black_username ?? "Black"}`;
}

function formatScore(cp: number | null, mate: number | null, after = false) {
  if (mate === 0) return after ? "Checkmate delivered" : "Checkmate";
  if (mate !== null) return mate > 0 ? `Mate in ${mate}` : `Mated in ${-mate}`;
  return cp === null ? "Unavailable" : `${cp > 0 ? "+" : ""}${(cp / 100).toFixed(2)}`;
}

export default function Dashboard() {
  const [games, setGames] = useState<Game[]>([]);
  const [nextOffset, setNextOffset] = useState<number | null>(null);
  const [selection, setSelection] = useState("");
  const [listUpdated, setListUpdated] = useState(0);
  const [listLoading, setListLoading] = useState(true);
  const [listError, setListError] = useState("");
  const [pgn, setPgn] = useState("");
  const [importing, setImporting] = useState(false);
  const [importError, setImportError] = useState("");
  const [importNotice, setImportNotice] = useState("");

  useEffect(() => {
    const controller = new AbortController();
    api<GamesPage>("/games", { signal: controller.signal }).then((page) => {
      setGames(page.games);
      setNextOffset(page.next_offset);
      setSelection((current) => current || page.games[0]?.id || "");
    }).catch((error) => {
      if (!controller.signal.aborted) setListError(errorText(error));
    }).finally(() => {
      if (!controller.signal.aborted) setListLoading(false);
    });
    return () => controller.abort();
  }, [listUpdated]);

  async function moreGames() {
    if (nextOffset === null) return;
    setListLoading(true);
    setListError("");
    try {
      const page = await api<GamesPage>(`/games?offset=${nextOffset}`);
      setGames((current) => [...new Map([...current, ...page.games].map((game) => [game.id, game])).values()]);
      setNextOffset(page.next_offset);
    } catch (error) {
      setListError(errorText(error));
    } finally {
      setListLoading(false);
    }
  }

  async function importGames(event: FormEvent) {
    event.preventDefault();
    setImportError("");
    setImportNotice("");
    if (new Blob([pgn]).size > pgnLimit) {
      setImportError("PGN exceeds 256 KiB.");
      return;
    }
    setImporting(true);
    try {
      const result = await api<ImportResult>("/games/import/pgn", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ pgn }),
      });
      setImportNotice(`Imported ${result.imported}. Duplicates ${result.duplicates}.`);
      setSelection(result.game_ids[0] ?? "");
      setPgn("");
      setListError("");
      setListLoading(true);
      setListUpdated((value) => value + 1);
    } catch (error) {
      setImportError(errorText(error));
    } finally {
      setImporting(false);
    }
  }

  return (
    <main className="workspace">
      <header className="site-header">
        <h1>ChessMind AI</h1>
        <span>Local demo</span>
      </header>
      <div className="dashboard">
        <aside className="sidebar">
          <section className="panel">
            <h2>Import a game</h2>
            <form onSubmit={importGames}>
              <label htmlFor="pgn">Paste PGN</label>
              <textarea id="pgn" rows={5} value={pgn} required
                onChange={(event) => setPgn(event.target.value)} />
              <label htmlFor="file">Or choose a PGN file</label>
              <input id="file" type="file" accept=".pgn,text/plain" onChange={async (event) => {
                const file = event.target.files?.[0];
                event.target.value = "";
                if (!file) return;
                setImportError("");
                setImportNotice("");
                if (file.size > pgnLimit) {
                  setImportError("PGN exceeds 256 KiB.");
                  return;
                }
                try {
                  setPgn(await file.text());
                } catch {
                  setImportError("Could not read the selected file.");
                }
              }} />
              <button className="primary" disabled={importing || listLoading || !pgn.trim()}>
                {importing ? "Importing…" : "Import PGN"}
              </button>
            </form>
            {importError && <p role="alert">{importError}</p>}
            {importNotice && <p role="status">{importNotice}</p>}
          </section>
          <section className="panel">
            <h2>Games</h2>
            {listLoading && <p role="status">Loading games…</p>}
            {listError && <p role="alert">{listError}</p>}
            {!listLoading && !listError && games.length === 0 && <p>Your imported games will appear here.</p>}
            <ul className="games">
              {games.map((game) => <li key={game.id}>
                <button type="button" aria-pressed={selection === game.id} onClick={() => setSelection(game.id)}>
                  <strong>{gameTitle(game)}</strong><span>{game.result}</span>
                </button>
              </li>)}
            </ul>
            {nextOffset !== null && <button disabled={listLoading || importing} onClick={moreGames}>Load more</button>}
          </section>
        </aside>
        {selection ? <GameViewer key={selection} id={selection} /> : (
          <section className="panel placeholder"><h2>Game review</h2><p>Import or select a game to review its moves.</p></section>
        )}
      </div>
    </main>
  );
}

function GameViewer({ id }: { id: string }) {
  const [game, setGame] = useState<GameDetail | null>(null);
  const [analysis, setAnalysis] = useState<Analysis | null>(null);
  const [ply, setPly] = useState(0);
  const [depth, setDepth] = useState(12);
  const [submitting, setSubmitting] = useState(false);
  const [gameError, setGameError] = useState("");
  const [analysisError, setAnalysisError] = useState("");
  const [refresh, setRefresh] = useState(0);

  useEffect(() => {
    const controller = new AbortController();
    api<GameDetail>(`/games/${id}`, { signal: controller.signal }).then(setGame).catch((error) => {
      if (!controller.signal.aborted) setGameError(errorText(error));
    });
    return () => controller.abort();
  }, [id]);

  useEffect(() => {
    const controller = new AbortController();
    api<Analysis>(`/games/${id}/analysis`, { signal: controller.signal }).then(setAnalysis).catch((error) => {
      if (!controller.signal.aborted) setAnalysisError(errorText(error));
    });
    return () => controller.abort();
  }, [id, refresh]);

  const activeJob = analysis?.status === "queued" || analysis?.status === "running" ? analysis.job?.id : undefined;
  useEffect(() => {
    if (!activeJob) return;
    const controller = new AbortController();
    let timer: ReturnType<typeof setTimeout>;
    async function poll() {
      try {
        const job = await api<Job>(`/analysis-jobs/${activeJob}`, { signal: controller.signal });
        if (job.status === "completed" || job.status === "failed") {
          const report = await api<Analysis>(`/games/${id}/analysis?job_id=${job.id}`, { signal: controller.signal });
          setAnalysis(report);
          setAnalysisError("");
          return;
        }
        setAnalysis({ status: job.status, job, evaluations: [] });
        setAnalysisError("");
      } catch (error) {
        if (!controller.signal.aborted) setAnalysisError(errorText(error));
      }
      if (!controller.signal.aborted) timer = setTimeout(poll, 2000);
    }
    timer = setTimeout(poll, 1000);
    return () => {
      controller.abort();
      clearTimeout(timer);
    };
  }, [activeJob, id]);

  async function analyze() {
    setAnalysisError("");
    setSubmitting(true);
    try {
      const headers: Record<string, string> = { "Content-Type": "application/json" };
      if (analysis?.status === "failed") headers["Idempotency-Key"] = crypto.randomUUID();
      const job = await api<Job>(`/games/${id}/analysis`, {
        method: "POST", headers, body: JSON.stringify({ depth }),
      });
      if (job.status === "completed") {
        setAnalysis(await api<Analysis>(`/games/${id}/analysis?job_id=${job.id}`));
      } else {
        setAnalysis({ status: job.status, job, evaluations: [] });
      }
    } catch (error) {
      setAnalysisError(errorText(error));
    } finally {
      setSubmitting(false);
    }
  }

  if (!game) return <section className="panel"><p role={gameError ? "alert" : "status"}>{gameError || "Loading game…"}</p></section>;
  const move = game.moves[ply - 1];
  const score = analysis?.evaluations.find((evaluation) => evaluation.ply === ply);

  return (
    <section className="panel viewer">
      <header className="game-header"><h2>{gameTitle(game)}</h2><span>{game.result}</span></header>
      <div className="review">
        <div>
          <div className="board" aria-label={move ? `Position after ${move.san}` : "Starting position"}>
            <Chessboard position={move?.fen_after ?? game.moves[0].fen_before} arePiecesDraggable={false}
              customLightSquareStyle={{ backgroundColor: "#e7e8d8" }}
              customDarkSquareStyle={{ backgroundColor: "#74856d" }} />
          </div>
          <nav className="controls" aria-label="Move navigation">
            <button disabled={ply === 0} onClick={() => setPly(0)}>Start</button>
            <button disabled={ply === 0} onClick={() => setPly(ply - 1)}>Previous</button>
            <button disabled={ply === game.moves.length} onClick={() => setPly(ply + 1)}>Next</button>
            <button disabled={ply === game.moves.length} onClick={() => setPly(game.moves.length)}>End</button>
          </nav>
          <p className="position" aria-live="polite">Ply {ply} of {game.moves.length}{move ? ` · ${move.san}` : " · Starting position"}</p>
        </div>
        <div className="review-details">
          <h3>Moves</h3>
          <ol className="moves">
            {game.moves.map((item) => <li key={item.ply}>
              <button aria-current={item.ply === ply ? "step" : undefined} onClick={() => setPly(item.ply)}
                aria-label={`Ply ${item.ply}, ${item.san}`}>{item.san}</button>
            </li>)}
          </ol>
          <h3>Stockfish analysis</h3>
          <form className="analysis-form" onSubmit={(event) => { event.preventDefault(); void analyze(); }}>
            <label htmlFor="depth">Depth</label>
            <input id="depth" type="number" value={depth || ""} required min={4} max={18}
              onChange={(event) => setDepth(event.target.valueAsNumber)} />
            <button className="primary" disabled={submitting || Boolean(activeJob) || !analysis}>
              {analysis?.status === "failed" ? "Retry analysis" : "Analyze"}
            </button>
          </form>
          {analysisError && <p role="alert">{analysisError}</p>}
          {!analysis && analysisError && <button onClick={() => { setAnalysisError(""); setRefresh(refresh + 1); }}>Reload analysis</button>}
          {!analysis && !analysisError && <p role="status">Loading analysis…</p>}
          {analysis?.job && <p role="status">
            {analysis.status === "completed"
              ? `${analysis.job.engine_version} · depth cap ${analysis.job.depth}`
              : `Analysis ${analysis.status}${analysis.job.error_code ? ` (${analysis.job.error_code})` : ""}`}
          </p>}
          {analysis?.status === "completed" && !score && <p>Select a move to see its evaluation.</p>}
          {score && (
            <>
              <p>Scores from {score.position_metadata.mover}’s perspective.</p>
              <dl className="scores">
                <dt>Before</dt><dd>{formatScore(score.score_before, score.mate_before)}</dd>
                <dt>After</dt><dd>{formatScore(score.score_after, score.mate_after, true)}</dd>
                <dt>Centipawn loss</dt><dd>{score.cp_loss ?? "Unavailable for mate scores"}</dd>
                <dt>Best move</dt><dd>{score.best_move_uci ?? "Unavailable"}</dd>
                <dt>Principal variation</dt><dd>{score.pv_uci.join(" ") || "Unavailable"}</dd>
              </dl>
            </>
          )}
        </div>
      </div>
    </section>
  );
}
