# Phase 1: Server-side pending-proposal cap + resolution paths

**Goal:** Enforce a hard cap of 10 pending proposals server-side, expose the pending count, and give proposals a full-reject resolution path that frees a queue slot.
**AC Coverage:** AC2 (server-side cap refuses new scan at 10 pending), AC4 (cap counts proposals, not edits), AC5 (push or full-reject frees a slot — server half)

---

## Context

`wiki_cite/web_app.py` keeps proposals in an in-memory `proposals: dict[str, EditProposal]`
(line 33). `EditProposal.status` is a `Literal["pending", "approved", "rejected", "pushed"]`
defaulting to `"pending"` (`wiki_cite/models.py:183`).

Current state verified in the codebase:
- New proposals are added in `scan_events()` at line 114 (`proposals[proposal.id] = proposal`)
  and always start `status == "pending"`.
- `push_proposal` (line 381) already sets `proposal.status = "pushed"` on a successful push —
  so push already removes a proposal from the pending set. No change needed there.
- **There is no proposal-level reject endpoint.** `reject_edit` (line 337) only flips a single
  edit's `approved` flag to `False`; it never changes `proposal.status`. The review page's
  "Reject all" button loops `reject-edit` over every edit but leaves `status == "pending"`.
  So today a fully-rejected proposal would occupy a queue slot forever. This phase adds the
  missing resolution endpoint.
- `scan_events()` is the single shared generator behind both `/api/fetch-article` (JSON,
  line 164) and `/api/fetch-article/stream` (SSE, line 184). Guarding inside `scan_events()`
  covers both routes at once.

This phase is server-only. The frontend wiring is Phase 2.

## Implementation

### Module-level cap constant + pending-count helper

**Files:**
- Modify: `wiki_cite/web_app.py`

**What to implement:**

Add a module-level constant near the top of the file (after the imports, before
`create_app`):

```python
# Maximum number of unresolved (status == "pending") proposals allowed in the
# queue at once. Fetching a new article is refused while the queue is at this cap.
MAX_PENDING_PROPOSALS = 10
```

Inside `create_app`, after the `proposals` dict is defined (line 33-34), add a small closure
that counts pending proposals. It must count **proposals**, not edits — a proposal with 3
edits counts as 1:

```python
def pending_proposal_count() -> int:
    """Count unresolved proposals (status == 'pending'). Pushed/rejected proposals
    do not occupy a queue slot. Counts proposals, not edits."""
    return sum(1 for p in proposals.values() if p.status == "pending")
```

### Cap guard in `scan_events()`

**Files:**
- Modify: `wiki_cite/web_app.py` — `scan_events()` (starts line 55)

**What to implement:**

At the very top of `scan_events()`'s `try` block (before the `scan_start` event is yielded
at line 68), refuse to scan when already at cap and return a clear terminal event:

```python
if pending_proposal_count() >= MAX_PENDING_PROPOSALS:
    yield {
        "type": "queue_full",
        "error": f"Queue is full — {MAX_PENDING_PROPOSALS} pending proposals. "
                 "Resolve some before fetching more.",
    }
    return
```

Use a distinct `"queue_full"` type (not the generic `"error"`) so the JSON route and the
frontend can treat "at cap" differently from an unexpected failure.

### JSON route handles the `queue_full` terminal

**Files:**
- Modify: `wiki_cite/web_app.py` — `fetch_article()` (line 164)

**What to implement:**

`fetch_article()` iterates `scan_events()` and returns the terminal event. Add handling so a
`queue_full` terminal returns HTTP 409 Conflict with the error message. After the loop, before
the existing `error`/404 handling (lines 180-182):

```python
if terminal and terminal["type"] == "queue_full":
    return jsonify({"error": terminal["error"]}), 409
```

The SSE route (`fetch_article_stream`, line 184) needs no change — it serializes whatever
events the generator yields, so the `queue_full` event flows through to the client as-is.

### New pending-count endpoint

**Files:**
- Modify: `wiki_cite/web_app.py`

**What to implement:**

Add a read-only endpoint the frontend polls to decide whether the fetch button should be
enabled. Place it near `get_proposals` (line 199):

```python
@app.route("/api/proposals/pending-count")
def get_pending_count():
    """Report the current pending-proposal count and the cap, so the UI can
    disable 'Fetch new article' before the user even tries when the queue is full."""
    pending = pending_proposal_count()
    return jsonify(
        {"pending": pending, "cap": MAX_PENDING_PROPOSALS, "at_cap": pending >= MAX_PENDING_PROPOSALS}
    )
```

