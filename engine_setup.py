"""Remember the selected UCI engine and discover a neighbouring Stockfish."""
import json
import os
from pathlib import Path
import sys


def settings_path():
    return Path(os.environ.get('LOCALAPPDATA',Path.home()/'AppData'/'Local'))/'ChessBotTrainer'/'settings.json'


def find_engine(base=None, settings=None):
    settings = Path(settings) if settings is not None else settings_path()
    try:
        saved = Path(json.loads(settings.read_text(encoding='utf-8'))['engine'])
        if saved.is_file() and saved.suffix.lower() == '.exe':
            return saved
    except (OSError,ValueError,KeyError,TypeError):
        pass
    if base is None:
        base = Path(sys.executable).parent if getattr(sys,'frozen',False) else Path(__file__).parent
    base = Path(base)
    candidates = list(base.glob('stockfish*.exe'))
    for folder in base.glob('*Stockfish*'):
        if folder.is_dir():
            candidates.extend(folder.glob('**/stockfish*.exe'))
    candidates.sort(key=lambda path: ('sse41' not in path.name.lower(),
                                       'avx' in path.name.lower(),len(str(path))))
    return candidates[0] if candidates else None


def remember_engine(path, settings=None):
    settings = Path(settings) if settings is not None else settings_path()
    settings.parent.mkdir(parents=True,exist_ok=True)
    temporary = settings.with_suffix('.tmp')
    temporary.write_text(json.dumps({'engine':str(Path(path).resolve())}),encoding='utf-8')
    temporary.replace(settings)
