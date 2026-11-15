from concurrent.futures import Future
from pathlib import Path
import sys
from tempfile import TemporaryDirectory
from types import SimpleNamespace
import unittest
from unittest.mock import patch

import chess

from match_tracking import MatchWatcher, find_new_match
from move_list import read_token


def position(*moves):
    board = chess.Board()
    for move in moves:
        board.push_san(move)
    return board


def pixels(board, flipped=False):
    return {63-square if flipped else square: piece
            for square, piece in board.piece_map().items()}


def tokens_for(board):
    result = {}
    state = board.root()
    for move in board.move_stack:
        result[state.ply()+1] = read_token(state.san(move))
        state.push(move)
    return result


class MatchTrackingTests(unittest.TestCase):
    def setUp(self):
        self.previous = position('e4', 'e5', 'Nf3', 'Nc6', 'Bb5', 'a6')

    def test_starting_board_restarts_after_two_stable_observations(self):
        watcher = MatchWatcher()
        raw = pixels(chess.Board())
        candidate, waiting = watcher.observe(raw, self.previous, 'Авто', 0)
        self.assertIsNone(candidate)
        self.assertTrue(waiting)
        candidate, waiting = watcher.observe(raw, self.previous, 'Авто', .11)
        self.assertFalse(waiting)
        self.assertEqual(candidate.board.fen(), chess.Board().fen())

    def test_missed_first_or_second_move_with_both_orientations(self):
        for flipped in (False, True):
            for board in (position('e4'), position('d4', 'Nf6')):
                with self.subTest(flipped=flipped, fen=board.fen()):
                    candidate = find_new_match(pixels(board, flipped), self.previous)
                    self.assertEqual(candidate.flipped, flipped)
                    self.assertEqual(candidate.board.fen(), board.fen())
                    self.assertEqual(candidate.board.move_stack, board.move_stack)

    def test_ordinary_moves_and_board_flip_do_not_start_another_game(self):
        for before, after in ((chess.Board(), position('e4')),
                              (position('e4'), position('e4', 'e5')),
                              (self.previous, self.previous)):
            for flipped in (False, True):
                self.assertIsNone(find_new_match(pixels(after, flipped), before))
        after = self.previous.copy()
        after.push_san('Ba4')
        self.assertIsNone(find_new_match(pixels(after), self.previous,
                                         fresh_tokens=tokens_for(after)))
        # Knights can legally return to the starting placement in the same game.
        before = position('Nf3', 'Nf6', 'Ng1')
        after = position('Nf3', 'Nf6', 'Ng1', 'Ng8')
        self.assertIsNone(find_new_match(pixels(after), before))

    def test_different_game_at_same_ply_is_detected(self):
        after = position('e4')
        candidate = find_new_match(pixels(after), position('d4'))
        self.assertEqual(candidate.board.fen(), after.fen())

    def test_already_started_match_replays_fresh_move_list(self):
        after = position('d4', 'd5', 'c4', 'e6', 'Nc3', 'Nf6')
        candidate = find_new_match(pixels(after, True), self.previous,
                                    fresh_tokens=tokens_for(after))
        self.assertEqual(candidate.board.fen(), after.fen())
        self.assertTrue(candidate.flipped)
        # Old rows or a missing opening cannot establish this new game.
        self.assertIsNone(find_new_match(pixels(after), self.previous,
                                         fresh_tokens=tokens_for(self.previous)))
        late_rows = tokens_for(after)
        del late_rows[1]
        self.assertIsNone(find_new_match(pixels(after), self.previous, fresh_tokens=late_rows))

    def test_finished_match_does_not_block_the_next_one(self):
        mate = position('f3', 'e5', 'g4', 'Qh4#')
        self.assertTrue(mate.is_game_over())
        after = position('d4', 'd5', 'c4', 'e6')
        candidate = find_new_match(pixels(after), mate, fresh_tokens=tokens_for(after))
        self.assertEqual(candidate.board.fen(), after.fen())
        self.assertFalse(candidate.board.is_game_over())

    def test_transient_frames_and_manual_orientation_are_respected(self):
        watcher = MatchWatcher()
        self.assertTrue(watcher.observe(pixels(chess.Board()), self.previous, 'Авто', 0)[1])
        self.assertEqual(watcher.observe(None, self.previous, 'Авто', .2), (None, False))
        self.assertTrue(watcher.observe(pixels(chess.Board()), self.previous, 'Авто', .3)[1])
        self.assertIsNone(find_new_match(pixels(position('e4'), True), self.previous,
                                         mode='Белые снизу'))

    def test_scrolled_opening_requires_fresh_lower_counter_and_valid_turn(self):
        previous = position('e4', 'e5', 'Nf3', 'Nc6', 'Bb5', 'a6', 'Ba4', 'Nf6',
                            'O-O', 'Be7', 'Re1', 'b5', 'Bb3', 'd6', 'c3', 'O-O')
        after = position('d4', 'd5', 'c4', 'e6', 'Nc3', 'Nf6', 'Bg5', 'Be7', 'e3', 'O-O')
        late_rows = {ply: token for ply, token in tokens_for(after).items() if ply >= 7}
        self.assertIsNone(find_new_match(pixels(after), previous))
        candidate = find_new_match(pixels(after, True), previous, fresh_tokens=late_rows)
        self.assertFalse(candidate.complete_history)
        self.assertEqual(candidate.board.piece_map(), after.piece_map())
        self.assertEqual(candidate.board.turn, after.turn)
        self.assertEqual(candidate.board.ply(), after.ply())
        self.assertFalse(candidate.board.castling_rights)
        wrong = {9: read_token('g8')}
        self.assertIsNone(find_new_match(pixels(after), previous, fresh_tokens=wrong))

    def test_scrolling_older_rows_while_playing_does_not_reset_the_match(self):
        before = position('e4', 'e5', 'Nf3', 'Nc6', 'Bb5', 'a6')
        after = before.copy()
        after.push_san('Ba4')
        old_rows = {1: read_token('e4'), 2: read_token('e5')}
        self.assertIsNone(find_new_match(pixels(after), before, fresh_tokens=old_rows))


