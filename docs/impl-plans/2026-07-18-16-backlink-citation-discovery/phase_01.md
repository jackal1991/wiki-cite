# Phase 1: Multi-URL citation extraction

**Goal:** Add `extract_all_citation_urls(text)` to `source_finder.py`, parallel to the existing
single-URL `extract_citation_url`, returning every distinct external citation URL in a wikitext
blob (all `{{cite ...}}` `url`/`URL` params plus bare `https?://` URLs), deduplicated with
first-seen order preserved.
**AC Coverage:** 16-backlink-citation-discovery.AC3 (AC3.1, AC3.2)

---

## Context

`wiki_cite/source_finder.py` already has the single-URL precedent this phase mirrors
(`source_finder.py:52` `extract_citation_url`):

```python
def extract_citation_url(text: str) -> str | None:
    wikicode = mwparserfromhell.parse(text)
    for template in wikicode.filter_templates():
        if template.name.strip().lower().startswith("cite"):
            for param_name in ("url", "URL"):
                if template.has(param_name):
                    value = str(template.get(param_name).value).strip()
                    if value:
                        return value
    bare_url_match = re.search(r"https?://[^\s|}\]<>\"']+", text)
    if bare_url_match:
        return bare_url_match.group(0)
    return None
```

`mwparserfromhell` and `re` are already imported at the top of the module. This phase adds a
second module-level function directly beneath `extract_citation_url`; it touches nothing else
and constructs no `SourceFinder`, so it is unit-testable in isolation.

`extract_all_citation_urls` differs from `extract_citation_url` only in that it accumulates
across the whole blob instead of returning on the first hit, and it deduplicates. A whole
backlinking article's wikitext (Phase 2) can carry dozens of `<ref>`/`{{cite}}` blocks, so the
scan must cover every template and every bare URL, not just the first.

## Implementation

### `extract_all_citation_urls` (new module-level function in `source_finder.py`)

**Files:**
- Modify: `wiki_cite/source_finder.py` — add one module-level function immediately after
  `extract_citation_url` (keep it a plain function, not a `SourceFinder` method, matching the
  existing single-URL helper).

**Signature:**
```python
def extract_all_citation_urls(text: str) -> list[str]:
    """Extract every distinct external citation URL from a wikitext blob.

    Scans all {{cite ...}} templates' |url=/|URL= parameters first, then every bare
    https?:// URL in the text. Deduplicates while preserving first-seen order — the
    citation most relevant to a specific claim is not guaranteed to be the first one
    on the page, so all distinct URLs are surfaced (design decision: all-URLs, not
    first-only).

    Args:
        text: Wikitext (typically a whole backlinking article's source).

    Returns:
        A list of distinct URLs in first-seen order. Empty list if none are found
        (never raises for a citation-free page).
    """
```

**What to implement:**
- Accumulate into a `list[str]` for order plus a `set[str]` for O(1) dedup — append a URL only
  if not already in the set. Do NOT use `sorted(...)`; first-seen order is required by AC3.1
  (unlike `crawl_subcategories`, which sorts).
- Template pass: `wikicode = mwparserfromhell.parse(text)`; for each
  `wikicode.filter_templates()` whose `template.name.strip().lower().startswith("cite")`, check
  both `"url"` and `"URL"` params (same as the single-URL version), and add each non-empty
  stripped value. A single template only contributes its first present of `url`/`URL` (they are
  the same logical parameter) — match `extract_citation_url`'s inner loop, which returns on the
  first present param name; here, `break` out of the param-name loop once one is added for that
  template so a template with both cased variants isn't double-counted.
- Bare-URL pass: `re.finditer(r"https?://[^\s|}\]<>\"']+", text)` (the same character class as
  the single-URL regex, switched from `re.search` to `re.finditer`), adding each match not
  already seen. Run this pass over the full original `text` so bare URLs outside cite templates
  (plain `<ref>https://...</ref>`) are captured; dedup against the template-pass set means a URL
  that appears both as a cite param and bare is surfaced once, template-pass position winning.
- Return the accumulated list.

**Notes:**
- Pure function: no network, no `SourceFinder`, no config. Reliability checking and `Source`
  construction are Phase 2's job — this returns raw URL strings only.
- Empty/citation-free input returns `[]`, not `None` and not an error (AC3.2).

## Verification

Run: `uv run pytest tests/test_source_finder.py -q`
Also: `uv run ruff check wiki_cite/source_finder.py`
Expected: existing tests still pass; new function importable
(`from wiki_cite.source_finder import extract_all_citation_urls`). Tests land in Phase 4, but you
may add the AC3.1/AC3.2 unit tests here.

## Commit

`feat: add multi-URL citation extraction to source_finder`
