"""Recognize a new standard game from the board, not from an old move log."""
from dataclasses import dataclass
from functools import lru_cache

import chess

from move_list import recover_position


def piece_key(pieces):
    return tuple(sorted((square, piece.symbol()) for square, piece in pieces.items()))


@lru_cache(maxsize=1)
def opening_positions():
    positions = {}
    board = chess.Board()

    def remember():
        positions[piece_key(board.piece_map())] = board.copy()

    remember()
    for first in list(board.legal_moves):
        board.push(first)
        remember()
        for second in list(board.legal_moves):
            board.push(second)
            remember()
            board.pop()
        board.pop()
    return positions


def continues_game(current, candidate):
    """An ordinary next move must not trigger a new-game reset."""
    if current.ply() > candidate.ply():
        return False
    prefix = candidate.copy()
    while prefix.ply() > current.ply():
        if not prefix.move_stack:
            return False
        prefix.pop()
    # Halfmove clocks can be unknown after a screen-only recovery. Compare the
    # placement, turn and rights, without inventing a history from a screenshot.
    return (prefix.board_fen(), prefix.turn, prefix.castling_rights, prefix.ep_square) == (
        current.board_fen(), current.turn, current.castling_rights, current.ep_square)


def immediate_continuation(current, pieces):
    previous = current.piece_map()
    changed = sum(previous.get(square) != pieces.get(square)
                  for square in previous.keys() | pieces.keys())
    if changed > 8:  # Even two castlings affect at most eight squares.
        return False
    state = current.copy(stack=False)
    for first in list(state.legal_moves):
        state.push(first)
        if state.piece_map() == pieces:
            return True
        for second in list(state.legal_moves):
            state.push(second)
            matches = state.piece_map() == pieces
            state.pop()
            if matches:
                return True
        state.pop()
    return False


def irreversible_reset(current, pieces):
    """Distinguish a new game's reset from merely scrolling old move rows."""
    for color in chess.COLORS:
        old = [square for square, piece in current.piece_map().items() if piece.color == color]
        new = [square for square, piece in pieces.items() if piece.color == color]
        if len(new) > len(old):
            return True  # Captured pieces cannot reappear in the same game.
        old_pawns = list(current.pieces(chess.PAWN, color))
        new_pawns = [square for square, piece in pieces.items()
                     if piece == chess.Piece(chess.PAWN, color)]
        owners = {}

        def match(square, visited):
            for origin in old_pawns:
                progress = (chess.square_rank(square)-chess.square_rank(origin)) * (1 if color else -1)
                if (origin in visited or progress < 0
                        or abs(chess.square_file(square)-chess.square_file(origin)) > progress):
                    continue
                visited.add(origin)
                if origin not in owners or match(owners[origin], visited):
                    owners[origin] = square
                    return True
            return False

        if any(not match(square, set()) for square in new_pawns):
            return True  # A pawn returned backwards or another pawn reappeared.
    return False


@lru_cache(maxsize=8)
def replay_visible_start(pieces, frozen_tokens):
    tokens = dict(frozen_tokens)
    if 1 not in tokens or not tokens or max(tokens) > 96:
        return None
    expected = dict(pieces)
    return recover_position(chess.Board(), tokens,
                            lambda board: int(dict(piece_key(board.piece_map())) != expected), 0)


@dataclass
class MatchCandidate:
    board: chess.Board
    flipped: bool
    complete_history: bool = True


def board_after_counter_reset(pieces, current, tokens):
    """Attach to a later new game when its opening rows have scrolled away.

    The caller supplies only twice-confirmed OCR for these exact board pixels.
    Unknown castling/en-passant rights deliberately stay disabled.
    """
    latest = max(tokens or {}, default=0)
    if not 0 < latest < current.ply() or not irreversible_reset(current, pieces):
        return None
    token = tokens[latest]
    turn = latest % 2 == 0
    if token in ("O-O", "O-O-O"):
        square = chess.square(6 if token == "O-O" else 2, 0 if not turn else 7)
        moved = pieces.get(square)
        if moved is None or moved.piece_type != chess.KING:
            return None
    else:
        if not isinstance(token, tuple) or len(token) != 3 or "?" in token[0]:
            return None
        try:
            moved = pieces.get(chess.parse_square(token[0]))
        except ValueError:
            return None
    if moved is None or moved.color == turn:
        return None
    board = chess.Board(None)
    board.set_piece_map(pieces)
    board.turn = turn
    board.fullmove_number = latest//2+1
    return board if board.is_valid() else None


def find_new_match(raw_pieces, current, mode="Авто", fresh_tokens=None):
    if not raw_pieces:
        return None
    orientations = (False, True) if mode == "Авто" else (mode == "Чёрные снизу",)
    candidates = []
    for flipped in orientations:
        pieces = {63-square if flipped else square: piece for square, piece in raw_pieces.items()}
        if pieces == current.piece_map():
            return None
        key = piece_key(pieces)
        opening = opening_positions().get(key) if len(pieces) == 32 else None
        candidate = opening
        if candidate is None and fresh_tokens:
            candidate = replay_visible_start(key, tuple(sorted(fresh_tokens.items())))
        if candidate is not None and not continues_game(current, candidate):
            if immediate_continuation(current, pieces):
                return None
            candidates.append(MatchCandidate(candidate.copy(), flipped))
        elif candidate is None and fresh_tokens:
            snapshot = board_after_counter_reset(pieces, current, fresh_tokens)
            if snapshot is not None:
                candidates.append(MatchCandidate(snapshot, flipped, False))
    return candidates[0] if len(candidates) == 1 else None


class MatchWatcher:
    def __init__(self):
        self.reset()

    def reset(self):
        self.key = None
        self.since = 0
        self.seen = 0
        self.lookup_key = self.lookup = None

    def observe(self, raw_pieces, current, mode, now, fresh_tokens=None):
        tokens = tuple(sorted((fresh_tokens or {}).items()))
        lookup_key = (piece_key(raw_pieces or {}), current.fen(), mode, tokens)
        if lookup_key != self.lookup_key:
            self.lookup = find_new_match(raw_pieces, current, mode, fresh_tokens)
            self.lookup_key = lookup_key
        candidate = self.lookup
        if candidate is None:
            self.key = None
            self.seen = 0
            return None, False
        key = candidate.board.fen(), candidate.flipped
        if key != self.key:
            self.key, self.since, self.seen = key, now, 1
            return None, True
        self.seen += 1
        if self.seen >= 2 and now-self.since >= .10:
            return candidate, False
        return None, True
