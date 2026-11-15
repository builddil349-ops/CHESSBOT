"""Local screen-based chess assistant with opponent move tracking."""
import ctypes
import os
from pathlib import Path
import time
from ctypes import wintypes
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass
import tkinter as tk
from tkinter import filedialog, messagebox, ttk
from PIL import ImageChops, ImageGrab, ImageStat, ImageTk
import chess
import chess.engine

from adaptive_themes import LIBRARY
from board_overlay import BoardOverlay, displayed_moves, paint_arrows
from live_analysis import LiveAnalysis
from match_tracking import MatchWatcher
from engine_setup import find_engine, remember_engine
from move_list import matching_moves, read_token, recover_position
from screen_ocr import scan_move_list, locate_move_list
from screen_locator import locate_boards
from standard_board import board_features, read_standard_pieces

user32 = ctypes.windll.user32
user32.GetForegroundWindow.restype = wintypes.HWND
user32.GetClientRect.argtypes = (wintypes.HWND, ctypes.POINTER(wintypes.RECT))
user32.ClientToScreen.argtypes = (wintypes.HWND, ctypes.POINTER(wintypes.POINT))
user32.GetWindowTextW.argtypes = (wintypes.HWND, wintypes.LPWSTR, ctypes.c_int)
user32.GetClassNameW.argtypes = (wintypes.HWND, wintypes.LPWSTR, ctypes.c_int)
user32.WindowFromPoint.argtypes = (wintypes.POINT,)
user32.WindowFromPoint.restype = wintypes.HWND
user32.GetAncestor.argtypes = (wintypes.HWND,wintypes.UINT)
user32.GetAncestor.restype = wintypes.HWND
user32.IsWindow.argtypes = (wintypes.HWND,)
user32.IsWindow.restype = wintypes.BOOL

def chrome_client(window):
    title, kind = ctypes.create_unicode_buffer(512), ctypes.create_unicode_buffer(128)
    user32.GetWindowTextW(window, title, len(title))
    user32.GetClassNameW(window, kind, len(kind))
    if kind.value != "Chrome_WidgetWin_1" or not title.value.endswith(" - Google Chrome"):
        return None
    rect, origin = wintypes.RECT(), wintypes.POINT()
    if user32.GetClientRect(window, ctypes.byref(rect)) and user32.ClientToScreen(window, ctypes.byref(origin)):
        return origin.x, origin.y, rect.right, rect.bottom
    return None

def grab(box):
    return ImageGrab.grab(bbox=box, all_screens=True).convert("RGB")


def capture_desktop():
    left,top = user32.GetSystemMetrics(76),user32.GetSystemMetrics(77)
    width,height = user32.GetSystemMetrics(78),user32.GetSystemMetrics(79)
    return grab((left,top,left+width,top+height)), (left,top)


@dataclass(frozen=True)
class ScreenMatch:
    browser: int
    client_size: tuple
    region: tuple
    moves_region: object


