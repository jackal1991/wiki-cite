"""Shared dimension list for the outcomes-feedback stats surfaces.

`wiki-cite stats` (cli.py) and the `/stats` web route (web_app.py) both walk
this list and render `SeenStore.dimension_rates` output, so keeping it in one
place means the two renderers can't drift from each other.
"""

STATS_DIMENSIONS = [
    "source_type",
    "source_api",
    "edit_type",
    "confidence",
    "has_infobox",
    "categories",
]