### New proposal-level reject endpoint

**Files:**
- Modify: `wiki_cite/web_app.py`

**What to implement:**

Add an endpoint that marks a whole proposal as resolved-by-rejection, freeing its queue slot.
Place it near the other `/api/proposals/<proposal_id>/...` POST routes (e.g. after
`reject_edit`, line 362):

```python
@app.route("/api/proposals/<proposal_id>/reject", methods=["POST"])
def reject_proposal(proposal_id: str):
    """Reject an entire proposal, freeing its queue slot. Sets status to 'rejected'
    so it no longer counts toward the pending cap."""
    if proposal_id not in proposals:
        return jsonify({"error": "Proposal not found"}), 404

    proposals[proposal_id].status = "rejected"
    return jsonify({"success": True})
```

Do not add per-edit outcome recording here — the existing per-edit `reject-edit` calls (which
the frontend already issues for each edit) handle outcome persistence. This endpoint only
changes proposal-level status. Keeping it status-only avoids double-counting outcomes.

## Tests

**File:** `tests/test_web_app.py`

Add tests using the existing `app` fixture (line 52-58), which seeds proposals directly via
`app.proposals[...]` and stubs out network services. Use `web_app.make_proposal`-style
construction (`make_proposal()` helper at line 61) or build minimal `EditProposal`s. Note the
`make_proposal` helper builds one proposal with **two** edits — useful for AC4.

Add these tests (map to ACs):

1. `test_pending_count_endpoint_reports_zero_when_empty` — `GET /api/proposals/pending-count`
   on a fresh app returns `{"pending": 0, "cap": 10, "at_cap": False}`. (AC2/AC4 support)

2. `test_pending_count_counts_proposals_not_edits` (AC4) — seed one proposal built by
   `make_proposal()` (2 edits). `GET /api/proposals/pending-count` returns `pending == 1`,
   not 2.

3. `test_fetch_refused_at_cap_returns_queue_full` (AC2) — seed 10 pending proposals into
   `app.proposals` (give each a distinct `id`; reuse a helper that clones `make_proposal`
   with a unique id). `GET /api/fetch-article` returns HTTP 409 with an `"error"` mentioning
   the queue is full. Assert `fetch_candidates` / the picker was **not** invoked (the scan
   never started) — e.g. patch `web_app.ArticlePicker` as the other fixtures do, or assert via
   the JSON that no `selected`/scan happened. Simplest: with 10 pending proposals seeded, the
   409 response itself proves the guard fired before scanning.

4. `test_fetch_stream_refused_at_cap_emits_queue_full` (AC2) — seed 10 pending proposals.
   `GET /api/fetch-article/stream` response body (SSE text) contains a `"queue_full"` event.
   Parse the streamed `data: {...}` lines and assert one has `type == "queue_full"`.

5. `test_reject_proposal_sets_status_and_frees_slot` (AC5) — seed 10 pending proposals so
   `pending-count` reports `at_cap == True`. `POST /api/proposals/<id>/reject` on one returns
   200 `{"success": True}` and sets that proposal's `status == "rejected"`. A following
   `GET /api/proposals/pending-count` reports `pending == 9`, `at_cap == False`.

6. `test_reject_proposal_unknown_id_404` (AC5 edge) — `POST /api/proposals/does-not-exist/reject`
   returns 404.

7. `test_push_frees_a_slot` (AC5) — seed proposals up to cap. Confirm that setting a proposal's
   status to "pushed" (or driving `push_proposal` with a stubbed push service returning success
   and one approved edit) drops the pending count below cap. If wiring a full push is awkward
   with the stubs, assert the narrower invariant: `pending_proposal_count()` excludes
   `status == "pushed"` proposals — seed a mix of pending and pushed proposals and assert the
   endpoint counts only pending ones.

For seeding N pending proposals with distinct ids, a small local helper in the test module is
fine, e.g.:

```python
def seed_pending(app, n):
    for i in range(n):
        p = make_proposal()
        p.id = f"p{i}"
        app.proposals[p.id] = p
```

(`EditProposal` is a dataclass, so reassigning `p.id` is valid; each `make_proposal()` builds a
fresh instance.)

---

## Verification

Run: `uv run pytest tests/test_web_app.py`
Expected: all tests pass, including the new cap/pending-count/reject tests.

Also run: `uv run ruff check .`
Expected: clean.

## Commit

`feat: cap pending proposals at 10 server-side, add reject-proposal + pending-count endpoints`
