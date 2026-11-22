"""The color themes a person can pick in Settings > Appearance. The palettes themselves are in static/themes.css (one block per id);
this list is what the settings page shows and what the server accepts. "abyss" is the framework's own look (tokens.css as it is)."""
from __future__ import annotations

DEFAULT = "abyss"

# id, label, one line about it, and four swatch colors for the picker: page, card, primary, accent
THEMES = [
    {"id": "abyss", "label": "Abyss", "note": "Deep navy with violet and cyan. The original look.", "swatch": ["#070a14", "#121a2e", "#8b7bff", "#3dd6f5"]},
    {"id": "midnight", "label": "Midnight", "note": "Cooler and bluer, with less violet.", "swatch": ["#060b16", "#0f1a30", "#5b8cff", "#4fd1ff"]},
    {"id": "ember", "label": "Ember", "note": "Warm dark brown with orange and amber.", "swatch": ["#0e0806", "#1d110c", "#ff7a3d", "#ffc857"]},
    {"id": "forest", "label": "Forest", "note": "Deep green with mint and lime.", "swatch": ["#060d0b", "#0e1d18", "#34d399", "#a3e635"]},
    {"id": "rose", "label": "Rose", "note": "Dark plum with pink and lilac.", "swatch": ["#0e0711", "#1c0f20", "#f472b6", "#c084fc"]},
    {"id": "graphite", "label": "Graphite", "note": "Quiet neutral grey. No color wash.", "swatch": ["#0b0c0f", "#15171c", "#aab4c3", "#7dd3fc"]},
    {"id": "contrast", "label": "High contrast", "note": "Black and white with yellow, for the clearest reading.", "swatch": ["#000000", "#111111", "#ffd400", "#00e5ff"]},
]
IDS = [t["id"] for t in THEMES]


def clean(theme) -> str | None:
    """The theme id if it is one we ship, else None."""
    return theme if isinstance(theme, str) and theme in IDS else None
