from collections import Counter
from pathlib import Path
from statistics import median
import csv
import io
import re
import os
import ctypes
import numpy as np

from PIL import ImageOps
from tesserocr import PSM, PyTessBaseAPI

from move_list import read_token

DATA_DIR = Path(__file__).resolve().parent
MOVE_ALPHABET = "abcdefgh12345678xKQRBNO0=-"


def tessdata_path():
    path = str(DATA_DIR)
    if os.name == 'nt' and not path.isascii():
        # Tesseract's native Windows file API may not accept a UTF-8 pathname.
        # Its DOS alias identifies the same directory without copying the model.
        kernel32 = ctypes.WinDLL('kernel32',use_last_error=True)
        kernel32.GetShortPathNameW.argtypes = (ctypes.c_wchar_p,ctypes.c_wchar_p,ctypes.c_uint32)
        kernel32.GetShortPathNameW.restype = ctypes.c_uint32
        short = ctypes.create_unicode_buffer(32768)
        length = kernel32.GetShortPathNameW(path,short,len(short))
        if 0 < length < len(short):
            return short.value
    return path


def move_list_from_words(words, board_box, image_size):
    """Locate aligned move numbers supported by move-shaped words."""
    left, top, right, bottom = board_box
    size = right-left
    groups = []
    for word in words:
        text, x, y, width, height = word
        match = re.fullmatch(r'(\d{1,3})[.)]?', text.strip())
        if not match or not 1 <= int(match[1]) <= 300:
            continue
        supported = any(read_token(other[0]) and abs(other[2]-y) < max(height,other[4])*.8
                        and x+width+size*.025 < other[1] < x+size*.65 for other in words)
        if not supported:
            continue
        for group in groups:
            if abs(group[0][1]+group[0][3]-(x+width)) <= max(7,size*.012):
                group.append((int(match[1]),x,y,width,height))
                break
        else:
            groups.append([(int(match[1]),x,y,width,height)])
    ranked = []
    for group in groups:
        group.sort(key=lambda item:item[2])
        for start in range(len(group)-1):
            sequence = [group[start]]
            for item in group[start+1:]:
                gap = item[2]-sequence[-1][2]
                if item[0] == sequence[-1][0]+1 and max(12,size*.02) <= gap <= size*.14:
                    sequence.append(item)
            if len(sequence) >= 2:
                ranked.append(sequence)
    if not ranked:
        return None
    group = max(ranked, key=len)
    x = max(0, round(min(item[1] for item in group)-size*.025))
    y = max(top, round(group[0][2]-max(group[0][4]*1.4,size*.18)))
    return x, y, min(image_size[0],round(x+size*.52)), min(image_size[1],bottom)


def locate_move_list(image, board_box, clip=None):
    """Read layout only around the detected board, without page/DOM access."""
    left, top, right, bottom = board_box
    size = right-left
    clip = clip or (0,0,image.width,image.height)
    area = (max(clip[0],right+5), max(clip[1],top),
            min(clip[2],round(right+size*.9)), min(clip[3],bottom))
    if area[2]-area[0] < 100 or area[3]-area[1] < 70:
        return None
    crop = image.crop(area).convert('L')
    background = median(crop.crop((0,0,max(1,crop.width//50),crop.height)).getdata())
    if background < 128:
        mask = crop.point(lambda value: 255 if value >= max(112,background+45) else 0)
    else:
        mask = crop.point(lambda value: 255 if value <= min(185,background-45) else 0)
    prepared = ImageOps.invert(mask).resize((crop.width*2,crop.height*2))
    words = []
    fields = ('level','page_num','block_num','par_num','line_num','word_num',
              'left','top','width','height','conf','text')
    def read_words(tsv, origin):
        for row in csv.DictReader(io.StringIO(tsv), delimiter='\t', fieldnames=fields):
            text = (row.get('text') or '').strip()
            if text and row['level'] == '5':
                words.append((text, round(int(row['left'])/2)+origin[0],
                              round(int(row['top'])/2)+origin[1],
                              round(int(row['width'])/2),round(int(row['height'])/2)))
    with PyTessBaseAPI(path=tessdata_path(), lang='eng', psm=PSM.SPARSE_TEXT) as api:
        api.SetImage(prepared)
        read_words(api.GetTSVText(0),area)
        # Sparse-page OCR discards isolated small move numbers. Find their
        # narrow ink column geometrically, then recognize it as a text block.
        band = np.asarray(mask)[round(mask.height*.5):round(mask.height*.95),
                                :min(mask.width,round(size*.28))]
        flags = np.count_nonzero(band,axis=0) >= max(4,band.shape[0]*.008)
        flags = np.convolve(flags,np.ones(5,dtype=int),mode='same') > 0
        api.SetPageSegMode(PSM.SINGLE_BLOCK)
        api.SetVariable('tessedit_char_whitelist','0123456789.')
        columns = [(a,b) for a,b in ink_runs(flags.tolist()) if 4 <= b-a <= size*.065]
        for a,b in columns[:7]:
            a,b = max(0,a-3),min(mask.width,b+3)
            column = ImageOps.invert(mask.crop((a,0,b,mask.height)))
            api.SetImage(column.resize((column.width*2,column.height*2)))
            read_words(api.GetTSVText(0),(area[0]+a,area[1]))
    box = move_list_from_words(words,board_box,image.size)
    if box:
        return max(box[0],clip[0]),max(box[1],clip[1]),min(box[2],clip[2]),min(box[3],clip[3])
    return None

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
    with PyTessBaseAPI(path=tessdata_path(), lang="eng", psm=PSM.SINGLE_LINE) as api:
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
            if row.isdecimal() and 1 <= int(row) <= 300 and -100 <= int(row)-offset <= 300:
                anchors[int(row)-offset] += 1
        if not anchors:
            return tokens
        origin, votes = anchors.most_common(1)[0]
        if len(rows) > 2 and votes < 2:
            return tokens
        for offset, (top, bottom) in zip(offsets, rows):
            number = origin + offset
            if not 1 <= number <= 300:
                continue
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





