from __future__ import annotations

from typing import Any

try:
    from colorama import Fore as _Fore
    from colorama import Style as _Style
    from colorama import init

    init(autoreset=True)
    Fore: Any = _Fore
    Style: Any = _Style
except ImportError:
    class _FallbackFore:
        GREEN = YELLOW = BLUE = CYAN = RED = MAGENTA = WHITE = RESET = ""

    class _FallbackStyle:
        BRIGHT = DIM = NORMAL = RESET_ALL = ""

    Fore = _FallbackFore()
    Style = _FallbackStyle()
