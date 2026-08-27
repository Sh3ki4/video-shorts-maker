from __future__ import annotations

import os
import sys
from pathlib import Path

from shorts_maker.gui_qt import run_gui


def application_dir() -> Path:
    if getattr(sys, "frozen", False):
        return Path(sys.executable).resolve().parent
    return Path(__file__).resolve().parent


if __name__ == "__main__":
    if "--self-test" in sys.argv:
        os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
        import ctranslate2  # noqa: F401 - verifies packaged local Whisper backend
        import faster_whisper  # noqa: F401
        import groq  # noqa: F401 - verifies packaged cloud backend
        raise SystemExit(run_gui(application_dir(), self_test=True))
    else:
        raise SystemExit(run_gui(application_dir()))