@unittest.skipUnless(sys.platform == 'win32', 'Windows application integration')
class MatchAdoptionTests(unittest.TestCase):
    def test_transition_archives_history_and_discards_all_old_ocr(self):
        import trainer
        with TemporaryDirectory() as folder:
            app = trainer.Trainer.__new__(trainer.Trainer)
            app.board = position('e4', 'e5', 'Nf3', 'Nc6', 'Bb5', 'a6')
            app.history = trainer.MoveHistory(Path(folder)/'moves.txt')
            app.history.record(app.board)
            old_text = app.history.read()
            app.history_writable = True
            app.orientation_mode = SimpleNamespace(get=lambda: 'Авто')
            app.match_watcher = MatchWatcher()
            app.clear_hints = lambda: None
            app.learn_templates = lambda samples: None
            app.promotion = SimpleNamespace(set=lambda text: None)
            app.capture_id = 7
            old_job = app.ocr_job = Future()
            after = position('d4')
            app.frame_raw_pieces = pixels(after, True)
            app.frame = object()
            app.visible_frame = None
            app.visible_tokens = {}
            app.visible_count = 0
            app.ocr_tokens = {50: ('h8', 'Q', True)}
            with patch.object(trainer.time, 'monotonic', return_value=0):
                self.assertTrue(app.detect_new_match(app.frame))
            with patch.object(trainer.time, 'monotonic', return_value=.11), \
                    patch.object(trainer, 'tile_signatures', return_value={}):
                self.assertFalse(app.detect_new_match(app.frame))
            self.assertEqual(app.board.fen(), after.fen())
            self.assertEqual(app.capture_id, 8)
            self.assertTrue(old_job.cancelled())
            self.assertIsNone(app.ocr_job)
            self.assertEqual(app.ocr_tokens, {1: read_token('d4')})
            self.assertEqual(app.history.read(), '1. d4\n')
            archives = list((Path(folder)/'games').glob('*.txt'))
            self.assertEqual(len(archives), 1)
            self.assertEqual(archives[0].read_text(encoding='utf-8'), old_text)
            self.assertFalse(app.needs_calibration)
            self.assertTrue(app.orientation)


if __name__ == '__main__':
    unittest.main()
