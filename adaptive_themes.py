"""Learn piece appearances from a legal opening and keep local theme profiles."""
import hashlib
import os
import tempfile
from pathlib import Path

import chess
import numpy as np
from PIL import Image

SIZE = 24
CORNERS = ((1, 1), (22, 1), (1, 22), (22, 22))
PARITY = np.array([(row+col)%2 for row in range(8) for col in range(8)])

def tiles_from_image(image):
    width, height = image.size
    return np.stack([
        np.asarray(image.crop((round(col*width/8), round(row*height/8),
                               round((col+1)*width/8), round((row+1)*height/8)))
                   .convert("RGB").resize((SIZE, SIZE)), dtype=np.uint8)
        for row in range(8) for col in range(8)
    ])

def foreground(tiles, backgrounds, noise):
    reference = backgrounds[PARITY].astype(np.float32)
    actual = tiles.astype(np.float32)
    corners = np.array([actual[:,y,x] for x,y in CORNERS])
    reference_corners = np.array([reference[:,y,x] for x,y in CORNERS])
    shift = np.median(corners,axis=0) - np.median(reference_corners,axis=0)
    expected = np.clip(reference + shift[:,None,None,:],0,255)
    difference = np.max(np.abs(actual-expected),axis=3)
    masks = difference > np.maximum(30, noise[PARITY]*3+12)
    masks[:,:2] = False
    masks[:,-2:] = False
    masks[:,:,:2] = False
    masks[:,:,-2:] = False
    # A legal-move dot occupies the centre of an otherwise empty square.
    for mask in masks:
        ys,xs = np.nonzero(mask)
        if len(xs) and xs.max()-xs.min() < 8 and ys.max()-ys.min() < 8:
            mask[:] = False
    return masks

def opening_labels():
    board = chess.Board()
    states = {board.board_fen(): board.piece_map()}
    for first in list(board.legal_moves):
        board.push(first)
        states.setdefault(board.board_fen(),board.piece_map())
        for second in list(board.legal_moves):
            board.push(second)
            states.setdefault(board.board_fen(),board.piece_map())
            board.pop()
        board.pop()
    result = []
    for pieces in states.values():
        labels = np.array([pieces[square].symbol() if square in pieces else "."
                           for rank in range(7,-1,-1) for square in range(rank*8,rank*8+8)])
        result.extend((labels,labels[::-1]))
    return result

class ThemeLibrary:
    def __init__(self, directory=None):
        local = Path(os.environ.get("LOCALAPPDATA",Path.home()/"AppData"/"Local"))
        self.directory = Path(directory) if directory is not None else local/"ChessBotTrainer"/"themes"
        self.profiles = None
        self.openings = None
        self.preferred = None

    def load(self):
        if self.profiles is not None:
            return
        self.profiles = {}
        for path in self.directory.glob("*.npz"):
            try:
                with np.load(path,allow_pickle=False) as stored:
                    profile = {key:stored[key] for key in ("backgrounds","noise","pixels","masks","labels")}
                if (profile["backgrounds"].shape != (2,SIZE,SIZE,3)
                        or profile["noise"].shape != (2,SIZE,SIZE)
                        or profile["pixels"].shape != (32,SIZE,SIZE,3)
                        or profile["masks"].shape != (32,SIZE,SIZE)
                        or profile["labels"].shape != (32,)):
                    continue
                self.profiles[path.stem] = profile
            except (OSError,ValueError,KeyError):
                continue

    def calibrate(self, tiles):
        centre = np.arange(16,48)
        backgrounds = np.stack([np.median(tiles[centre[PARITY[centre]==parity]],axis=0)
                                for parity in (0,1)]).astype(np.uint8)
        noise = np.stack([np.median(np.max(np.abs(
            tiles[centre[PARITY[centre]==parity]].astype(np.float32)-backgrounds[parity]),axis=3),axis=0)
                          for parity in (0,1)])
        masks = foreground(tiles,backgrounds,noise)
        occupied = np.any(masks,axis=(1,2))
        if occupied.sum() != 32:
            return None
        if self.openings is None:
            self.openings = opening_labels()
        match = None
        for labels in self.openings:
            if not np.array_equal(labels!=".",occupied):
                continue
            white, black = [], []
            for index,label in enumerate(labels):
                if label == ".":
                    continue
                brightness = float(np.quantile(tiles[index][masks[index]].mean(axis=1),.75))
                (white if label.isupper() else black).append(brightness)
            if np.median(white) - np.median(black) < 20:
                continue
            if match is not None and not np.array_equal(match,labels):
                return None
            match = labels
        if match is None:
            return None
        profile = {"backgrounds":backgrounds, "noise":noise.astype(np.float32),
                   "pixels":tiles[occupied], "masks":masks[occupied], "labels":match[occupied]}
        # Verify independent classification before retaining the profile.
        if self.classify(tiles,profile) is None:
            return None
        key = hashlib.sha256(profile["pixels"].tobytes()+profile["backgrounds"].tobytes()).hexdigest()[:20]
        self.profiles[key] = profile
        self.preferred = key
        temporary = None
        try:
            self.directory.mkdir(parents=True,exist_ok=True)
            with tempfile.NamedTemporaryFile(dir=self.directory,suffix=".npz",delete=False) as handle:
                temporary = Path(handle.name)
                np.savez_compressed(handle,**profile)
            temporary.replace(self.directory/(key+".npz"))
        except OSError:
            if temporary is not None:
                temporary.unlink(missing_ok=True)
        return profile

    def classify(self,tiles,profile):
        masks = foreground(tiles,profile["backgrounds"],profile["noise"])
        prototypes = profile["pixels"].astype(np.float32)
        template_masks = profile["masks"]
        labels = []
        for tile,mask in zip(tiles,masks):
            if not mask.any():
                labels.append(".")
                continue
            union = template_masks | mask
            areas = np.maximum(union.sum(axis=(1,2)),1)
            pixel_error = (np.abs(prototypes-tile).mean(axis=3)*union).sum(axis=(1,2))/(255*areas)
            shape_error = (template_masks ^ mask).sum(axis=(1,2))/areas
            losses = pixel_error*.65 + shape_error*.35
            scores = sorted((float(losses[profile["labels"]==symbol].min()),symbol)
                            for symbol in sorted(set(profile["labels"])))
            if scores[0][0] > .19 or scores[1][0]-scores[0][0] < .025:
                return None
            labels.append(scores[0][1])
        if labels.count("K") != 1 or labels.count("k") != 1:
            return None
        return {chess.square(col,7-row):chess.Piece.from_symbol(labels[row*8+col])
                for row in range(8) for col in range(8) if labels[row*8+col]!="."}

    def read(self,image,flipped):
        self.load()
        tiles = tiles_from_image(image)
        keys = list(self.profiles)
        if self.preferred in keys:
            keys.remove(self.preferred)
            keys.insert(0,self.preferred)
        for key in keys:
            pieces = self.classify(tiles,self.profiles[key])
            if pieces is not None:
                self.preferred = key
                break
        else:
            profile = self.calibrate(tiles)
            if profile is None:
                return None
            pieces = self.classify(tiles,profile)
        if flipped:
            pieces = {63-square:piece for square,piece in pieces.items()}
        return pieces

LIBRARY = ThemeLibrary()