def discover_screen():
    image, origin = capture_desktop()
    for candidate in locate_boards(image):
        a,b,c,d = candidate.box
        point = wintypes.POINT(origin[0]+(a+c)//2,origin[1]+(b+d)//2)
        browser = user32.GetAncestor(user32.WindowFromPoint(point),2)
        client = chrome_client(browser)
        if not client:
            continue
        x,y,width,height = client
        region = (a+origin[0]-x,b+origin[1]-y,c+origin[0]-x,d+origin[1]-y)
        if not (0 <= region[0] < region[2] <= width and 0 <= region[1] < region[3] <= height):
            continue
        pieces = read_standard_pieces(image.crop(candidate.box),False)
        if not pieces:
            continue
        probe = chess.Board(None)
        probe.set_piece_map(pieces)
        valid = False
        for color in chess.COLORS:
            probe.turn = color
            valid = valid or probe.is_valid()
        if not valid:
            continue
        clip = x-origin[0],y-origin[1],x+width-origin[0],y+height-origin[1]
        try:
            moves = locate_move_list(image,candidate.box,clip)
        except (OSError,RuntimeError):
            moves = None
        if moves:
            moves = tuple(value+(origin[index%2]-(x if index%2 == 0 else y))
                          for index,value in enumerate(moves))
        return ScreenMatch(browser,(width,height),region,moves)
    return None

def signature(board):
    return {square: piece.symbol() for square, piece in board.piece_map().items()}

def tile_signatures(image, flipped):
    width, height = image.size
    result = {}
    for square in chess.SQUARES:
        file, rank = chess.square_file(square), chess.square_rank(square)
        col, row = (7-file, rank) if flipped else (file, 7-rank)
        tile = image.crop((round(col*width/8), round(row*height/8),
                           round((col+1)*width/8), round((row+1)*height/8)))
        pixels = tile.resize((16, 16)).load()
        corners = [pixels[x,y] for x,y in ((1,1),(14,1),(1,14),(14,14))]
        background = [sorted(pixel[i] for pixel in corners)[2] for i in range(3)]
        # Keep the sign of the contrast so light and dark pieces stay distinct.
        result[square] = tuple(
            max(-1, min(1, sum(a-b for a,b in zip(pixels[x,y], background))/210))
            for y in range(3,13) for x in range(3,13)
        )
    return result

def distance(a, b):
    return sum(abs(x-y) for x,y in zip(a,b))/len(a)

def occupied_squares(samples):
    return {square for square, sample in samples.items()
            if sum(map(abs, sample))/len(sample) > .11}

POSITION_TOLERANCE = .48

PROMOTION_NAMES = {
    chess.QUEEN: "ферзь", chess.ROOK: "ладья",
    chess.BISHOP: "слон", chess.KNIGHT: "конь",
}

def hint_text(board, move):
    if move.promotion:
        return f"Подсказка: {chess.square_name(move.to_square)} → {PROMOTION_NAMES[move.promotion]}"
    return f"Подсказка: {board.san(move)}"

def same_board_frame(first, second):
    if first.size != second.size:
        return False
    difference = ImageChops.difference(first.resize((64,64)), second.resize((64,64)))
    for row in range(8):
        for col in range(8):
            cell = difference.crop((col*8,row*8,col*8+8,row*8+8))
            if max(ImageStat.Stat(cell).mean) > 1.8:
                return False
    return True


def rotate_pieces(pieces):
    return {chess.square(7-chess.square_file(square), 7-chess.square_rank(square)): piece
            for square, piece in pieces.items()}


def board_orientation(raw_pieces, board, previous=None):
    if raw_pieces is None:
        return previous
    if raw_pieces == board.piece_map():
        return False
    if rotate_pieces(raw_pieces) == board.piece_map():
        return True
    white = [chess.square_rank(square) for square, piece in raw_pieces.items()
             if piece == chess.Piece(chess.PAWN, chess.WHITE)]
    black = [chess.square_rank(square) for square, piece in raw_pieces.items()
             if piece == chess.Piece(chess.PAWN, chess.BLACK)]
    if previous is not None:
        return previous
    if len(white) >= 2 and len(black) >= 2:
        difference = sum(white)/len(white) - sum(black)/len(black)
        if abs(difference) >= 2:
            return difference > 0
    white_king = next((square for square, piece in raw_pieces.items()
                       if piece == chess.Piece(chess.KING, chess.WHITE)), None)
    black_king = next((square for square, piece in raw_pieces.items()
                       if piece == chess.Piece(chess.KING, chess.BLACK)), None)
    if white_king is not None and black_king is not None:
        difference = chess.square_rank(white_king) - chess.square_rank(black_king)
        if abs(difference) >= 4:
            return difference > 0
    return None


def sequence_for_pieces(board, pieces):
    if pieces == board.piece_map():
        return ()
    singles, pairs = [], []
    position = board.copy()
    for first in list(position.legal_moves):
        position.push(first)
        try:
            if position.piece_map() == pieces:
                singles.append((first,))
            else:
                for second in list(position.legal_moves):
                    position.push(second)
                    try:
                        if position.piece_map() == pieces:
                            pairs.append((first, second))
                    finally:
                        position.pop()
        finally:
            position.pop()
    if len(singles) == 1:
        return singles[0]
    if not singles and len(pairs) == 1:
        return pairs[0]
    return None


class MoveHistory:
    def __init__(self, path):
        self.path = path
        self.start_ply = 0
        self.prefix = ""

    def read(self):
        try:
            return self.path.read_text(encoding="utf-8")
        except FileNotFoundError:
            return ""

    def write(self, text):
        self.path.parent.mkdir(parents=True, exist_ok=True)
        temporary = self.path.with_suffix(".tmp")
        temporary.write_text(text, encoding="utf-8")
        temporary.replace(self.path)

    def record(self, board):
        root_ply = board.ply() - len(board.move_stack)
        moves = board.move_stack[max(0, self.start_ply - root_ply):]
        position = board.copy()
        for _ in moves:
            position.pop()
        lines = []
        for move in moves:
            prefix = f"{position.fullmove_number}." if position.turn else f"{position.fullmove_number}…"
            lines.append(f"{prefix} {position.san(move)}")
            position.push(move)
        self.write(self.prefix + "\n".join(lines) + ("\n" if lines else ""))

    def reset(self, ply):
        self.write("")
        self.start_ply = ply
        self.prefix = ""

    def archive(self):
        text = self.read()
        if not text.strip():
            return None
        directory = self.path.parent / "games"
        directory.mkdir(parents=True, exist_ok=True)
        name = time.strftime("%Y-%m-%d_%H-%M-%S") + f"-{time.time_ns()}.txt"
        path = directory / name
        path.write_text(text, encoding="utf-8")
        return path


class Trainer:
    def __init__(self, root, engine):
        self.root, self.engine = root, engine
        self.region = self.future = self.board = None
        self.moves_region = None
        self.auto_mode = False
        self.auto_generation = 0
        self.scan_executor = ThreadPoolExecutor(max_workers=1,thread_name_prefix='screen-search')
        self.scan_future = None
        self.next_scan = 0
        self.mismatch_since = None
        self.auto_color_pending = False
        self.analysis = LiveAnalysis(engine)
        self.reply = None
        self.analysis_error = ""
        self.analysis_depth = 0
        self.overlay_enabled = tk.BooleanVar(value=True)
        self.preview_enabled = tk.BooleanVar(value=False)
        self.paint_image = None
        self.paint_key = None
        self.board_bounds = None
        self.ocr_tokens = {}
        self.ocr_executor = ThreadPoolExecutor(max_workers=1)
        self.ocr_job = None
        self.capture_id = 0
        self.match_watcher = MatchWatcher()
        self.capture_retry_after = 0
        self.history_writable = True
        self.closing = False
        self.selection_timer = None
        self.promotion = tk.StringVar()
        local = Path(os.environ.get("LOCALAPPDATA", Path.home() / "AppData" / "Local"))
        self.history = MoveHistory(local / "ChessBotTrainer" / "moves.txt")
        self.player_black = tk.BooleanVar()
        self.auto_color = tk.BooleanVar(value=True)
        self.orientation_mode = tk.StringVar(value="Авто")
        self.orientation = None
        self.status = tk.StringVar(value="Открой шахматы в Chrome и нажми «Начать». Доску найду сам.")
        compact = root.winfo_screenheight() < 950
        self.board_size = 224 if compact else 288
        self.move_heading = tk.StringVar(value="Готов к поиску")
        self.move_caption = tk.StringVar(value="Одна кнопка — доску и ходы найду на экране")
        self.phase = tk.StringVar(value="ГОТОВ К ЗАХВАТУ")
        root.title("CHESSBOT 1.4 · автоматический экран")
        root.attributes("-topmost", True)
        root.configure(bg="#0d1411")
        root.resizable(False, False)

        style = ttk.Style(root)
        style.theme_use("clam")
        style.configure("Board.TCombobox", fieldbackground="#1a2720", background="#24382b",
                        foreground="#e6efe6", arrowcolor="#b5d8a7", borderwidth=0,
                        padding=(8, 4), font=("Segoe UI", 10))
        style.map("Board.TCombobox", fieldbackground=[("readonly", "#1a2720")],
                  foreground=[("readonly", "#e6efe6")],
                  selectbackground=[("readonly", "#1a2720")],
                  selectforeground=[("readonly", "#e6efe6")])
        root.option_add("*TCombobox*Listbox.background", "#1a2720")
        root.option_add("*TCombobox*Listbox.foreground", "#e6efe6")
        root.option_add("*TCombobox*Listbox.selectBackground", "#354b39")
        root.option_add("*TCombobox*Listbox.font", ("Segoe UI", 10))

        panel = tk.Frame(root, bg="#0d1411", padx=18, pady=10 if compact else 16)
        panel.pack(fill="both", expand=True)
        header = tk.Frame(panel, bg="#0d1411")
        header.pack(fill="x", pady=(0, 8 if compact else 14))
        logo = tk.Canvas(header, width=40, height=42, bg="#1c3021", highlightthickness=0)
        logo.create_text(20, 20, text="♞", font=("Segoe UI Symbol", 27), fill="#c1f58c")
        logo.pack(side="left", padx=(0, 10))
        brand = tk.Frame(header, bg="#0d1411")
        brand.pack(side="left")
        tk.Label(brand, text="CHESSBOT", font=("Segoe UI", 20, "bold"),
                 bg="#0d1411", fg="#f1f4eb").pack(anchor="w")
        tk.Label(brand, text="Автоматический экран · 1.4", font=("Segoe UI", 9),
                 bg="#0d1411", fg="#8b9e91").pack(anchor="w")
        tk.Button(header, text="Темы", command=self.show_themes,
                  font=("Segoe UI", 9), bg="#1a2720", fg="#abd28f",
                  activebackground="#2a3d2c", activeforeground="#efffdf",
                  relief="flat", padx=8, pady=4, cursor="hand2").pack(side="right",anchor="n",pady=5)

        hero = tk.Frame(panel, bg="#1b2d20", padx=14, pady=7 if compact else 10,
                        highlightbackground="#35472b", highlightthickness=1)
        hero.pack(fill="x", pady=(0, 8 if compact else 12))
        tk.Label(hero, textvariable=self.phase, font=("Segoe UI", 8, "bold"),
                 bg="#1b2d20", fg="#9fc780").pack(anchor="w")
        tk.Label(hero, textvariable=self.move_heading, font=("Segoe UI", 22 if compact else 25, "bold"),
                 bg="#1b2d20", fg="#d1ffa0", anchor="w").pack(fill="x", pady=(0, 1))
        tk.Label(hero, textvariable=self.move_caption, font=("Segoe UI", 9),
                 wraplength=300, justify="left", bg="#1b2d20", fg="#b4c5b3").pack(anchor="w")

        board_frame = tk.Frame(panel, bg="#24332a", padx=1, pady=1)
        board_frame.pack()
        self.canvas = tk.Canvas(board_frame, width=self.board_size, height=self.board_size,
                                bg="#17211b", highlightthickness=0)
        self.canvas.pack()
        self.draw_placeholder()
        self.board_frame = board_frame
        board_frame.pack_forget()
        self.preview_anchor = hero

        status_row = tk.Frame(panel, bg="#0d1411")
        status_row.pack(fill="x", pady=(6, 7) if compact else (8, 11))
        self.status_dot = tk.Label(status_row, text="●", font=("Segoe UI", 8),
                                   bg="#0d1411", fg="#8b9e91")
        self.status_dot.pack(side="left", anchor="n", padx=(0, 6), pady=2)
        tk.Label(status_row, textvariable=self.status, font=("Segoe UI", 9),
                 wraplength=292, justify="left", anchor="w", height=2,
                 bg="#0d1411", fg="#a0b2a4").pack(side="left", fill="x", expand=True)

        settings = tk.Frame(panel, bg="#142019", padx=10, pady=9)
        settings.pack(fill="x")
        color_row = tk.Frame(settings, bg="#142019")
        color_row.pack(fill="x")
        tk.Label(color_row, text="Играю за", bg="#142019", fg="#9fb2a4",
                 font=("Segoe UI", 9)).pack(side="left")
        colors = tk.Frame(color_row, bg="#142019")
        colors.pack(side="right")
        for label, value in (("Белых", False), ("Чёрных", True)):
            tk.Radiobutton(colors, text=label, variable=self.player_black, value=value,
                           command=self.player_changed, indicatoron=False, relief="flat", bd=0,
                           padx=12, pady=4, font=("Segoe UI", 9, "bold"),
                           bg="#213127", fg="#dbe5da", selectcolor="#496331",
                           activebackground="#324a32", activeforeground="#efffdf",
                           cursor="hand2").pack(side="left", padx=(3, 0))
        orientation_row = tk.Frame(settings, bg="#142019")
        orientation_row.pack(fill="x", pady=(5 if compact else 8, 0))
        tk.Label(orientation_row, text="Поворот доски", bg="#142019", fg="#9fb2a4",
                 font=("Segoe UI", 9)).pack(side="left")
        orientation_choice = ttk.Combobox(
            orientation_row, textvariable=self.orientation_mode, style="Board.TCombobox",
            state="readonly", width=17, values=("Авто", "Белые снизу", "Чёрные снизу"))
        orientation_choice.pack(side="right")
        orientation_choice.bind("<<ComboboxSelected>>", self.orientation_changed)

        for text, variable, command in (
                ("Мой цвет автоматически", self.auto_color, self.auto_color_changed),
                ("Стрелки прямо на доске", self.overlay_enabled, self.overlay_changed),
                ("Показывать копию доски", self.preview_enabled, self.preview_changed)):
            tk.Checkbutton(settings, text=text, variable=variable, command=command,
                           bg="#142019", fg="#c2d0c1", selectcolor="#24382b",
                           activebackground="#142019", activeforeground="#efffdf",
                           font=("Segoe UI", 9), bd=0, highlightthickness=0,
                           anchor="w").pack(fill="x", pady=(5, 0))
        tk.Label(settings, text="● Зелёная — твой ход   ● Голубая — ответ",
                 font=("Segoe UI", 8), bg="#142019", fg="#9fb2a4").pack(anchor="w", pady=(5, 0))

        self.start_button = tk.Button(panel, text="▶   Начать", command=self.toggle_auto,
                  bg="#c0ef86", fg="#182411", activebackground="#d4ffa6",
                  activeforeground="#182411", font=("Segoe UI", 11, "bold"),
                  relief="flat", bd=0, pady=7 if compact else 9, cursor="hand2")
        self.start_button.pack(fill="x", pady=(8 if compact else 12, 8))
        actions = tk.Frame(panel, bg="#0d1411")
        actions.pack(fill="x")
        for column, (label, command) in enumerate((
                ("История", self.show_history), ("Найти заново", self.start_auto),
                ("Сброс ходов", self.reset_history))):
            actions.columnconfigure(column, weight=1)
            tk.Button(actions, text=label, command=command, bg="#1a2720", fg="#c2d0c1",
                      activebackground="#2a3d2c", activeforeground="#efffdf",
                      font=("Segoe UI", 9), relief="flat", bd=0, pady=6,
                      cursor="hand2").grid(row=0, column=column, sticky="ew",
                                          padx=(0 if column == 0 else 5, 0))
        tk.Label(panel, textvariable=self.promotion, bg="#0d1411", fg="#c0ef86",
                 wraplength=310, justify="left", font=("Segoe UI", 9)).pack(
                     anchor="w", pady=(7, 0))
        self.status.trace_add("write", self.update_summary)
        self.overlay = BoardOverlay(root)
        root.protocol("WM_DELETE_WINDOW", self.close)
        self.timer = root.after(33, self.tick)

    def auto_color_changed(self):
        self.auto_color_pending = self.auto_color.get()
        self.frame = None
        self.clear_hints()

    def toggle_auto(self):
        if self.auto_mode:
            self.auto_mode = False
            self.auto_generation += 1
            self.region = None
            self.clear_hints()
            self.start_button.configure(text='▶   Начать')
            self.status.set('Остановлено. Нажми «Начать», чтобы снова найти доску.')
        else:
            self.start_auto()

    def start_auto(self):
        self.auto_mode = True
        self.auto_generation += 1
        self.capture_id += 1
        self.region = None
        self.next_scan = 0
        self.mismatch_since = None
        if self.board is None:
            self.board = chess.Board()
        self.auto_color_pending = self.auto_color.get()
        self.prepare_capture()
        self.start_button.configure(text='■   Остановить')
        self.status.set('Ищу доску на экране…')
        self.root.iconify()

    def poll_auto(self):
        if not self.auto_mode:
            return
        now = time.monotonic()
        if self.scan_future is not None and self.scan_future.done():
            job,self.scan_future = self.scan_future,None
            try:
                match = job.result()
            except (OSError,RuntimeError,ValueError):
                match = None
            if match is not None and job.generation == self.auto_generation:
                client = chrome_client(match.browser)
                if client and client[2:] == match.client_size:
                    changed = (self.region is None or self.browser != match.browser
                               or max(abs(a-b) for a,b in zip(self.region,match.region)) > 3
                               or self.client_size != match.client_size)
                    if changed:
                        first = self.region is None
                        self.clear_hints()
                        self.browser,self.client_size = match.browser,match.client_size
                        self.region,self.moves_region = match.region,match.moves_region
                        self.capture_id += 1
                        if self.ocr_job:
                            self.ocr_job.cancel()
                            self.ocr_job = None
                        self.prepare_capture()
                        self.mismatch_since = None
                        if first:
                            self.auto_color_pending = self.auto_color.get()
                    elif match.moves_region is not None and match.moves_region != self.moves_region:
                        self.moves_region = match.moves_region
                        self.capture_id += 1
                        if self.ocr_job:
                            self.ocr_job.cancel()
                            self.ocr_job = None
                        self.ocr_image = self.ocr_board_frame = self.visible_frame = None
                        self.visible_count = 0
                        self.next_ocr = 0
            if job.generation == self.auto_generation:
                self.next_scan = now+(2.5 if self.region else .5)
        if self.scan_future is None and now >= self.next_scan:
            self.overlay.before_capture()
            self.scan_future = self.scan_executor.submit(discover_screen)
            self.scan_future.generation = self.auto_generation
            self.next_scan = now+(2.5 if self.region else .5)

    def search_again(self):
        self.clear_hints()
        self.region = None
        self.next_scan = 0
        self.status.set('Ищу доску на экране…')

    def preview_changed(self):
        if self.preview_enabled.get():
            self.board_frame.pack(after=self.preview_anchor)
        else:
            self.board_frame.pack_forget()

    def overlay_changed(self):
        self.overlay.hide()

    def clear_hints(self):
        self.arrow = self.reply = None
        self.last_requested = None
        self.analysis_error = ""
        self.analysis_depth = 0
        self.analysis.cancel()
        self.overlay.hide()
        self.paint_key = None
        self.canvas.delete("hint")

    def show_themes(self):
        LIBRARY.load()
        dialog = tk.Toplevel(self.root)
        dialog.title("Распознавание оформления")
        dialog.transient(self.root)
        dialog.configure(bg="#101614",padx=18,pady=16)
        tk.Label(dialog,text="Профили оформления",font=("Segoe UI",15,"bold"),
                 bg="#101614",fg="#eff7e8").pack(anchor="w")
        text = (
            "Встроены обычные и рисованные фигуры.\n"
            f"Сохранено локальных профилей: {len(LIBRARY.profiles)}.\n\n"
            "Чтобы запомнить новое оформление, начни захват на стартовой доске "
            "или после первых одного–двух ходов. Тренер сохранит образцы фигур "
            "на текущем фоне доски автоматически.\n\n"
            "Новая тема в середине партии может потребовать начала новой партии "
            "для настройки. При неуверенном распознавании подсказка скрывается."
        )
        tk.Label(dialog,text=text,wraplength=320,justify="left",font=("Segoe UI",10),
                 bg="#101614",fg="#b9c9b7").pack(anchor="w",pady=(10,0))
        tk.Label(dialog,text=str(LIBRARY.directory),wraplength=320,justify="left",
                 font=("Segoe UI",8),bg="#101614",fg="#8fa38c").pack(anchor="w",pady=(12,0))

    def draw_placeholder(self):
        size = self.board_size
        self.canvas.delete("all")
        for row in range(8):
            for col in range(8):
                cell = size/8
                color = "#1e2d23" if (row+col)%2 else "#26372a"
                self.canvas.create_rectangle(col*cell, row*cell, (col+1)*cell, (row+1)*cell,
                                             fill=color, outline="")
        self.canvas.create_rectangle(0, size*.29, size, size*.72, fill="#17231b", outline="")
        self.canvas.create_text(size/2, size*.45, text="♞", fill="#9ab978",
                                font=("Segoe UI Symbol", 47))
        self.canvas.create_text(size/2, size*.64, text="Здесь появится твоя доска",
                                fill="#a6b69e", font=("Segoe UI", 10))

    def update_summary(self, *_args):
        text = self.status.get()
        if text.startswith("Подсказка:"):
            self.phase.set("РЕКОМЕНДУЕМЫЙ ХОД")
            if self.arrow and self.board:
                move = self.arrow
                self.move_heading.set(f"{chess.square_name(move.from_square)}  →  {chess.square_name(move.to_square)}")
                self.move_caption.set(hint_text(self.board, move).removeprefix("Подсказка: "))
                if self.reply:
                    position = self.board.copy(stack=False)
                    position.push(move)
                    if self.reply in position.legal_moves:
                        self.move_caption.set(f"Ответ: {position.san(self.reply)} · по варианту движка")
            else:
                self.move_heading.set(text.removeprefix("Подсказка: "))
                self.move_caption.set("Рекомендация движка")
            self.status_dot.configure(fg="#c0ef86")
        else:
            self.status_dot.configure(fg="#8b9e91")
            if text.startswith("Ход противника:"):
                heading, caption, phase = text, "Ожидаемый ход по расчёту движка", "ХОД ПРОТИВНИКА"
                if self.analysis_depth:
                    caption += f" · глубина {self.analysis_depth}"
            elif "Считаю" in text:
                heading, caption, phase = "Считаю ход…", "Анализ текущей позиции", "ТВОЙ ХОД"
            elif text.startswith('Ищу доску'):
                heading, caption, phase = 'Ищу доску…', 'Открой видимую доску в Chrome. Области выбираются автоматически.', 'ПОИСК ПО ЭКРАНУ'
            elif text.startswith('Остановлено'):
                heading, caption, phase = 'Остановлено', 'Нажми «Начать», чтобы продолжить', 'ЗАХВАТ ВЫКЛЮЧЕН'
            elif text == "Партия завершена":
                heading, caption, phase = "Партия окончена", "Ходы доступны в истории", "ГОТОВО"
            elif text.startswith("Пауза"):
                heading, caption, phase = "Пауза", "Вернись в выбранное окно Chrome", "ЗАХВАТ ПРИОСТАНОВЛЕН"
            elif self.region:
                heading, caption, phase = "Сверяю доску", "Проверяю позицию перед подсказкой", "ЗАХВАТ АКТИВЕН"
            else:
                heading, caption, phase = "Готов к поиску", "Открой шахматы и нажми «Начать»", "ГОТОВ К ЗАХВАТУ"
            self.move_heading.set(heading)
            self.move_caption.set(caption)
            self.phase.set(phase)

    def player_changed(self, manual=True):
        if manual:
            self.auto_color.set(False)
            self.auto_color_pending = False
        self.clear_hints()
        self.canvas.delete("hint")

    def orientation_changed(self, _event=None):
        self.orientation = None
        self.frame = None
        self.pending = None
        self.needs_calibration = True
        self.player_changed(manual=False)

    def prepare_capture(self):
        self.clear_hints()
        self.orientation = None
        self.needs_calibration = True
        self.arrow = self.pending = self.last_requested = None
        self.frame = self.frame_match = self.ocr_image = None
        self.next_ocr = 0
        self.ocr_attempt = self.picture_attempt = None
        self.pieces_frame = None
        self.visible_tokens = {}
        self.visible_frame = None
        self.visible_count = 0
        self.ocr_board_frame = None

        self.match_watcher.reset()
        self.capture_retry_after = 0

    def recognized_pieces(self, image):
        if self.pieces_frame is image:
            return self.frame_pieces
        raw = read_standard_pieces(image, False)
        self.frame_raw_pieces = raw
        orientation_evidence = raw
        if raw is None:
            orientation_evidence = {
                square: chess.Piece(chess.PAWN, tone > 157)
                for square, (shape, tone) in board_features(image, False)
                if shape.bit_count() >= 8*55
            }
        self.orientation = self.resolve_orientation(orientation_evidence)
        if getattr(self,'auto_mode',False) and self.auto_color_pending and self.orientation is not None:
            self.player_black.set(self.orientation)
            self.auto_color_pending = False
        if self.orientation is None:
            return None
        self.pieces_frame = image
        self.frame_pieces = rotate_pieces(raw) if raw is not None and self.orientation else raw
        return self.frame_pieces

    def calibrate_start(self, image):
        if self.board.ply() != 0:
            return False
        features = dict(board_features(image, self.orientation))
        occupied = {square for square, (shape, _) in features.items()
                    if shape.bit_count() >= 8*55}
        expected = self.board.piece_map()
        if occupied != set(expected):
            return False
        white = [features[square][1] for square, piece in expected.items() if piece.color]
        black = [features[square][1] for square, piece in expected.items() if not piece.color]
        if min(white) - max(black) < 30:
            return False
        self.learn_templates(tile_signatures(image, self.orientation))
        self.needs_calibration = False
        return True

    def resolve_orientation(self, raw):
        mode = self.orientation_mode.get()
        if mode == "Белые снизу":
            return False
        if mode == "Чёрные снизу":
            return True
        return board_orientation(raw, self.board, self.orientation)

    def show_history(self):
        try:
            text = self.history.read()
        except OSError as error:
            messagebox.showerror("История ходов", str(error), parent=self.root)
            return
        dialog = tk.Toplevel(self.root)
        dialog.title("История ходов")
        dialog.transient(self.root)
        dialog.configure(bg="#101614", padx=12, pady=12)
        tk.Label(dialog, text=str(self.history.path), bg="#101614", fg="#abbcaf",
                 wraplength=340, justify="left").pack(anchor="w", pady=(0, 8))
        entries = tk.Text(dialog, width=36, height=14, bg="#1a251e", fg="#f3f7f1",
                          font=("Consolas", 12), relief="flat", padx=8, pady=8)
        entries.insert("1.0", text or "История пока пуста.")
        entries.configure(state="disabled")
        entries.pack(fill="both", expand=True)

    def reset_history(self):
        if messagebox.askyesno("Сбросить историю", "Удалить сохранённый список ходов?",
                               parent=self.root):
            try:
                self.history.reset(self.board.ply() if self.region else 0)
                self.history_writable = True
            except OSError as error:
                messagebox.showerror("История ходов", str(error), parent=self.root)

    def save_history(self):
        if not self.history_writable:
            return
        try:
            self.history.record(self.board)
        except OSError:
            self.promotion.set("Не удалось сохранить журнал ходов")

    def archive_match(self):
        try:
            self.history.archive()
            self.history_writable = True
        except OSError:
            # Keep capture running and preserve the old file if archival fails.
            self.history_writable = False

    def detect_new_match(self, image):
        fresh = (self.visible_tokens if self.visible_frame is self.frame
                 and self.visible_count >= 2 else None)
        candidate, waiting = self.match_watcher.observe(
            getattr(self, "frame_raw_pieces", None), self.board,
            self.orientation_mode.get(), time.monotonic(), fresh)
        if waiting:
            self.clear_hints()
            return True
        if candidate is None:
            return False
        self.archive_match()
        self.clear_hints()
        self.capture_id += 1
        if self.ocr_job is not None:
            self.ocr_job.cancel()
            self.ocr_job = None
        self.board = candidate.board
        self.orientation = candidate.flipped
        if getattr(self,'auto_mode',False) and self.auto_color.get():
            self.player_black.set(candidate.flipped)
        self.history.start_ply = 0
        self.history.prefix = ""
        self.ocr_tokens = {}
        # Seed only moves proven by the new board, never the preceding match.
        position = self.board.root()
        for move in self.board.move_stack:
            self.ocr_tokens[position.ply()+1] = read_token(position.san(move))
            position.push(move)
        if not candidate.complete_history:
            self.ocr_tokens = dict(fresh or {})
        self.visible_tokens = {}
        self.visible_frame = self.ocr_board_frame = self.ocr_image = None
        self.visible_count = 0
        self.next_ocr = 0
        self.ocr_attempt = self.picture_attempt = None
        self.pending = None
        self.frame_pieces = self.board.piece_map()
        self.pieces_frame = image
        self.frame_match = (None, 0)
        self.learn_templates(tile_signatures(image, self.orientation))
        self.needs_calibration = False
        self.match_watcher.reset()
        self.save_history()
        note = ("Новый матч распознан автоматически" if candidate.complete_history else
                "Новый матч восстановлен по доске · права рокировки и взятия на проходе неизвестны")
        self.promotion.set(note if self.history_writable else
                           "Новый матч распознан · история недоступна, захват продолжается")
        return False


    def close(self):
        self.closing = True
        self.root.after_cancel(self.timer)
        if self.selection_timer is not None:
            self.root.after_cancel(self.selection_timer)
        self.ocr_executor.shutdown(wait=False, cancel_futures=True)
        self.scan_executor.shutdown(wait=False,cancel_futures=True)
        self.analysis.close()
        self.overlay.close()
        try:
            self.engine.quit()
        finally:
            self.root.destroy()

    def select(self, restart=False):
        self.restart = restart
        self.previous_region = self.region
        self.region = None
        self.player_changed()
        self.capture_id += 1
        self.root.withdraw()
        self.selection_timer = self.root.after(2000, self.show_selection)

    def show_selection(self):
        self.selection_timer = None
        browser = user32.GetForegroundWindow()
        client = chrome_client(browser)
        if not client:
            self.region = self.previous_region
            self.root.deiconify()
            self.status.set("После нажатия кнопки переключись на Chrome в течение двух секунд.")
            return
        x, y, width, height = client
        image = grab((x, y, x+width, y+height))
        window = tk.Toplevel(self.root)
        window.attributes("-fullscreen", True)
        window.attributes("-topmost", True)
        photo = ImageTk.PhotoImage(image)
        canvas = tk.Canvas(window, highlightthickness=0, cursor="crosshair")
        canvas.pack(fill="both", expand=True)
        canvas.create_image(x, y, image=photo, anchor="nw")
        canvas.photo = photo
        origin = []
        board_box = None
        caption = canvas.create_text(
            window.winfo_screenwidth()//2, 42, text="1 / 2   Выдели все 64 клетки доски",
            fill="#c4ff80", font=("Segoe UI", 18, "bold"))
        border = canvas.bbox(caption)
        caption_bg = canvas.create_rectangle(border[0]-16, border[1]-8, border[2]+16,
                                             border[3]+8, fill="#101614", outline="")
        canvas.tag_lower(caption_bg, caption)
        rectangle = canvas.create_rectangle(0,0,0,0,outline="#00eaff",width=3)

        def cancel(_event=None):
            self.region = self.previous_region
            window.destroy()
            self.root.deiconify()

        def press(event):
            origin[:] = [event.x, event.y]

        def drag(event):
            if origin:
                canvas.coords(rectangle, *origin, event.x, event.y)

        def release(event):
            nonlocal board_box
            if not origin:
                return
            left, right = sorted((origin[0]-x, event.x-x))
            top, bottom = sorted((origin[1]-y, event.y-y))
            if not (0 <= left < right <= width and 0 <= top < bottom <= height):
                return
            box = left, top, right, bottom
            if board_box is None:
                if min(right-left, bottom-top) < 160 or abs((right-left)/(bottom-top)-1) > .06:
                    canvas.itemconfigure(caption, text="1 / 2   Выдели квадрат доски без рамки")
                    return
                board_box = box
                canvas.create_rectangle(left+x, top+y, right+x, bottom+y,
                                        outline="#c4ff80", width=3)
                canvas.itemconfigure(caption, text="2 / 2   Обведи таблицу ходов целиком")
                origin.clear()
                return
            if right-left < 160 or bottom-top < 60:
                return
            cancel()
            self.browser, self.client_size = browser, (width, height)
            self.region, self.moves_region = board_box, box
            if self.board is None or self.restart:
                if self.board is not None:
                    self.archive_match()
                self.board = chess.Board()
                self.history.start_ply = 0
                self.history.prefix = ""
                self.ocr_tokens = {}
                self.promotion.set("")
            self.prepare_capture()
            # The taskbar button restores settings; hints remain on Chrome.
            self.root.iconify()

        window.bind("<Escape>", cancel)
        canvas.bind("<Button-1>", press)
        canvas.bind("<B1-Motion>", drag)
        canvas.bind("<ButtonRelease-1>", release)
        window.focus_force()

    def learn_templates(self, samples):
        self.templates = {}
        pieces = signature(self.board)
        for square, tile in samples.items():
            self.templates.setdefault(pieces.get(square, "."), []).append(tile)

    def saved_prefix(self):
        # A restarted trainer may still have the beginning of the current game.
        # Use it only when several visible moves agree with the saved history.
        board = chess.Board()
        prefix = {}
        overlap = 0
        try:
            for line in self.history.read().splitlines():
                number, _, san = line.partition(" ")
                expected = f"{board.fullmove_number}." if board.turn else f"{board.fullmove_number}…"
                if number != expected:
                    return {}
                move = board.parse_san(san)
                ply = board.ply()+1
                token = self.ocr_tokens.get(ply)
                if token is not None:
                    if move not in matching_moves(board, token):
                        return {}
                    overlap += 1
                prefix[ply] = read_token(san)
                board.push(move)
        except (ValueError, OSError):
            return {}
        return prefix if overlap >= 4 else {}

    def recovery_status(self):
        if not self.ocr_tokens:
            if self.ocr_job:
                return "Читаю список ходов…"
            if self.frame_pieces is not None:
                return "Фигуры прочитаны · проверь область списка ходов"
            return "Не все фигуры читаются · проверь тему и область доски"
        if self.board.ply() + 1 < min(self.ocr_tokens):
            return f"Прокрути список к ходу {self.board.ply()//2+1}"
        if self.frame_pieces is not None:
            return "Фигуры прочитаны · восстанавливаю порядок ходов"
        return "Не все фигуры читаются · дождись окончания анимации"

    def tile_costs(self, image):
        return {
            square: {key: min(distance(sample, template) for template in templates)
                     for key, templates in self.templates.items()}
            for square, sample in tile_signatures(image, self.orientation).items()
        }

    def update_ocr(self, client):
        if self.moves_region is None:
            return
        if self.ocr_job and self.ocr_job.done():
            job, self.ocr_job = self.ocr_job, None
            try:
                tokens = job.result()
            except RuntimeError:
                tokens = {}
            if job.capture_id == self.capture_id:
                self.ocr_tokens.update(tokens)
                if job.board_frame is self.frame:
                    self.visible_count = (self.visible_count+1
                                          if self.visible_frame is self.frame and self.visible_tokens == tokens
                                          else 1)
                    self.visible_tokens = tokens
                    self.visible_frame = self.frame
                    self.ocr_attempt = self.picture_attempt = None
        now = time.monotonic()
        if self.ocr_job or now < self.next_ocr or now-self.frame_changed < .10:
            return
        self.next_ocr = now + .15
        x, y, _, _ = client
        left, top, right, bottom = self.moves_region
        moves_image = grab((x+left, y+top, x+right, y+bottom))
        if user32.GetForegroundWindow() != self.browser:
            return
        if (self.ocr_board_frame is self.frame and self.visible_count >= 2
                and self.ocr_image is not None
                and not ImageChops.difference(moves_image, self.ocr_image).getbbox()):
            return
        self.ocr_image = moves_image
        self.ocr_board_frame = self.frame
        self.ocr_job = self.ocr_executor.submit(scan_move_list, moves_image)
        self.ocr_job.capture_id = self.capture_id
        self.ocr_job.board_frame = self.frame

    def recover_from_list(self, image):
        if not self.ocr_tokens or self.ocr_attempt is self.frame:
            return None
        self.ocr_attempt = self.frame
        costs = self.tile_costs(image)
        best = sum(min(tile.values()) for tile in costs.values())

        def error(board):
            pieces = signature(board)
            return sum(costs[square].get(pieces.get(square, "."), 1.0) for square in chess.SQUARES) - best

        return recover_position(self.board, self.ocr_tokens, error, POSITION_TOLERANCE + .2)

    def calibrate_from_moves(self, image):
        if not self.ocr_tokens or self.ocr_attempt is self.frame:
            return False
        self.ocr_attempt = self.frame
        samples = tile_signatures(image, self.orientation)
        occupied = occupied_squares(samples)
        tokens = {**self.saved_prefix(), **self.ocr_tokens}

        def mismatch(board):
            return len(set(signature(board)) ^ occupied)

        board = recover_position(self.board, tokens, mismatch, 0)
        if board is None:
            return False
        self.board = board
        self.learn_templates(samples)
        self.needs_calibration = False
        self.save_history()
        return True

    def restore_from_picture(self, image):
        key = self.frame, max(self.ocr_tokens, default=0)
        if self.picture_attempt == key:
            return False
        self.picture_attempt = key
        pieces = self.recognized_pieces(image)
        if not pieces:
            return False
        if pieces == self.board.piece_map():
            self.learn_templates(tile_signatures(image, self.orientation))
            self.needs_calibration = False
            return True

        previous_ply = self.board.ply()
        recovered = recover_position(
            self.board, self.ocr_tokens,
            lambda board: 0 if board.piece_map() == pieces else 1, 0,
        )
        if recovered is None:
            sequence = sequence_for_pieces(self.board, pieces)
            if sequence is not None:
                recovered = self.board.copy()
                for move in sequence:
                    recovered.push(move)
        if recovered is None:
            if self.visible_frame is not self.frame or self.visible_count < 2:
                return False
            latest = max(self.visible_tokens, default=0)
            if latest <= previous_ply:
                return False
            token = self.visible_tokens[latest]
            if not isinstance(token, tuple) or token[0][1] == "?":
                return False
            piece = pieces.get(chess.parse_square(token[0]))
            turn = latest % 2 == 0
            if not piece or piece.color == turn:
                return False
            recovered = chess.Board(None)
            recovered.set_piece_map(pieces)
            recovered.turn = turn
            recovered.fullmove_number = latest//2 + 1
            if not recovered.is_valid():
                return False
            if self.board.move_stack:
                self.save_history()
                try:
                    self.history.prefix = self.history.read()
                except OSError:
                    pass
            self.promotion.set("Восстановлено по доске · без рокировки и взятия на проходе")
        else:
            first_new = max(0, len(recovered.move_stack) - (recovered.ply() - previous_ply))
            for move in recovered.move_stack[first_new:]:
                self.show_promotion(move)
        self.board = recovered
        self.learn_templates(tile_signatures(image, self.orientation))
        self.needs_calibration = False
        self.arrow = self.pending = self.last_requested = None
        self.save_history()
        return True

    def show_promotion(self, move):
        if move.promotion:
            square = chess.square_name(move.to_square)
            self.promotion.set(f"Превращение: {square} → {PROMOTION_NAMES[move.promotion]}")

    def read_move(self, image):
        pieces = self.recognized_pieces(image)
        if pieces is not None:
            sequence = sequence_for_pieces(self.board, pieces)
            return (sequence or None, 0) if sequence is not None else (None, POSITION_TOLERANCE+1)
        costs = self.tile_costs(image)
        current = signature(self.board)
        # Measure only the mismatch that another piece template explains better.
        best_tile_cost = {square: min(options.values()) for square, options in costs.items()}
        baseline = sum(costs[square].get(current.get(square, "."), 1.0) - best_tile_cost[square]
                       for square in chess.SQUARES)


        def search(two_plies):
            best = None
            best_score = runner_up = 0

            def consider(sequence):
                nonlocal best, best_score, runner_up
                position = signature(self.board)
                gains = [costs[square].get(current.get(square,"."), 1.0) - costs[square].get(position.get(square,"."), 1.0)
                         for square in current.keys() | position.keys()
                         if current.get(square) != position.get(square)]
                if not gains or min(gains) <= .035:
                    return
                score = sum(gains)
                if baseline - score > POSITION_TOLERANCE:
                    return
                if score > best_score:
                    best, best_score, runner_up = sequence, score, best_score
                else:
                    runner_up = max(runner_up, score)

            for first in list(self.board.legal_moves):
                self.board.push(first)
                try:
                    if two_plies:
                        for second in list(self.board.legal_moves):
                            self.board.push(second)
                            try:
                                consider((first, second))
                            finally:
                                self.board.pop()
                    else:
                        consider((first,))
                finally:
                    self.board.pop()
            return (best, best_score) if best_score-runner_up > .025 else (None, 0)

        single, score = search(False)
        if baseline-score > .27:
            pair, pair_score = search(True)
            if pair and pair_score > score+.15:
                return pair, baseline
        return single, baseline

    def paint(self, image, show_arrow):
        pv = tuple(move for move in (self.arrow, self.reply) if move is not None)
        arrows = displayed_moves(self.board, pv, not self.player_black.get()) if show_arrow else []
        if self.overlay_enabled.get() and self.board_bounds and arrows:
            self.overlay.show(self.board_bounds, arrows, self.orientation)
        else:
            self.overlay.hide()
        key = (tuple(arrows), self.orientation)
        if self.paint_image is image and self.paint_key == key:
            return
        self.paint_image, self.paint_key = image, key
        self.preview = ImageTk.PhotoImage(image.resize((self.board_size, self.board_size)))
        self.canvas.delete("all")
        self.canvas.create_image(0,0,anchor="nw",image=self.preview)
        paint_arrows(self.canvas, self.board_size, self.board_size, arrows,
                     self.orientation, labels=False)

    def tick(self):
        try:
            self.poll_auto()
            if not self.region:
                self.overlay.hide()
                if self.auto_mode:
                    self.status.set('Ищу доску на экране… Открой видимую доску в Chrome.')
                return
            if time.monotonic() < self.capture_retry_after:
                return
            if user32.GetForegroundWindow() != self.browser:
                if self.auto_mode and not user32.IsWindow(self.browser):
                    self.search_again()
                    return
                self.pending = None
                self.clear_hints()
                self.canvas.delete("hint")
                self.status.set("Пауза. Вернись в выбранное окно Chrome.")
                return
            client = chrome_client(self.browser)
            if not client or client[2:] != self.client_size:
                self.clear_hints()
                self.canvas.delete("hint")
                if self.auto_mode:
                    self.search_again()
                else:
                    self.status.set("Верни прежний размер окна Chrome.")
                return
            x,y,_,_ = client
            left,top,right,bottom = self.region
            self.board_bounds = (x+left,y+top,x+right,y+bottom)
            self.overlay.before_capture()
            image = grab(self.board_bounds)
            if user32.GetForegroundWindow() != self.browser:
                self.clear_hints()
                return
            if self.frame is None or not same_board_frame(image,self.frame):
                self.clear_hints()
                self.frame = image
                self.frame_match = None
                self.frame_changed = time.monotonic()
                self.pieces_frame = None
                self.visible_frame = None
                self.visible_count = 0
            image = self.frame
            self.update_ocr(client)
            if time.monotonic()-self.frame_changed < .08:
                self.paint(image, False)
                self.status.set("Дожидаюсь завершения хода…")
                return
            self.recognized_pieces(image)
            if self.detect_new_match(image):
                self.status.set("Распознаю новый матч…")
                self.paint(image, False)
                return
            known = getattr(self,'frame_raw_pieces',None) is not None
            if self.auto_mode and not known:
                if self.mismatch_since is None:
                    self.mismatch_since = time.monotonic()
                elif time.monotonic()-self.mismatch_since > 1.1:
                    self.search_again()
                    return
            else:
                self.mismatch_since = None
            if self.orientation is None:
                self.status.set("Выбери поворот доски в настройках")
                self.paint(image, False)
                return
            if (self.needs_calibration and not self.restore_from_picture(image)
                    and not self.calibrate_start(image) and not self.calibrate_from_moves(image)):
                self.status.set(self.recovery_status())
                self.paint(image, False)
                return
            if self.frame_match is None:
                self.frame_match = self.read_move(image)
            sequence, error = self.frame_match
            if not sequence and error > POSITION_TOLERANCE:
                recovered = self.recover_from_list(image)
                if recovered:
                    previous_ply = self.board.ply()
                    for move in recovered.move_stack[max(0, previous_ply-1):]:
                        self.show_promotion(move)
                    self.board = recovered
                    self.save_history()
                    self.arrow = self.pending = self.last_requested = None
                    self.frame = None
                    error = 0
                elif self.restore_from_picture(image):
                    self.frame_match = (None, 0)
                    error = 0
            if sequence and sequence == self.pending:
                for move in sequence:
                    self.board.push(move)
                    self.show_promotion(move)
                self.save_history()
                self.clear_hints()
                self.pending = None
                self.frame_match = (None, 0)
                sequence = None
            else:
                self.pending = sequence
            if self.board.is_game_over():
                status = "Партия завершена"
            elif sequence:
                status = "Подтверждаю ход…"
            elif error > POSITION_TOLERANCE:
                status = self.recovery_status()
            else:
                self.analysis.request(self.board, not self.player_black.get(), self.capture_id)
                result = self.analysis.poll()
                if result:
                    self.analysis_error = result.error
                    if result.pv:
                        self.arrow = result.pv[0]
                        self.reply = result.pv[1] if len(result.pv) > 1 else None
                        self.analysis_depth = result.depth
                if self.analysis_error:
                    status = "Движок: " + self.analysis_error
                elif self.board.turn != (not self.player_black.get()):
                    status = ("Ход противника: " + self.board.san(self.arrow)
                              if self.arrow else "Считаю ответ противника…")
                else:
                    status = hint_text(self.board, self.arrow) if self.arrow else "Считаю ход…"
            self.status.set(status)
            self.paint(image, not sequence and error <= POSITION_TOLERANCE
                       and not self.board.is_game_over() and not self.analysis_error)
        except (OSError, chess.engine.EngineError) as error:
            self.clear_hints()
            self.capture_retry_after = time.monotonic()+.5
            self.status.set("Повторяю захват… " + str(error))
        finally:
            if not self.closing:
                self.timer = self.root.after(33, self.tick)

def main():
    user32.SetProcessDPIAware()
    root = tk.Tk()
    root.withdraw()
    path = find_engine()
    if not path:
        path = filedialog.askopenfilename(title="Первый запуск: выбери Stockfish",filetypes=[("Stockfish","*.exe")])
    if not path:
        root.destroy()
        return
    try:
        engine = chess.engine.SimpleEngine.popen_uci([str(path)],timeout=5,creationflags=0x08000000)
        options = {"Threads": max(1, min(4, (os.cpu_count() or 2)//2)), "Hash": 64}
        engine.configure({name: value for name, value in options.items() if name in engine.options})
        try:
            remember_engine(path)
        except OSError:
            pass
    except (OSError, chess.engine.EngineError) as error:
        messagebox.showerror("Stockfish",str(error))
        root.destroy()
        return
    Trainer(root,engine)
    root.deiconify()
    root.mainloop()

if __name__ == "__main__":
    main()

