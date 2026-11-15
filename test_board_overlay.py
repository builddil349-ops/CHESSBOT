import sys
import unittest

import chess

if sys.platform == "win32":
    import tkinter as tk
    from board_overlay import (BoardOverlay, OWN_COLOR, OPPONENT_COLOR,
                               displayed_moves, square_center, user32)
    user32.SetProcessDPIAware()


@unittest.skipUnless(sys.platform == "win32", "Windows overlay")
class OverlayTests(unittest.TestCase):
    def test_both_colors_and_conditional_reply(self):
        board = chess.Board()
        pv = (chess.Move.from_uci("e2e4"), chess.Move.from_uci("e7e5"))
        arrows = displayed_moves(board, pv, chess.WHITE)
        self.assertEqual([a[1] for a in arrows], [OWN_COLOR, OPPONENT_COLOR])
        self.assertFalse(arrows[0][3])
        self.assertTrue(arrows[1][3])
        self.assertEqual(len(displayed_moves(board, pv, chess.BLACK)), 1)
        board.push(pv[0])
        replies = displayed_moves(board, (pv[1],), chess.WHITE)
        self.assertEqual(replies[0][1], OPPONENT_COLOR)
        self.assertFalse(replies[0][3])

    def test_black_orientation_and_promotion_label(self):
        self.assertEqual(square_center(chess.A1, 800, 800, False), (50, 750))
        self.assertEqual(square_center(chess.A1, 800, 800, True), (750, 50))
        board = chess.Board("4k3/8/8/8/8/8/1p6/4K3 b - - 0 1")
        move = chess.Move.from_uci("b2b1n")
        arrow = displayed_moves(board, (move,), chess.BLACK)[0]
        self.assertIn("=N", arrow[2])
        self.assertEqual(arrow[1], OWN_COLOR)

    def test_native_overlay_maps_moves_hides_and_does_not_activate(self):
        root = tk.Tk()
        root.withdraw()
        overlay = BoardOverlay(root)
        try:
            foreground = user32.GetForegroundWindow()
            arrows = displayed_moves(chess.Board(), (chess.Move.from_uci("e2e4"),), chess.WHITE)
            overlay.show((100, 120, 676, 696), arrows, False)
            root.update()
            self.assertTrue(user32.IsWindowVisible(overlay.hwnd))
            self.assertEqual(user32.GetForegroundWindow(), foreground)
            style = user32.GetWindowLongPtrW(overlay.hwnd, -20)
            self.assertEqual(style & (0x80000 | 0x20 | 0x8000000), 0x80000 | 0x20 | 0x8000000)
            self.assertEqual(len(overlay.canvas.find_withtag("hint")), 2)
            overlay.show((-300, 70, 276, 646), arrows, True)
            root.update()
            rect = __import__('ctypes').wintypes.RECT()
            user32.GetWindowRect(overlay.hwnd, __import__('ctypes').byref(rect))
            self.assertEqual((rect.left, rect.top, rect.right, rect.bottom), (-300, 70, 276, 646))
            overlay.hide()
            self.assertFalse(user32.IsWindowVisible(overlay.hwnd))
        finally:
            overlay.close()
            root.destroy()

    def test_arrows_do_not_contaminate_board_capture(self):
        from PIL import ImageChops, ImageGrab
        root = tk.Tk()
        root.overrideredirect(True)
        root.geometry('400x400+140+140')
        root.configure(bg='#426d91')
        root.attributes('-topmost', True)
        root.update()
        __import__('ctypes').windll.dwmapi.DwmFlush()
        overlay = BoardOverlay(root)
        try:
            box = (140, 140, 540, 540)
            baseline = ImageGrab.grab(bbox=box, include_layered_windows=True)
            arrows = displayed_moves(chess.Board(), (chess.Move.from_uci('e2e4'),), chess.WHITE)
            overlay.show(box, arrows, False)
            root.update()
            __import__('ctypes').windll.dwmapi.DwmFlush()
            overlay.before_capture()
            captured = ImageGrab.grab(bbox=box, include_layered_windows=True)
            self.assertIsNone(ImageChops.difference(baseline, captured).getbbox(),
                              'The recognition input must contain the board without overlay pixels')
        finally:
            overlay.close()
            root.destroy()


if __name__ == "__main__":
    unittest.main()
