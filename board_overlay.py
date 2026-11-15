"""Non-activating, click-through Windows overlay over the captured board."""
import ctypes
from ctypes import wintypes
import tkinter as tk

import chess

OWN_COLOR = "#b9ff75"
OPPONENT_COLOR = "#6ccbff"
TRANSPARENT = "#010203"
user32 = ctypes.WinDLL("user32", use_last_error=True)
user32.GetAncestor.argtypes = (wintypes.HWND, wintypes.UINT)
user32.GetAncestor.restype = wintypes.HWND
user32.GetWindowLongPtrW.argtypes = (wintypes.HWND, ctypes.c_int)
user32.GetWindowLongPtrW.restype = ctypes.c_ssize_t
user32.SetWindowLongPtrW.argtypes = (wintypes.HWND, ctypes.c_int, ctypes.c_ssize_t)
user32.SetWindowLongPtrW.restype = ctypes.c_ssize_t
user32.SetWindowPos.argtypes = (wintypes.HWND, wintypes.HWND, ctypes.c_int, ctypes.c_int,
                               ctypes.c_int, ctypes.c_int, wintypes.UINT)
user32.SetWindowPos.restype = wintypes.BOOL
user32.ShowWindow.argtypes = (wintypes.HWND, ctypes.c_int)
user32.GetForegroundWindow.restype = wintypes.HWND
user32.IsWindowVisible.argtypes = (wintypes.HWND,)
user32.IsWindowVisible.restype = wintypes.BOOL
user32.GetWindowRect.argtypes = (wintypes.HWND, ctypes.POINTER(wintypes.RECT))
user32.SetWindowDisplayAffinity.argtypes = (wintypes.HWND, wintypes.DWORD)
user32.SetWindowDisplayAffinity.restype = wintypes.BOOL


def square_center(square, width, height, flipped=False):
    col, row = chess.square_file(square), 7-chess.square_rank(square)
    if flipped:
        col, row = 7-col, 7-row
    return (col+.5)*width/8, (row+.5)*height/8


def displayed_moves(board, pv, player_color):
    position = board.copy(stack=False)
    result = []
    for index, move in enumerate(pv[:2]):
        if move not in position.legal_moves:
            break
        own = position.turn == player_color
        # On the opponent's turn show their immediate expected move only.
        if index and board.turn != player_color:
            break
        san = position.san(move)
        result.append((move, OWN_COLOR if own else OPPONENT_COLOR,
                       ("Твой: " if own else "Ответ: ")+san, index > 0))
        position.push(move)
    return result


def paint_arrows(canvas, width, height, arrows, flipped, labels=True):
    cell = min(width, height)/8
    for move, color, label, conditional in reversed(arrows):
        a = square_center(move.from_square, width, height, flipped)
        b = square_center(move.to_square, width, height, flipped)
        points = *a, *b
        options = {"dash": (round(cell*.12), round(cell*.08))} if conditional else {}
        for fill, thickness, shape in (("#14221b", .15, (.30,.38,.15)),
                                        (color, .085, (.26,.32,.12))):
            canvas.create_line(*points, fill=fill, width=max(3, cell*thickness),
                               arrow=tk.LAST, arrowshape=tuple(cell*v for v in shape),
                               tags=("hint",), **options)
        if labels:
            # Labels stay within the board, including on a promotion rank.
            tx = min(width-58, max(58, b[0]))
            ty = max(13, b[1]-cell*.34)
            item = canvas.create_text(tx, ty, text=label, fill=color,
                                       font=("Segoe UI", max(9, round(cell*.14)), "bold"),
                                       tags=("hint-label",))
            bounds = canvas.bbox(item)
            rect = canvas.create_rectangle(bounds[0]-5, bounds[1]-3, bounds[2]+5,
                                            bounds[3]+3, fill="#14221b", outline=color,
                                            tags=("hint-label",))
            canvas.tag_lower(rect, item)


class BoardOverlay:
    def __init__(self, root):
        self.window = tk.Toplevel(root)
        self.window.withdraw()
        self.window.overrideredirect(True)
        self.window.configure(bg=TRANSPARENT)
        self.window.attributes("-transparentcolor", TRANSPARENT)
        self.canvas = tk.Canvas(self.window, bg=TRANSPARENT, highlightthickness=0,
                                takefocus=0)
        self.canvas.pack(fill="both", expand=True)
        self.window.update_idletasks()
        self.hwnd = user32.GetAncestor(self.window.winfo_id(), 2)
        style = user32.GetWindowLongPtrW(self.hwnd, -20)
        # LAYERED | TRANSPARENT | TOOLWINDOW | NOACTIVATE. Chrome receives clicks.
        user32.SetWindowLongPtrW(self.hwnd, -20, style | 0x80000 | 0x20 | 0x80 | 0x8000000)
        self.excluded_from_capture = bool(user32.SetWindowDisplayAffinity(self.hwnd, 0x11))
        self.visible = False
        self.content = self.bounds = None

    def hide(self):
        if self.visible:
            user32.ShowWindow(self.hwnd, 0)
            self.visible = False

    def before_capture(self):
        if not self.excluded_from_capture and self.visible:
            self.hide()
            # Older Windows versions cannot exclude an overlay from capture.
            # Wait for the compositor before reading the underlying pixels.
            ctypes.windll.dwmapi.DwmFlush()

    def show(self, bounds, arrows, flipped):
        if not arrows:
            self.hide()
            return
        left, top, right, bottom = bounds
        width, height = right-left, bottom-top
        content = (tuple(arrows), flipped, width, height)
        if content != self.content:
            self.canvas.configure(width=width, height=height)
            self.canvas.delete("all")
            paint_arrows(self.canvas, width, height, arrows, flipped)
            self.content = content
        if not self.visible or self.bounds != bounds:
            self.window.geometry(f"{width}x{height}{left:+d}{top:+d}")
            self.window.deiconify()
            self.window.update_idletasks()
            # SWP_NOACTIVATE | SHOWWINDOW, with HWND_TOPMOST.
            user32.SetWindowPos(self.hwnd, wintypes.HWND(-1), left, top, width, height, 0x10 | 0x40)
            self.bounds, self.visible = bounds, True

    def close(self):
        self.window.destroy()
