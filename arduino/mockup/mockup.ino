// Minimal fixed sketch flashed by the kidink demo CLI (server/app/eink/,
// run as `uv run python -m app.eink` from server/). The CLI generates
// mockup.h next to this file; mockup.h is gitignored, so compiling
// requires running the CLI first.
//
// Adapted from the Inkplate Arduino library's Inkplate13SPECTRA_Image_Converter
// example, Copyright (c) Soldered Electronics, LGPL-3.0 — see
// THIRD_PARTY_NOTICES.md at the repo root.
#ifndef ARDUINO_INKPLATE13SPECTRA
#error "Wrong board selection for this example, please select Soldered Inkplate13SPECTRA in the boards menu."
#endif

#include "Inkplate.h"
#include "mockup.h"

Inkplate display;

void setup()
{
    display.begin();
    display.clearDisplay();
    display.image.draw(mockup, 0, 0, mockup_w, mockup_h);
    display.display();
}

void loop()
{
}
