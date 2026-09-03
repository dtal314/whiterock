"""Generate the WhiteRock logo through the free local Codex image lane
(gpt-image-2 on the ChatGPT subscription; no API key, $0 marginal).

Run from the project folder:  python tools/make_logo.py
Output: site/assets/logo_raw.png (then tools/make_icons.py derives sizes).
"""
from __future__ import annotations

import sys
from pathlib import Path

CODEX_ADAPTER_DIR = Path(r"D:\Dropbox (Personal)\000 - PLAN2CAD\00 - CODEX")
sys.path.insert(0, str(CODEX_ADAPTER_DIR))
from codex_imagegen import generate_image  # noqa: E402

HERE = Path(__file__).resolve().parent
OUT = HERE.parent / "site" / "assets" / "logo_raw.png"

PROMPT = (
    "Minimalist logo mark for an app named WhiteRock: a single smooth black rock "
    "(dark matte basalt stone, softly faceted, slightly rounded) resting centered on a "
    "pure white background. Clean flat vector illustration style, a very subtle soft "
    "shadow beneath the rock, no text, no letters, no other objects, square composition, "
    "generous white margins around the rock."
)

if __name__ == "__main__":
    res = generate_image(PROMPT, OUT)
    print(res["error"] or f"OK -> {res['path']} (session {res['session_id']})")
    print(res["raw_tail"])
    sys.exit(0 if res["ok"] else 1)
