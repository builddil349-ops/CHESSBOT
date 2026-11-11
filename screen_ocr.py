from collections import Counter
from pathlib import Path
from statistics import median

from PIL import ImageOps
from tesserocr import PSM, PyTessBaseAPI

from move_list import read_token

DATA_DIR = Path(__file__).resolve().parent
MOVE_ALPHABET = "abcdefgh12345678xKQRBNO0=-"

def ink_runs(flags):
    start = None
    for index, flag in enumerate([*flags, False]):
        if flag and start is None:
            start = index
        elif not flag and start is not None:
            yield start, index
            start = None

def cell_text(api, mask, alphabet, strip_icon=False):
    bounds = mask.getbbox()
    if not bounds:
        return ""
    mask = mask.crop(bounds)
    if strip_icon:
        groups = list(ink_runs(mask.getprojection()[0]))
        if len(groups) > 1:
            left, right = groups[0]
            following = groups[1][0]
            if right-left >= mask.height*.64 and following-right >= max(3, mask.height*.2):
                mask = mask.crop((following, 0, mask.width, mask.height))
    scaled = mask.resize((mask.width*4, mask.height*4))
    api.SetVariable("tessedit_char_whitelist", alphabet)
    api.SetImage(ImageOps.expand(ImageOps.invert(scaled), 12, 255))
    return api.GetUTF8Text().strip()

def scan_move_list(image):
    gray = image.convert("L")
    w, h = gray.size
    # Chess.com aligns the move number and both move columns at the left.
    first, second = round(w*.14), round(w*.39)
    reference = gray.crop((0, 0, max(1, round(w*.025)), h))
    background = sorted(reference.getdata())[reference.width*h//2]
    if background < 128:
        mask = gray.point(lambda value: 255 if value >= max(140, background+45) else 0)
        soft_mask = gray.point(lambda value: 255 if value >= max(112, background+45) else 0)
    else:
        mask = gray.point(lambda value: 255 if value <= min(165, background-45) else 0)
        soft_mask = gray.point(lambda value: 255 if value <= min(185, background-45) else 0)
    number_mask = soft_mask.crop((0, 0, first, h))
    rows = [(top, bottom) for top, bottom in ink_runs(number_mask.getprojection()[1])
            if 7 <= bottom-top <= 36]
    tokens = {}
    with PyTessBaseAPI(path=str(DATA_DIR), lang="eng", psm=PSM.SINGLE_LINE) as api:
        if not rows:
            return tokens
        gaps = [b[0]-a[0] for a,b in zip(rows, rows[1:])]
        spacing = median(gaps) if gaps else 1
        offsets = [round((top-rows[0][0])/spacing) for top, _ in rows]
        anchors = Counter()
        for offset, (top, bottom) in zip(offsets, rows):
            number_cell = number_mask.crop((0, top, first, bottom))
            columns = list(ink_runs(number_cell.getprojection()[0]))
            if len(columns) > 1 and columns[-1][1] - columns[-1][0] <= 3:
                number_cell = number_cell.crop((0, 0, columns[-1][0], number_cell.height))
            row = cell_text(api, number_cell, "0123456789")
            if row.isdecimal() and 1 <= int(row)-offset <= 300:
                anchors[int(row)-offset] += 1
        if not anchors:
            return tokens
        origin, votes = anchors.most_common(1)[0]
        if len(rows) > 2 and votes < 2:
            return tokens
        for offset, (top, bottom) in zip(offsets, rows):
            number = origin + offset
            margin = max(3, round((bottom-top)*.42))
            for index, (left, right) in enumerate(((first, second), (second, w))):
                box = (left, max(0, top-margin), right, min(h, bottom+margin))
                cell = mask.crop(box)
                token = read_token(cell_text(api, cell, MOVE_ALPHABET, strip_icon=True))
                if not token:
                    token = read_token(cell_text(api, soft_mask.crop(box), MOVE_ALPHABET, strip_icon=True))
                if not token and cell.getbbox():
                    api.SetPageSegMode(PSM.SINGLE_WORD)
                    token = read_token(cell_text(api, cell, "", strip_icon=True))
                    api.SetPageSegMode(PSM.SINGLE_LINE)
                if token:
                    tokens[2*number-1+index] = token
    return tokens





