"""Recover legal chess positions from imperfect OCR of a move list."""
import re

import chess

FIGURINES = str.maketrans("♔♕♖♗♘♚♛♜♝♞", "KQRBNKQRBN")
LOOKALIKES = str.maketrans("аАсСеЕхХоОвВкКнНрР", "aAcCeExXoObBkKhHpP")

def read_token(text):
    text = text.translate(FIGURINES).translate(LOOKALIKES)
    text = re.sub(r"[^a-zA-Z0-9=\-]", "", text)
    castling = text.upper().replace("0", "O")
    if castling in ("O-O", "O-O-O"):
        return castling
    match = re.search(r"([a-h])([1-8il])(?:=?([qrbn]))?=?$", text, re.I)
    if not match:
        partial = re.search(r"x([a-h])$", text, re.I)
        return (partial[1].lower() + "?", "", True) if partial else None
    start = match.start()
    square = match[1].lower() + match[2].lower().translate(str.maketrans("il", "11"))
    promotion = match[3].upper() if match[3] else ""
    prefix = text[:start]
    # Chess.com uses an icon in place of the leading SAN piece letter.
    # OCR can omit it or misread it, so only the destination is mandatory.
    capture = prefix.lower().endswith("x")
    return square, promotion, capture

def matching_moves(board, token):
    if isinstance(token, str):
        try:
            yield board.parse_san(token)
        except ValueError:
            pass
        return
    square, promotion, capture = token
    target_file = chess.FILE_NAMES.index(square[0])
    for move in board.legal_moves:
        destination = chess.square_name(move.to_square)
        if chess.square_file(move.to_square) != target_file or board.is_castling(move):
            continue
        if square[1] != "?" and destination != square:
            continue
        if promotion and (not move.promotion or chess.piece_symbol(move.promotion).upper() != promotion):
            continue
        if capture and not board.is_capture(move):
            continue
        yield move

def recover_position(board, tokens, position_error, max_error):
    """Replay a contiguous run of visible plies and verify it against the board."""
    if not tokens:
        return None
    ply = board.ply()
    latest = max(tokens)
    if latest < ply or latest - ply > 96:
        return None
    start = ply
    while start > max(ply - len(board.move_stack), ply - 6) and start in tokens:
        start -= 1
    # One unreadable cell inside a continuous section can be recovered legally.
    # Do not guess unseen opening rows or speculate beyond the last OCR token.
    missing = [number for number in range(start + 1, latest + 1) if number not in tokens]
    if start >= latest or len(missing) > 1 or (missing and missing[0] == start + 1):
        return None
    initial = board.copy()
    while initial.ply() > start:
        initial.pop()
    states = [initial]
    for number in range(start + 1, latest + 1):
        next_states = {}
        for state in states:
            moves = matching_moves(state, tokens[number]) if number in tokens else state.legal_moves
            for move in moves:
                position = state.copy()
                position.push(move)
                next_states.setdefault(position.fen(), position)
                if len(next_states) > 512:
                    return None
        states = next_states.values()
        if not states:
            return None
    ranked = sorted(((position_error(state), state) for state in states), key=lambda item: item[0])
    error, candidate = ranked[0]
    if error > max_error or position_error(board) - error < .15:
        return None
    if len(ranked) > 1 and ranked[1][0] - error < .06:
        return None
    return candidate



