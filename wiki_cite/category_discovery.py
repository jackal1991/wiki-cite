"""Anthropic-facing category relevance classification.

Given raw Wikipedia subcategory names (from ``article_picker.crawl_subcategories``),
classifies each as content-relevant (KEEP) vs. Wikipedia-internal bookkeeping
(EXCLUDE), concurrently across batches. Kept separate from ``article_picker.py``,
which stays Wikipedia-facing only.
"""

import concurrent.futures
import json
import logging
import re
from datetime import datetime, timezone
from pathlib import Path

from anthropic import Anthropic

from wiki_cite.config import get_config

logger = logging.getLogger(__name__)

EXPANSIONS_DIR = Path("data/category_expansions")

CLASSIFY_SYSTEM_PROMPT = """You are classifying Wikipedia category names discovered by crawling the \
subcategory tree under a topic root. For each category name, decide whether it is likely to contain \
actual content articles — topical or biographical subjects a reader would look up — or whether it is \
Wikipedia-internal bookkeeping that exists to organize editors' work rather than readers' topics.

EXCLUDE categories like:
- Task force categories (e.g. "American politics task force")
- Quality/assessment categories (e.g. "... articles by quality", "... articles by importance")
- WikiProject participant categories (e.g. "WikiProject Biography participants")
- Other maintenance/tracking categories (cleanup, deletion, dispute, backlog categories)

KEEP categories like:
- Topical or biographical subcategories (e.g. "20th-century American politicians")
- "...stubs" categories (e.g. "American politician stubs") — this tool specifically targets stub
  articles, so stub categories are content-relevant and must be kept.

Respond with ONLY a JSON object mapping each input category name verbatim to a boolean: true to keep, \
false to exclude. Example:
{"20th-century American politicians": true, "American politics task force": false}

Do not include any other text, explanation, or markdown formatting — just the JSON object."""


def _parse_keep_map(text: str, names: list[str]) -> dict[str, bool]:
    """Extract the first JSON object from ``text`` and coerce it to a name->bool map.

    Tolerates code fences / surrounding prose. Any name missing from the parsed
    object, or a parse failure, is simply absent from the returned map — callers
    treat a missing name as excluded (fail closed).
    """
    json_match = re.search(r"```(?:json)?\s*(\{.*?\})\s*```", text, re.DOTALL)
    if json_match:
        json_text = json_match.group(1)
    else:
        json_match = re.search(r"\{.*\}", text, re.DOTALL)
        json_text = json_match.group(0) if json_match else text

    try:
        parsed = json.loads(json_text)
    except json.JSONDecodeError as e:
        logger.warning("Could not parse classification response as JSON: %s", e)
        return {}

    if not isinstance(parsed, dict):
        return {}

    return {name: bool(parsed[name]) for name in names if name in parsed}


def _classify_batch(client, model: str, names: list[str]) -> list[str]:
    """One Anthropic call classifying a batch; returns the KEEP names.

    Fail-closed: on a missing/unparseable/partial response, only names explicitly
    marked true in the parsed map are kept.
    """
    try:
        listed = "\n".join(f"- {name}" for name in names)
        response = client.messages.create(
            model=model,
            max_tokens=2000,
            system=CLASSIFY_SYSTEM_PROMPT,
            messages=[
                {
                    "role": "user",
                    "content": f"Classify these category names. Respond with the JSON object only:\n{listed}",
                }
            ],
        )
        text = "".join(block.text for block in response.content if block.type == "text")
        keep_map = _parse_keep_map(text, names)
        return [name for name in names if keep_map.get(name, False)]
    except Exception as e:
        logger.warning("Classification batch failed: %s", e)
        return []


def classify_categories(
    names: list[str],
    *,
    client: Anthropic | None = None,
    batch_size: int = 20,
    max_workers: int = 4,
) -> list[str]:
    """Classify category names concurrently; return the accepted (content-relevant) names.

    Batches ``names`` (default 20/call), dispatches batches across a ThreadPoolExecutor
    (Anthropic traffic — no Wikipedia etiquette constraint applies), and unions the KEEP
    results. Fail-closed: any batch whose call errors or whose response can't be parsed
    contributes NO accepted names (every name in it defaults to excluded) and is logged —
    the rest of the batches still complete.
    """
    if client is None:
        client = Anthropic(api_key=get_config().anthropic_api_key)
    model = get_config().agent.model

    deduped = sorted(set(names))
    batches = [deduped[i : i + batch_size] for i in range(0, len(deduped), batch_size)]

    accepted: set[str] = set()
    with concurrent.futures.ThreadPoolExecutor(max_workers=max_workers) as executor:
        futures = [executor.submit(_classify_batch, client, model, batch) for batch in batches]
        for future in concurrent.futures.as_completed(futures):
            try:
                accepted.update(future.result())
            except Exception as e:
                logger.warning("Classification batch failed: %s", e)

    return sorted(accepted)


def slugify_root(root: str) -> str:
    """Filesystem slug for a root category name: strip a Category: prefix, casefold,
    spaces/underscores -> hyphens, drop anything but [a-z0-9-]. Deterministic."""
    name = root.split(":", 1)[-1] if root.lower().startswith("category:") else root
    slug = re.sub(r"[_\s]+", "-", name.strip().casefold())
    slug = re.sub(r"[^a-z0-9-]", "", slug)
    return slug


def expansion_file_path(root: str) -> Path:
    """Path to the static expansion file for ``root`` (used by both writer and loader)."""
    return EXPANSIONS_DIR / f"{slugify_root(root)}.json"


def write_expansion_file(root: str, categories: list[str], *, max_depth: int | None) -> Path:
    """Write the deterministic, sorted, deduplicated expansion file and return its path.

    Overwrites the file wholesale — never appends/merges — so a fixed crawl+classification
    result produces identical file content run-to-run except ``generated_at``.
    """
    root_name = root.split(":", 1)[-1] if root.lower().startswith("category:") else root

    EXPANSIONS_DIR.mkdir(parents=True, exist_ok=True)

    accepted = sorted(set(categories) | {root_name})
    data = {
        "root": root_name,
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "max_depth": max_depth,
        "categories": accepted,
    }

    path = expansion_file_path(root)
    with path.open("w", encoding="utf-8") as f:
        json.dump(data, f, indent=2, ensure_ascii=False, sort_keys=True)
        f.write("\n")

    return path


def load_expansion(name: str) -> list[str] | None:
    """Return the discovered category-name list for a root ``name`` if an expansion file
    exists (data/category_expansions/<slug>.json), else None. Pure read; no network.
    Malformed/unreadable file -> log a warning and return None (fall back to direct match)."""
    path = expansion_file_path(name)
    if not path.exists():
        return None

    try:
        with path.open("r", encoding="utf-8") as f:
            data = json.load(f)
        categories = data["categories"]
        if not isinstance(categories, list) or not all(isinstance(c, str) for c in categories):
            raise ValueError(f"'categories' must be a list of strings, got {categories!r}")
        return categories
    except (OSError, json.JSONDecodeError, KeyError, ValueError) as e:
        logger.warning("Ignoring unreadable expansion file %s: %s", path, e)
        return None
