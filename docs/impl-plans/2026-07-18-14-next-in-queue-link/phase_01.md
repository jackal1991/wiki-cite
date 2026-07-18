# Phase 1: Server route — next pending proposal

**Goal:** Add a read-only endpoint that, given a proposal id, returns the id of the next *pending* proposal to review (excluding the current one), or `null` when none remains. This puts the skip/exclude/ordering logic server-side where it is unit-testable, so the review-page control (Phase 2) can be a thin navigation shim.
**AC Coverage:** AC2 (jumps to the next *pending* proposal excluding the current one), AC3 (skips non-pending proposals), AC4 (server signals "no next" so the client can avoid a dead link) — see `test-requirements.md`.

---

## Context

`wiki_cite/web_app.py` keeps proposals in an in-memory, insertion-ordered
`proposals: dict[str, EditProposal]` (line 37; `app.proposals = proposals` is the
test-only seeding seam at line 38). `EditProposal.status` is a
`Literal["pending", "approved", "rejected", "pushed"]` defaulting to `"pending"`
(`wiki_cite/models.py`).

Current state verified in the codebase:
- `GET /api/proposals` (line 218) already serializes every proposal — including
  `status` — in `proposals.values()` order. The queue page (`index.html`) renders
  *all* of them (pending, pushed, rejected), so "which proposal is next to review"
  is a *pending-only* question the list endpoint does not answer directly.
- `pending_proposal_count()` (line 40) is the existing precedent for "a pending
  proposal is one with `status == 'pending'`"; reuse that same predicate here.
- The review page route is `GET /review/<proposal_id>` (line 489), rendering
  `review.html` with `proposal_id`. Navigation between proposals is therefore just
  a URL change to `/review/<next_id>`.
- Other `/api/proposals/<proposal_id>/...` routes return **404** with
  `{"error": "Proposal not found"}` when the id is unknown (e.g. `get_proposal`
  line 245, `reject_proposal` line 392). The new route follows that convention.

This phase is server-only. The `review.html` control that consumes the route is
Phase 2.

## Implementation

### New "next pending" endpoint

**Files:**
- Modify: `wiki_cite/web_app.py`

**What to implement:**

Add a read-only route near the other `/api/proposals/<proposal_id>/...` GET routes
(e.g. directly after `get_proposal`, line 245-276). It computes the next pending
proposal to review, in queue order, wrapping around, excluding the current one:

```python
@app.route("/api/proposals/<proposal_id>/next")
def next_pending_proposal(proposal_id: str):
    """Return the id of the next proposal still awaiting review, so the review
    page can jump straight to it without a round-trip through the queue. Walks
    proposals in queue (insertion) order starting just after the current one and
    wrapping around, skipping the current proposal and any that are no longer
    'pending' (pushed/rejected/approved). Returns {"next_id": None} when the
    current proposal is the only pending one (or none are)."""
    if proposal_id not in proposals:
        return jsonify({"error": "Proposal not found"}), 404

    ordered = list(proposals.values())
    start = next(i for i, p in enumerate(ordered) if p.id == proposal_id)
    # Rotate the list to begin right after the current proposal, so traversal is
    # forward-with-wraparound and every other pending item is reachable.
    rotated = ordered[start + 1 :] + ordered[: start + 1]
    for p in rotated:
        if p.id != proposal_id and p.status == "pending":
            return jsonify({"next_id": p.id})
    return jsonify({"next_id": None})
```

Notes on the design choices:
- **Pending-only** (`status == "pending"`) matches `pending_proposal_count()` — a
  pushed or rejected proposal is resolved and must never be the "next to review"
  (AC3).
- **Exclude the current proposal** via `p.id != proposal_id`. The current
  proposal is typically still `pending` while being reviewed, so filtering by
  status alone is not enough — it must be excluded explicitly (AC2). The rotation
  places the current proposal last, and the explicit `id` guard covers the case
  where it is the only pending item (returns `None`).
- **Forward-with-wraparound** ordering makes traversal predictable: from a given
  proposal you always advance to the next unreviewed one in queue order, and the
  wraparound guarantees you can reach earlier pending items too rather than
  dead-ending at the tail.
- **`next_id: null` (not 404) when nothing is next.** A 404 is reserved for an
  unknown `proposal_id`; "no other pending proposal" is a valid, expected answer
  the client uses to swap the control to "Back to queue" (AC4).

No other server changes are needed. `GET /api/proposals` and the review route are
untouched.

## Tests

**File:** `tests/test_web_app.py`

Reuse the existing `app` fixture (line 54), `make_proposal()` (line 62; builds one
`EditProposal` with id `"p1"` and two edits), and the `seed_pending(app, n)` helper
(line 267; seeds `n` pending proposals with ids `p0..p{n-1}`). `EditProposal` allows
reassigning `.id` and `.status`, as the existing cap tests already do.

Add these tests (map to ACs):

1. `test_next_pending_returns_following_pending` (AC2) — `seed_pending(app, 3)`
   (ids `p0`, `p1`, `p2`, all pending). `GET /api/proposals/p0/next` returns 200
   with `{"next_id": "p1"}`. A second call `GET /api/proposals/p1/next` returns
   `{"next_id": "p2"}`.

2. `test_next_pending_excludes_current` (AC2) — with `seed_pending(app, 3)`, assert
   `GET /api/proposals/p1/next` never returns `"p1"` (returns `"p2"`).

3. `test_next_pending_wraps_around` (AC2) — with `seed_pending(app, 3)`,
   `GET /api/proposals/p2/next` (the last in order) returns `{"next_id": "p0"}`.

4. `test_next_pending_skips_non_pending` (AC3) — `seed_pending(app, 3)`, then set
   `app.proposals["p1"].status = "pushed"`. `GET /api/proposals/p0/next` skips the
   pushed `p1` and returns `{"next_id": "p2"}`.

5. `test_next_pending_none_when_only_current_pending` (AC4) — seed one pending
   proposal (`make_proposal()` → id `p1`) plus one resolved proposal (a second
   `make_proposal()` with `.id = "p2"`, `.status = "rejected"`).
   `GET /api/proposals/p1/next` returns 200 with `{"next_id": None}`.

6. `test_next_pending_none_with_single_proposal` (AC4 edge) — seed exactly one
   pending proposal. `GET /api/proposals/p1/next` returns `{"next_id": None}`.

7. `test_next_pending_unknown_id_404` (convention) —
   `GET /api/proposals/does-not-exist/next` returns 404.

---

## Verification

Run: `uv run pytest tests/test_web_app.py`
Expected: all tests pass, including the seven new `test_next_pending_*` tests.

Also run: `uv run ruff check .`
Expected: clean.

## Commit

`feat: add /api/proposals/<id>/next endpoint returning the next pending proposal`
