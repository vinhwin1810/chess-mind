import hashlib
import io
import json
import re
from datetime import datetime, timezone

import chess
import chess.pgn

MAX_PGN_BYTES = 256 * 1024
MAX_GAMES = 20
MAX_PLIES = 300
RESULTS = {"1-0", "0-1", "1/2-1/2", "*"}
TOKEN = re.compile(
    r'\[[A-Za-z0-9_]+\s+"(?:[^"\\\r\n]|\\.)*"\s*\]'
    r'|\{[^}]*\}|;[^\n]*|(?m:^%[^\n]*)'
    r'|\s+|\d{1,4}\.(?:\.\.)?|\$\d+|[!?]+|[()]'
    r'|1/2-1/2|1-0|0-1|\*'
    r'|(?:O-O(?:-O)?|0-0(?:-0)?|[KQRBN]?[a-h]?[1-8]?x?[a-h][1-8](?:=?[QRBN])?)[+#]?'
)


class InvalidPGN(ValueError):
    pass


class ImportBuilder(chess.pgn.GameBuilder):
    def begin_game(self):
        super().begin_game()
        self.mainline_result = None
        self.visited_moves = 0

    def visit_move(self, board, move):
        self.visited_moves += 1
        if self.visited_moves > MAX_PLIES:
            raise InvalidPGN("Game exceeds the move limit.")
        if self.mainline_result and len(self.variation_stack) == 1:
            raise InvalidPGN("Moves follow the game result.")
        if not move:
            raise InvalidPGN("Null moves are unsupported.")
        super().visit_move(board, move)

    def visit_result(self, result):
        declared = self.game.headers.get("Result", "*")
        if self.mainline_result or (declared != "*" and declared != result):
            raise InvalidPGN("Conflicting game results.")
        self.mainline_result = result
        super().visit_result(result)

    def handle_error(self, error):
        raise InvalidPGN("Illegal move or invalid starting position.") from error


def validate_tokens(pgn):
    offset = nesting = 0
    for match in TOKEN.finditer(pgn):
        if match.start() != offset:
            raise InvalidPGN("Unrecognized PGN token.")
        offset = match.end()
        if match[0] == "(":
            nesting += 1
            if nesting > 16:
                raise InvalidPGN("Variations exceed the nesting limit.")
        if match[0] == ")":
            nesting -= 1
            if nesting < 0:
                raise InvalidPGN("Unbalanced variations.")
    if offset != len(pgn) or nesting:
        raise InvalidPGN("Incomplete or malformed PGN.")


def parse_pgn(pgn):
    pgn = pgn.lstrip("\ufeff")
    if "\x00" in pgn:
        raise InvalidPGN("PGN contains a null character.")
    validate_tokens(pgn)
    stream = io.StringIO(pgn)
    parsed = []
    while game := chess.pgn.read_game(stream, Visitor=ImportBuilder):
        if len(parsed) >= MAX_GAMES:
            raise InvalidPGN("Upload exceeds the game limit.")
        board = game.board()
        if type(board) is not chess.Board or board.chess960 or not board.is_valid():
            raise InvalidPGN("Only valid standard chess positions are supported.")
        result = game.headers.get("Result")
        if result not in RESULTS:
            raise InvalidPGN("Invalid game result.")
        start = board.fen()
        moves = []
        for ply, move in enumerate(game.mainline_moves(), 1):
            san = board.san(move)
            before = board.fen()
            board.push(move)
            moves.append(dict(ply=ply, uci=move.uci(), san=san, fen_before=before, fen_after=board.fen()))
        if not moves:
            raise InvalidPGN("Each game must contain at least one move.")
        outcome = board.outcome()
        if outcome and result != "*" and outcome.result() != result:
            raise InvalidPGN("Game result conflicts with the final position.")
        headers = game.headers
        white, black = (headers.get(color) for color in ("White", "Black"))
        white = white if white not in ("?", "") else None
        black = black if black not in ("?", "") else None
        date = headers.get("UTCDate") or headers.get("Date", "")
        try:
            played_at = datetime.strptime(date, "%Y.%m.%d").replace(tzinfo=timezone.utc)
        except ValueError:
            played_at = None
        fingerprint = [start, [move["uci"] for move in moves], white, black, date, result]
        normalized = game.accept(chess.pgn.StringExporter(headers=True, variations=False, comments=False))
        parsed.append((dict(pgn=normalized, white_username=white, black_username=black, result=result,
                            time_control=headers.get("TimeControl"), played_at=played_at,
                            canonical_hash=hashlib.sha256(json.dumps(fingerprint).encode()).hexdigest()), moves))
    if not parsed:
        raise InvalidPGN("Provide a PGN containing at least one game.")
    return parsed
