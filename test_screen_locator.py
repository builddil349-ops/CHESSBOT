from pathlib import Path
from tempfile import TemporaryDirectory
import sys
import time
import unittest
from unittest.mock import patch
from types import SimpleNamespace

import chess
from PIL import Image, ImageDraw

from engine_setup import find_engine, remember_engine
from screen_locator import locate_boards
from screen_ocr import move_list_from_words


def board_scene(size=(1000,760), box=(90,110,634,654), colors=('#789950','#efeacf')):
    image = Image.new('RGB',size,'#292929')
    draw = ImageDraw.Draw(image)
    x,y,right,bottom = box
    for row in range(8):
        for col in range(8):
            draw.rectangle((round(x+col*(right-x)/8),round(y+row*(bottom-y)/8),
                            round(x+(col+1)*(right-x)/8)-1,round(y+(row+1)*(bottom-y)/8)-1),
                           fill=colors[(row+col)%2])
    return image


class LocatorTests(unittest.TestCase):
    def test_board_bounds_at_different_scales_and_palettes(self):
        for box,colors in (((90,110,634,654),('#789950','#efeacf')),
                           ((40,95,360,415),('#508fa5','#fff1e5')),
                           ((310,70,710,470),('#626262','#d4d4d4'))):
            with self.subTest(box=box):
                candidates = locate_boards(board_scene(box=box,colors=colors))
                self.assertTrue(candidates)
                self.assertLessEqual(max(abs(a-b) for a,b in zip(candidates[0].box,box)),3)

    def test_empty_screen_and_striped_tables_do_not_look_like_a_board(self):
        empty = Image.new('RGB',(800,600),'#202020')
        self.assertFalse(locate_boards(empty))
        draw = ImageDraw.Draw(empty)
        for y in range(100,500,40):
            draw.rectangle((40,y,700,y+19),fill='#dddddd')
        self.assertFalse(locate_boards(empty))

    def test_main_board_is_preferred_over_a_preview(self):
        image = board_scene(size=(1400,850),box=(40,140,600,700))
        preview = board_scene(size=(240,240),box=(0,0,240,240))
        image.paste(preview,(1050,80))
        found = locate_boards(image)
        self.assertTrue(found)
        self.assertGreater(found[0].box[2]-found[0].box[0],500)

    def test_move_table_requires_aligned_consecutive_numbers_and_moves(self):
        board = (35,83,775,823)
        words = [('650',900,120,25,15),('1',835,450,12,12),('e4',900,450,22,12),
                 ('e5',1030,450,22,12),('2',835,487,12,12),('Nf3',900,487,30,15),
                 ('Nc6',1030,487,30,15)]
        box = move_list_from_words(words,board,(1600,900))
        self.assertIsNotNone(box)
        self.assertLess(box[0],835)
        self.assertGreater(box[2],1060)
        self.assertLess(box[1],450)
        self.assertIsNone(move_list_from_words([('650',900,120,25,15)],board,(1600,900)))


class EngineSetupTests(unittest.TestCase):
    def test_neighbouring_engine_and_remembered_choice(self):
        with TemporaryDirectory() as folder:
            base = Path(folder)
            engine = base/'Stockfish-18-Windows'/'stockfish'/'stockfish-sse41-popcnt.exe'
            engine.parent.mkdir(parents=True)
            engine.write_bytes(b'test fixture only')
            settings = base/'state'/'settings.json'
            self.assertEqual(find_engine(base,settings),engine)
            remember_engine(engine,settings)
            self.assertEqual(find_engine(base/'different-folder',settings),engine)
            settings.write_text('invalid json',encoding='utf-8')
            self.assertEqual(find_engine(base,settings),engine)


@unittest.skipUnless(sys.platform == 'win32','Windows capture integration')
class AutoWorkflowTests(unittest.TestCase):
    def test_one_button_finds_board_relocates_and_stops(self):
        import tkinter as tk
        import trainer
        with TemporaryDirectory() as folder:
            root = tk.Tk()
            root.withdraw()
            engine = SimpleNamespace(play=lambda board,limit:SimpleNamespace(move=next(iter(board.legal_moves))),
                                      quit=lambda:None)
            app = trainer.Trainer(root,engine)
            app.history = trainer.MoveHistory(Path(folder)/'moves.txt')
            match = [None]
            calls = []
            image = board_scene().crop((90,110,634,654))
            def pump(condition):
                deadline = time.monotonic()+4
                while not condition() and time.monotonic() < deadline:
                    root.update()
                    time.sleep(.01)
                self.assertTrue(condition())
            try:
                with patch.object(trainer,'discover_screen',side_effect=lambda:(calls.append(1) or match[0])), \
                        patch.object(trainer,'chrome_client',return_value=(0,0,1000,760)), \
                        patch.object(trainer,'user32',SimpleNamespace(GetForegroundWindow=lambda:1,IsWindow=lambda _:True)), \
                        patch.object(trainer,'grab',return_value=image), \
                        patch.object(trainer,'read_standard_pieces',return_value=chess.Board().piece_map()):
                    app.start_auto()
                    pump(lambda:len(calls) >= 1)
                    self.assertTrue(app.auto_mode)
                    self.assertIsNone(app.region)
                    match[0] = trainer.ScreenMatch(1,(1000,760),(90,110,634,654),None)
                    app.next_scan = 0
                    pump(lambda:app.region is not None and app.arrow is not None)
                    self.assertTrue(app.overlay.visible)
                    self.assertIn('Остановить',app.start_button.cget('text'))
                    match[0] = trainer.ScreenMatch(1,(1000,760),(190,110,734,654),None)
                    app.next_scan = 0
                    pump(lambda:app.region == match[0].region and app.arrow is not None)
                    app.toggle_auto()
                    self.assertFalse(app.auto_mode)
                    self.assertIsNone(app.region)
                    self.assertFalse(app.overlay.visible)
            finally:
                app.close()

    def test_negative_monitor_origin_and_piece_validation(self):
        import trainer
        image = board_scene()
        point = []
        native = SimpleNamespace(WindowFromPoint=lambda p:point.append((p.x,p.y)) or 1,
                                 GetAncestor=lambda hwnd,flag:1)
        with patch.object(trainer,'capture_desktop',return_value=(image,(-1000,-200))), \
                patch.object(trainer,'user32',native), \
                patch.object(trainer,'chrome_client',return_value=(-1000,-200,1000,760)), \
                patch.object(trainer,'locate_move_list',return_value=None), \
                patch.object(trainer,'read_standard_pieces',return_value=chess.Board().piece_map()):
            found = trainer.discover_screen()
            self.assertIsNotNone(found)
            self.assertLessEqual(max(abs(a-b) for a,b in zip(found.region,(90,110,634,654))),3)
            self.assertTrue(point[0][0] < 0)
        with patch.object(trainer,'capture_desktop',return_value=(image,(0,0))), \
                patch.object(trainer,'user32',native), \
                patch.object(trainer,'chrome_client',return_value=(0,0,1000,760)), \
                patch.object(trainer,'read_standard_pieces',return_value=None):
            self.assertIsNone(trainer.discover_screen())


if __name__ == '__main__':
    unittest.main()
