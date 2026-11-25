import chess
import chess.engine


def score_fields(before, after, color):
    before, after = before.pov(color), after.pov(color)
    cp_before, cp_after = before.score(), after.score()
    cp_loss = None if cp_before is None or cp_after is None else max(0, cp_before - cp_after)
    return dict(score_before=cp_before, score_after=cp_after, mate_before=before.mate(),
                mate_after=after.mate(), cp_loss=cp_loss)


def evaluate(engine, moves, depth, seconds):
    board = chess.Board(moves[0].fen_before)
    limit = chess.engine.Limit(depth=depth, time=seconds)
    before = engine.analyse(board, limit)
    for move in moves:
        color = board.turn
        board.push_uci(move.uci)
        if outcome := board.outcome():
            score = chess.engine.Mate(0) if outcome.winner is not None else chess.engine.Cp(0)
            after = {"score": chess.engine.PovScore(score, board.turn)}
        else:
            after = engine.analyse(board, limit)
        pv = [candidate.uci() for candidate in before.get("pv", [])]
        yield dict(
            ply=move.ply, depth=depth, best_move_uci=pv[0] if pv else None, pv_uci=pv,
            position_metadata=dict(mover="white" if color else "black", before_depth=before.get("depth"),
                                   after_depth=after.get("depth"), seconds_per_position=seconds),
            **score_fields(before["score"], after["score"], color),
        )
        before = after
