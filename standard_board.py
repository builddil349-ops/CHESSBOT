"""Read the standard Chess.com pieces from silhouettes in screen pixels."""
from statistics import median

import chess
from PIL import Image, ImageDraw, ImageFilter

from standard_templates import TEMPLATES
from illustrated_templates import ILLUSTRATED_TEMPLATES
from adaptive_themes import LIBRARY

SIZE = 36

def tile_features(tile, mode="standard"):
    tile = tile.convert("RGB").resize((SIZE, SIZE))
    pixels = tile.load()
    corners = [pixels[x, y] for x, y in ((2, 2), (33, 2), (2, 33), (33, 33), (2, 18), (33, 18))]
    background = tuple(median([p[i] for p in corners]) for i in range(3))
    # Ignore file and rank labels in the extreme corners.
    mask = Image.new("L", (SIZE, SIZE))
    def foreground(x, y):
        if not (3 <= x < 33 and 2 <= y < 34):
            return False
        red, green, blue = pixels[x, y]
        if mode == "illustrated":
            # Purple outlines separate these sprites from the blue board texture,
            # grey legal-move dots, and turquoise selection highlights.
            return (blue-green > 40 and blue-red > 25 and red-green > -12) or (red-green > 20 and blue-green > 8)
        return max(abs(a-b) for a, b in zip(pixels[x, y], background)) > 34

    mask.putdata([255 if foreground(x,y) else 0 for y in range(SIZE) for x in range(SIZE)])
    colored = [pixels[x,y] for y in range(2,34) for x in range(3,33) if mask.getpixel((x,y))]
    ImageDraw.floodfill(mask, (0, 0), 128)
    mask = mask.point(lambda value: 0 if value == 128 else 255)
    if mode == "illustrated":
        mask = mask.filter(ImageFilter.MaxFilter(3))
    bitmask = int.from_bytes(mask.tobytes(), "little")
    if not bitmask:
        return 0, 0
    interior = mask.filter(ImageFilter.MinFilter(5))
    tones = [(pixel[0] if mode == "illustrated" else sum(pixel)/3)
             for pixel, inside in zip(tile.getdata(), interior.getdata()) if inside]
    if mode == "illustrated":
        pink = sum(red > 150 and red-green > 25 and blue-green > 10
                   for red, green, blue in colored)
        tone = 200 if colored and pink/len(colored) > .45 else 80
    else:
        tone = median(tones) if tones else 0
    return bitmask, tone


def board_features(image, flipped, mode="standard"):
    width, height = image.size
    for square in chess.SQUARES:
        file, rank = chess.square_file(square), chess.square_rank(square)
        col, row = (7-file, rank) if flipped else (file, 7-rank)
        box = (round(col*width/8), round(row*height/8),
               round((col+1)*width/8), round((row+1)*height/8))
        yield square, tile_features(image.crop(box), mode)

def classify_tile(shape, tone, templates=TEMPLATES):
    if shape.bit_count() < 8*55:
        return "."
    color = "white" if tone > 157 else "black"
    scores = []
    for symbol, examples in templates.items():
        if symbol.isupper() != (color == "white"):
            continue
        score = max((shape & sample).bit_count()/(shape | sample).bit_count() for sample in examples)
        scores.append((score, symbol))
    scores.sort(reverse=True)
    if scores[0][0] < .72 or scores[0][0] - scores[1][0] < .045:
        return None
    return scores[0][1]

def read_standard_pieces(image, flipped):
    for mode, templates in (("standard", TEMPLATES), ("illustrated", ILLUSTRATED_TEMPLATES)):
        pieces = {}
        for square, (shape, tone) in board_features(image, flipped, mode):
            symbol = classify_tile(shape, tone, templates)
            if symbol is None:
                break
            if symbol != ".":
                pieces[square] = chess.Piece.from_symbol(symbol)
        else:
            if all(sum(piece == chess.Piece(chess.KING,color) for piece in pieces.values()) == 1
                   for color in chess.COLORS):
                return pieces
    return LIBRARY.read(image, flipped)

