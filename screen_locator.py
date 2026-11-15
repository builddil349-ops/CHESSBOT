"""Locate visible chessboards by their eight-by-eight background pattern."""
from dataclasses import dataclass

import numpy as np
from PIL import Image, ImageFilter


@dataclass(frozen=True)
class BoardLocation:
    box: tuple
    score: float


def _edges(line):
    change = np.max(np.abs(line[4:].astype(np.int16)-line[:-4].astype(np.int16)), axis=1) >= 22
    padded = np.r_[False, change, False]
    starts = np.flatnonzero(padded[1:] & ~padded[:-1])
    ends = np.flatnonzero(~padded[1:] & padded[:-1])
    return (starts+ends-1)/2+2.5


def _spans(pixels, min_cell, max_cell):
    votes = {}
    for cross in range(3, pixels.shape[0]-3, 7):
        edges = _edges(pixels[cross])
        for index, cell in enumerate(np.diff(edges)):
            if not min_cell <= cell <= max_cell:
                continue
            first = edges[index]
            targets = first+np.arange(7)*cell
            indices = np.searchsorted(edges, targets)
            right = edges[np.minimum(indices, len(edges)-1)]
            left = edges[np.maximum(indices-1, 0)]
            error = np.minimum(np.abs(targets-left), np.abs(targets-right))
            matched = error <= max(2.5, cell*.025)
            if matched.sum() < 6:
                continue
            nearest = np.where(np.abs(targets-left) < np.abs(targets-right), left, right)
            ks, xs = np.arange(7)[matched], nearest[matched]
            centered = ks-ks.mean()
            cell = float(np.dot(centered, xs-xs.mean())/np.dot(centered, centered))
            first = float(xs.mean()-cell*ks.mean())
            for offset in range(3):
                start, size = first-offset*cell, cell*8
                if start < -2 or start+size > pixels.shape[1]+2:
                    continue
                key = round(start/3), round(size/3)
                entry = votes.setdefault(key, [0, 0., 0., cross, cross])
                entry[0] += 1
                entry[1] += start
                entry[2] += size
                entry[3] = min(entry[3], cross)
                entry[4] = max(entry[4], cross)
    return [(total/count, size/count, count, low, high)
            for count, total, size, low, high in sorted(votes.values(), reverse=True)[:70]]


def checker_score(pixels, box):
    left, top, right, bottom = box
    width, height = right-left, bottom-top
    if min(width, height) < 80:
        return 0.
    corners = []
    for dy, dx in ((.08,.08), (.92,.08), (.08,.92), (.92,.92)):
        xs = np.clip((left+(np.arange(8)+dx)*width/8).astype(int), 0, pixels.shape[1]-1)
        ys = np.clip((top+(np.arange(8)+dy)*height/8).astype(int), 0, pixels.shape[0]-1)
        corners.append(pixels[ys[:,None], xs[None,:]])
    colors = np.median(corners, axis=0)
    parity = np.indices((8,8)).sum(axis=0)%2
    backgrounds = np.array([np.median(colors[parity == value], axis=0) for value in (0,1)])
    contrast = np.max(np.abs(backgrounds[0]-backgrounds[1]))
    if contrast < 22:
        return 0.
    losses = np.max(np.abs(colors-backgrounds[parity]), axis=2)
    coverage = np.mean(losses < max(18, contrast*.28))
    if coverage < .78 or np.median(losses) > max(14, contrast*.18):
        return 0.
    return float(coverage - np.median(losses)/(contrast*4))


def refine_board(pixels, box):
    """Fit grid lines at full resolution, avoiding cumulative resize errors."""
    x, y, right, bottom = box
    width, height = right-x, bottom-y

    def fit(array, start, length, other, extent):
        ys = np.clip((other+(np.arange(8)[:,None]+np.array([.04,.96]))*extent/8)
                     .astype(int).ravel(), 0, array.shape[0]-1)
        best = (-1., start, length)
        for proposed_start in range(start-3, start+4):
            for proposed_length in range(length-4, length+5):
                if proposed_start < 0 or proposed_start+proposed_length > array.shape[1]:
                    continue
                xs = np.rint(proposed_start+np.arange(1,8)*proposed_length/8).astype(int)
                changes = np.max(np.abs(array[ys[:,None], xs[None,:]].astype(float)
                                        -array[ys[:,None], xs[None,:]-1]), axis=2)
                score = float(np.median(changes, axis=0).mean())
                score -= .001*(abs(proposed_start-start)+abs(proposed_length-length))
                if score > best[0]:
                    best = (score, proposed_start, proposed_length)
        return best[1:]

    x, width = fit(pixels, x, width, y, height)
    y, height = fit(pixels.transpose(1,0,2), y, height, x, width)
    return x, y, x+width, y+height


def locate_boards(image, min_size=220, max_results=6):
    """Return screen-relative bounds; recognition of actual pieces is separate."""
    scale = min(1., 1280/image.width)
    small = image.convert('RGB').resize((round(image.width*scale), round(image.height*scale)),
                                        Image.Resampling.BILINEAR).filter(ImageFilter.GaussianBlur(.55))
    pixels = np.asarray(small)
    min_cell = max(8, min_size*scale/8)
    max_cell = min(pixels.shape[:2])/8
    horizontal = _spans(pixels, min_cell, max_cell)
    vertical = _spans(pixels.transpose(1,0,2), min_cell, max_cell)
    candidates = []
    for x, width, xvotes, ylow, yhigh in horizontal:
        for y, height, yvotes, xlow, xhigh in vertical:
            if abs(width-height) > max(3, width*.018):
                continue
            if not (y-2 <= ylow <= y+height+2 or y-2 <= yhigh <= y+height+2):
                continue
            if not (x-2 <= xlow <= x+width+2 or x-2 <= xhigh <= x+width+2):
                continue
            size = (width+height)/2
            box = (max(0,x), max(0,y), min(small.width,x+size), min(small.height,y+size))
            score = checker_score(pixels, box)
            if score >= .90:
                original = tuple(round(value/scale) for value in box)
                if min(original[2]-original[0], original[3]-original[1]) >= min_size:
                    candidates.append(BoardLocation(original, score))
    # Prefer the main board over a small preview, after the pattern is confirmed.
    candidates.sort(key=lambda item: (item.box[2]-item.box[0])*item.score, reverse=True)
    found = []
    full = np.asarray(image.convert('RGB'))
    for candidate in candidates:
        if any(max(abs(a-b) for a,b in zip(candidate.box, other.box)) < 18 for other in found):
            continue
        found.append(BoardLocation(refine_board(full, candidate.box), candidate.score))
        if len(found) >= max_results:
            break
    return found
