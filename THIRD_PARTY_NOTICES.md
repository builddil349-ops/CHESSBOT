# Third-party notices

CHESSBOT source code is provided under GPL-3.0-or-later; see LICENSE. This does not replace licenses or ownership of dependencies or source artwork.

Original license files are preserved in `THIRD_PARTY_LICENSES.zip`; paths below refer to files inside that archive.

## python-chess / chess 1.11.2

Copyright (C) 2012–2025 Niklas Fiekas and contributors. GPL-3.0-or-later. Source: https://github.com/niklasf/python-chess. License text: python-chess-LICENSE.txt.

## Stockfish

An external UCI chess engine, selected by the user at startup. It is not bundled in the CHESSBOT executable or source repository. Official source and license: https://github.com/official-stockfish/Stockfish and https://stockfishchess.org. The existing local Stockfish distribution is kept outside version control with its original source and notices.

## Tesseract, OCR model and tesserocr

Tesseract OCR and the English model are Apache-2.0 projects. The English model is from tesseract-ocr/tessdata_fast, commit 87416418657359cb625c412a48b6e1d6d41c29bd: https://github.com/tesseract-ocr/tessdata_fast. Model SHA-256: 7d4322bd2a7749724879683fc3912cb542f19906c83bcc1a52132556427170b2.

tesserocr 2.12.0 is MIT licensed, copyright Fayez Zouheiry; source: https://github.com/sirfz/tesserocr. Its Windows wheel includes native libraries. The wheel's supplied license directory, including native dependency notices, is preserved in tesserocr/ and included in the EXE.

## Pillow and NumPy

Pillow 13.0.0: https://github.com/python-pillow/Pillow (MIT-CMU and included dependency notices). NumPy 2.3.5: https://github.com/numpy/numpy (BSD-3-Clause and included bundled-library notices). License files from the installed distributions are preserved in THIRD_PARTY_LICENSES.zip.

## Python, Tcl/Tk and PyInstaller

The executable is assembled using CPython, Tcl/Tk and PyInstaller. Their respective notices are retained in THIRD_PARTY_LICENSES.zip where supplied by the build environment. PyInstaller's bootloader exception allows distribution of packaged applications under their chosen license. Source: https://github.com/pyinstaller/pyinstaller.

## Chess.com appearances

Chess.com is a trademark of its owner. Shape templates describe visual appearances for recognition; the project does not claim ownership of the original board or piece artwork. User screenshots, account information and captured game history are not included in the repository. No affiliation or endorsement is implied.
