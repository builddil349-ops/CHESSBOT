"""Stream legal engine variations without blocking the capture/UI thread."""
from collections import OrderedDict
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, field
from queue import Empty, SimpleQueue
from threading import Event, Lock
import time

import chess.engine


def position_key(board):
    return board.fen(), tuple(board.move_stack)


def legal_line(board, moves):
    position = board.copy(stack=False)
    line = []
    for move in moves:
        if move not in position.legal_moves:
            break
        line.append(move)
        position.push(move)
    return tuple(line)


@dataclass(frozen=True)
class AnalysisUpdate:
    key: tuple
    pv: tuple
    depth: int = 0
    elapsed: float = 0
    cached: bool = False
    error: str = ""


@dataclass
class _Search:
    key: tuple
    board: object
    cancel: Event = field(default_factory=Event)
    stream: object = None
    future: object = None


class LiveAnalysis:
    def __init__(self, engine, budget=.9):
        self.engine, self.budget = engine, budget
        self.executor = ThreadPoolExecutor(max_workers=1, thread_name_prefix="chess-analysis")
        self.updates = SimpleQueue()
        self.lock = Lock()
        self.current = None
        self.cache = OrderedDict()

    def cancel(self):
        with self.lock:
            search, self.current = self.current, None
            if search is None:
                return
            search.cancel.set()
            stream = search.stream
            if search.future:
                search.future.cancel()
        if stream is not None:
            try:
                stream.stop()
            except (chess.engine.EngineError, RuntimeError):
                pass

    def request(self, board, player_color, capture_id):
        key = position_key(board), player_color, capture_id
        if self.current is not None and self.current.key == key:
            return
        self.cancel()
        search = _Search(key, board.copy())
        self.current = search
        cached = self.cache.get(key[0])
        if cached:
            self.updates.put(AnalysisUpdate(key, legal_line(board, cached.pv),
                                            cached.depth, cached=True))
        search.future = self.executor.submit(self._run, search)

    def _run(self, search):
        if search.cancel.is_set():
            return
        started = time.monotonic()
        try:
            # All supported UCI engines use this path. The fallback also permits
            # small test engines and UCI adapters which only implement play().
            if not hasattr(self.engine, "analysis"):
                result = self.engine.play(search.board, chess.engine.Limit(time=.06))
                line = [result.move] if result.move else []
                if getattr(result, "ponder", None):
                    line.append(result.ponder)
                if not search.cancel.is_set():
                    self.updates.put(AnalysisUpdate(search.key, legal_line(search.board, line)))
                return
            with self.engine.analysis(search.board, chess.engine.Limit(time=self.budget),
                                      info=chess.engine.INFO_BASIC | chess.engine.INFO_PV) as stream:
                with self.lock:
                    search.stream = stream
                if search.cancel.is_set():
                    stream.stop()
                    return
                last = None
                for info in stream:
                    if search.cancel.is_set():
                        break
                    pv = legal_line(search.board, info.get("pv", ()))
                    if not pv:
                        continue
                    depth = info.get("depth", 0)
                    last = AnalysisUpdate(search.key, pv, depth, time.monotonic()-started)
                    # Avoid displaying the unstable one-ply startup variation.
                    if depth >= 6:
                        self.updates.put(last)
                if last is not None and not search.cancel.is_set():
                    self.updates.put(last)
        except (OSError, chess.engine.EngineError, RuntimeError) as error:
            if not search.cancel.is_set():
                self.updates.put(AnalysisUpdate(search.key, (), error=str(error)))

    def poll(self):
        latest = None
        while True:
            try:
                update = self.updates.get_nowait()
            except Empty:
                break
            if self.current is not None and update.key == self.current.key:
                latest = update
        if latest is not None and latest.pv and not latest.cached:
            position = self.current.board.copy()
            for offset in range(min(3, len(latest.pv))):
                key = position_key(position)
                self.cache[key] = AnalysisUpdate((), latest.pv[offset:],
                                                  max(0, latest.depth-offset), cached=offset > 0)
                self.cache.move_to_end(key)
                if len(self.cache) > 32:
                    self.cache.popitem(last=False)
                position.push(latest.pv[offset])
        return latest

    def close(self):
        self.cancel()
        self.executor.shutdown(wait=True, cancel_futures=True)
