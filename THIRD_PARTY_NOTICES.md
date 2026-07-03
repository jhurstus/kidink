# Third-party notices

This repository is licensed under the Apache License 2.0 (see `LICENSE`),
with the exceptions noted below.

## Inkplate Arduino library (LGPL-3.0)

Portions of this project are derived from the
[Inkplate Arduino library](https://github.com/SolderedElectronics/Inkplate-Arduino-library),
Copyright (c) Soldered Electronics, licensed under the GNU Lesser General
Public License v3.0. Those portions remain under LGPL-3.0 (text in
[`licenses/LGPL-3.0.txt`](licenses/LGPL-3.0.txt); LGPL-3.0 incorporates the
GNU GPL v3 by reference, see <https://www.gnu.org/licenses/gpl-3.0.html>):

- `server/app/eink/dither.py` — the `reduced`/`fs` error-diffusion
  implementation and its kernel tables are a Python port (modified) of
  `src/graphics/ImageColor/ImageDitherColor.cpp` and
  `ImageDitherColorKernels.h` from that library.
- `arduino/mockup/mockup.ino` — adapted from the library's
  `Inkplate13SPECTRA_Image_Converter` example sketch.

Compiling `arduino/mockup/` links against the Inkplate Arduino library
itself, which must be installed separately and is not distributed here.

The six-ink palette values, panel color codes, and 4bpp buffer layout used
elsewhere in `server/app/eink/` are hardware-interface facts documented from
that library but reimplemented independently.
