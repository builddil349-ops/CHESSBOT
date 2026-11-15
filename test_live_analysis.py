import contextlib
import time
import unittest

import chess

from live_analysis import LiveAnalysis, legal_line


class ScriptedEngine:
    def __init__(self):
        self.calls = []
        self.stopped = []

    def analysis(self, board, limit, info):
        self.calls.append(board.fen())
        engine = self
        position = board.copy()
        pv = []
        for _ in range(3):
            if position.is_game_over():
                break
            move = next(iter(position.legal_moves))
            pv.append(move)
            position.push(move)

        class Stream:
            def stop(self):
                engine.stopped.append(board.fen())

            def __iter__(self):
                yield {"depth": 6, "pv": pv}
                time.sleep(.025)
                yield {"depth": 12, "pv": pv}

        return contextlib.nullcontext(Stream())


class LiveAnalysisTests(unittest.TestCase):
    def setUp(self):
        self.engine = ScriptedEngine()
        self.analysis = LiveAnalysis(self.engine)
        self.addCleanup(self.analysis.close)

    def wait_update(self):
        deadline = time.monotonic()+2
        while time.monotonic() < deadline:
            result = self.analysis.poll()
            if result is not None:
                return result
            time.sleep(.002)
        self.fail("Analysis update did not arrive")

    def test_new_position_rejects_old_updates_and_duplicate_requests(self):
        board = chess.Board()
        self.analysis.request(board, chess.WHITE, 1)
        self.wait_update()
        board.push_san("e4")
        self.analysis.request(board, chess.WHITE, 1)
        self.analysis.request(board, chess.WHITE, 1)
        result = self.wait_update()
        self.assertEqual(result.key[0][0], board.fen())
        self.assertIn(result.pv[0], board.legal_moves)
        time.sleep(.06)
        self.assertEqual(len(self.engine.calls), 2)
        result = self.analysis.poll()
        if result:
            self.assertEqual(result.key[0][0], board.fen())

    def test_player_and_capture_changes_cannot_reuse_old_response_key(self):
        board = chess.Board()
        self.analysis.request(board, chess.WHITE, 1)
        self.wait_update()
        self.analysis.request(board, chess.BLACK, 2)
        result = self.wait_update()
        self.assertEqual(result.key[1:], (chess.BLACK, 2))
        self.analysis.cancel()
        time.sleep(.04)
        self.assertIsNone(self.analysis.poll())

    def test_predicted_actual_move_has_immediate_legal_cached_continuation(self):
        board = chess.Board()
        self.analysis.request(board, chess.WHITE, 1)
        first = self.wait_update()
        board.push(first.pv[0])
        self.analysis.request(board, chess.WHITE, 1)
        update = self.analysis.poll()
        self.assertIsNotNone(update)
        self.assertIn(update.pv[0], board.legal_moves)
        self.assertEqual(update.key[0][0], board.fen())

    def test_illegal_reply_is_never_displayed(self):
        board = chess.Board()
        line = legal_line(board, [chess.Move.from_uci("e2e4"), chess.Move.from_uci("e4e5")])
        self.assertEqual(line, (chess.Move.from_uci("e2e4"),))


if __name__ == "__main__":
    unittest.main()
