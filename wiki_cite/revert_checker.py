"""Post-push revert detection.

Walks a pushed article's revisions newer than the revision we pushed, looking
for a rollback/undo/manual-revert marker (tag first, edit-summary substring as
a conservative fallback). Content-hash confirmation was deliberately deferred
to a later iteration (design doc §Architecture) — v1 trades some false
negatives (an unmarked manual revert) for zero reliance on a diff heuristic.
"""

import logging
from typing import NamedTuple

import mwclient

from wiki_cite.seen_store import SeenStore

logger = logging.getLogger(__name__)

# MediaWiki tags applied to the reverting edit itself (rollback/undo/manual
# revert action). Deliberately excludes `mw-reverted`, which MediaWiki applies
# to the *victim* revision that got reverted, not the reverting edit — reading
# it here would flag our own pushed revision as "reverted" whenever a later,
# unrelated edit on top of it gets reverted, even though that later revert
# effectively restores our edit.
REVERT_TAGS = frozenset({"mw-rollback", "mw-undo", "mw-manual-revert"})

# Conservative edit-summary substrings for reverts that land without one of the
# tags above (e.g. a manual revert). Kept short and specific: a false positive
# here writes a spurious "reverted" row that dents the revert rate. "revert"
# already covers "reverted" as a substring; "restore" is deliberately omitted
# since it also matches legitimate summaries like "restored formatting".
REVERT_SUMMARY_MARKERS = ("revert", "rv ", "undo", "undid", "rollback")


class RevertCheckSummary(NamedTuple):
    """Result of a `check_pending_reverts` batch run."""

    checked: int
    reverts_found: int
    failures: list[tuple[str, str]]


def is_revert_revision(tags: list[str] | None, comment: str | None) -> bool:
    """Return True if a revision's tags or edit summary mark it as a revert."""
    if tags and any(tag in REVERT_TAGS for tag in tags):
        return True
    if comment:
        low = comment.lower()
        if any(marker in low for marker in REVERT_SUMMARY_MARKERS):
            return True
    return False


def check_article_for_revert(site: mwclient.Site, article_title: str, pushed_revid: str) -> bool:
    """Return True if a revision newer than pushed_revid reverts our edit."""
    try:
        revid_int = int(pushed_revid)
    except (TypeError, ValueError):
        logger.warning("Non-numeric pushed_revid %r for %r; treating as un-walkable", pushed_revid, article_title)
        return False

    page = site.pages[article_title]
    revisions = page.revisions(
        startid=revid_int,
        dir="newer",
        prop="ids|timestamp|flags|comment|user|tags",
    )
    for rev in revisions:
        if str(rev.get("revid")) == str(pushed_revid):
            continue  # startid is inclusive - skip our own revision
        if is_revert_revision(rev.get("tags"), rev.get("comment")):
            return True
    return False


def check_pending_reverts(site: mwclient.Site, store: SeenStore, horizon_days: int) -> RevertCheckSummary:
    """Walk every pending candidate; record "reverted" on match.

    Per-article errors are caught so one bad article can't abort the batch.
    """
    checked = 0
    reverts_found = 0
    failures: list[tuple[str, str]] = []

    for title, revid in store.pending_revert_candidates(horizon_days):
        checked += 1
        try:
            if check_article_for_revert(site, title, revid):
                store.record_outcome(title, revid, "reverted")
                reverts_found += 1
        except Exception as e:
            failures.append((title, str(e)))

    return RevertCheckSummary(checked=checked, reverts_found=reverts_found, failures=failures)
