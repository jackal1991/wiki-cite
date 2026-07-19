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


def compute_summary(counts: dict[str, int]) -> dict:
    """Turn `SeenStore.summary_counts()` raw tallies into display-ready rates.

    A rate is None when its denominator is 0, so callers/templates render a
    placeholder instead of dividing by zero.
    """
    pushed = counts.get("pushed_articles", 0)
    reverted = counts.get("reverted_articles", 0)
    approved = counts.get("approved_edits", 0)
    rejected = counts.get("rejected_edits", 0)
    review_total = approved + rejected
    return {
        "pushed_articles": pushed,
        "reverted_articles": reverted,
        "revert_rate": (reverted / pushed) if pushed else None,
        "approved_edits": approved,
        "rejected_edits": rejected,
        "approval_rate": (approved / review_total) if review_total else None,
        "has_data": bool(pushed or review_total),
    }
