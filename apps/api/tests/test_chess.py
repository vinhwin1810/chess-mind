import os
import shutil
from pathlib import Path
from types import SimpleNamespace

import chess
import chess.engine
import pytest

from chessmind.engine import evaluate, score_fields
from chessmind.pgn import InvalidPGN, parse_pgn

FIXTURES = Path(__file__).parent / "fixtures"


def test_special_moves_and_missing_headers():
    (game, moves), (_, promotion) = parse_pgn((FIXTURES / "special-moves.pgn").read_text())
    assert game["white_username"] is None and game["played_at"] is None
    assert game["result"] == "*"
    assert moves[4]["uci"] == "e5d6"
    assert chess.Board(moves[4]["fen_after"]).piece_at(chess.D5) is None
    assert [move["uci"] for move in moves[-2:]] == ["e1g1", "e8g8"]
    assert promotion[0]["san"] == "axb8=Q+"
    assert promotion[0]["uci"] == "a7b8q"


@pytest.mark.parametrize("pgn", [
    "", "1. e5 *", "1. e4 garbage *", "1. e4 (1. d4 *", "1. e4 0-1 e5",
    '[Result "0-1"]\n\n1. e4 1-0', "1. f3 e5 2. g4 Qh4# 1-0",
    '[SetUp "1"]\n[FEN "not a fen"]\n\n1. e4 *',
])
def test_invalid_pgn(pgn):
    with pytest.raises(InvalidPGN):
        parse_pgn(pgn)


def test_dedup_ignores_comments_and_variations():
    def digest(pgn):
        return parse_pgn(pgn)[0][0]["canonical_hash"]

    assert digest("1. e4 {A comment} (1. d4) e5 *") == digest("1.e4 e5 *")


def test_black_centipawn_loss_and_mate():
    before = chess.engine.PovScore(chess.engine.Cp(-80), chess.WHITE)
    after = chess.engine.PovScore(chess.engine.Cp(+30), chess.WHITE)
    assert score_fields(before, after, chess.BLACK)["cp_loss"] == 110
    assert score_fields(before, after, chess.WHITE)["cp_loss"] == 0
    after = chess.engine.PovScore(chess.engine.Mate(0), chess.WHITE)
    row = score_fields(before, after, chess.BLACK)
    assert row["cp_loss"] is None and row["mate_after"] == 0


def test_real_stockfish_analysis():
    path = os.getenv("STOCKFISH_PATH") or shutil.which("stockfish")
    if not path:
        pytest.skip("Stockfish executable unavailable")
    _, moves = parse_pgn((FIXTURES / "fools-mate.pgn").read_text())[0]
    with chess.engine.SimpleEngine.popen_uci(path, timeout=5) as engine:
        rows = list(evaluate(engine, [SimpleNamespace(**move) for move in moves], depth=4, seconds=0.2))
    assert len(rows) == 4
    assert rows[0]["pv_uci"]
    assert rows[-1]["position_metadata"]["mover"] == "black"
    assert rows[-1]["mate_after"] == 0 and rows[-1]["cp_loss"] is None
