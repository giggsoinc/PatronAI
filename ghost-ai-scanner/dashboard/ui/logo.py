# =============================================================
# FILE: dashboard/ui/logo.py
# VERSION: 1.0.0
# UPDATED: 2026-04-29
# OWNER: Giggso Inc (Ravi Venugopal)
# PURPOSE: Inline SVG PatronAI logo — no file dependency.
#          Works in Docker container without branding PNGs.
#          If assets/branding/patronai-logo.png exists it takes
#          precedence (higher resolution) in sidebar and home page.
#          Two variants:
#            sidebar_html() — white text on dark (#0A0F1F) tile
#            home_html()    — dark text on white background
# AUDIT LOG:
#   v1.0.0  2026-04-29  Initial. Replaces file-based logo rendering.
# =============================================================

import base64
import pathlib

_BLUE  = "#1F6FEB"
_DARK  = "#0D1117"
_WHITE = "#FFFFFF"
_BG    = "#0A0F1F"

# Shield path: centred shield ~24×36px within a 44px-tall viewBox
_SHIELD = (
    'M12 4 L24 9 L24 26 Q24 38 12 42 Q0 38 0 26 L0 9 Z'
)

_PNG_PATH = pathlib.Path("assets/branding/patronai-logo.png")


def _png_b64() -> str | None:
    """Return base64 data-URI for the PNG logo, or None if absent."""
    try:
        if _PNG_PATH.exists():
            return "data:image/png;base64," + base64.b64encode(
                _PNG_PATH.read_bytes()).decode()
    except Exception:  # intentional: must not crash
        pass
    return None


def _svg(text_color: str, width: int = 175) -> str:
    """Build the inline SVG logo at the requested display width."""
    return (
        f'<svg xmlns="http://www.w3.org/2000/svg" '
        f'viewBox="0 0 175 44" width="{width}" height="{int(44*width/175)}">'
        # Shield fill + outline
        f'<path d="{_SHIELD}" fill="{_BLUE}" fill-opacity="0.15" '
        f'stroke="{_BLUE}" stroke-width="1.8"/>'
        # "P" inside shield
        f'<text x="7" y="30" font-family="Arial,sans-serif" '
        f'font-size="18" font-weight="800" fill="{_BLUE}">P</text>'
        # "Patron" (main text colour)
        f'<text x="32" y="32" font-family="Arial,sans-serif" '
        f'font-size="24" font-weight="700" fill="{text_color}">Patron</text>'
        # "AI" in brand blue
        f'<text x="118" y="32" font-family="Arial,sans-serif" '
        f'font-size="24" font-weight="700" fill="{_BLUE}">AI</text>'
        f'</svg>'
    )


def sidebar_html() -> str:
    """Dark-background logo tile for the sidebar.
    Uses PNG if available, SVG otherwise."""
    png = _png_b64()
    inner = (
        f'<img src="{png}" width="170" style="display:block;margin:auto"/>'
        if png else _svg(_WHITE, 170)
    )
    return (
        f'<div style="background:{_BG};border-radius:10px;'
        f'padding:14px 10px;text-align:center;margin-bottom:4px">'
        f'{inner}</div>'
    )


def home_html(width: int = 220) -> str:
    """Light-background logo for the home page hero.
    Uses PNG if available, SVG otherwise."""
    png = _png_b64()
    inner = (
        f'<img src="{png}" width="{width}" style="display:block;margin:auto"/>'
        if png else _svg(_DARK, width)
    )
    return (
        f'<div style="text-align:center;margin-bottom:4px">{inner}</div>'
    )
