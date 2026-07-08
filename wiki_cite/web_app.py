"""
Flask web application for reviewing and approving article edits.
"""

import json
import os
from collections.abc import Iterator
from datetime import datetime

from flask import Flask, Response, jsonify, render_template, request, stream_with_context
from flask_cors import CORS

from wiki_cite.agent import ClaudeAgent
from wiki_cite.article_picker import ArticlePicker, build_focused_excerpt
from wiki_cite.config import get_config
from wiki_cite.models import Article, EditProposal
from wiki_cite.seen_store import SeenStore
from wiki_cite.source_finder import SourceFinder, extract_citation_url
from wiki_cite.wikipedia_push import WikipediaPushService


def create_app() -> Flask:
    """Create and configure the Flask application."""
    app = Flask(__name__)
    config = get_config()

    app.config["SECRET_KEY"] = config.flask_secret_key
    CORS(app)

    # In-memory storage for proposals (in production, use a database)
    proposals: dict[str, EditProposal] = {}

    # In-memory include/exclude category override, seeded from config.yaml. Mutating
    # this never touches config.yaml, so it resets to the config defaults on restart.
    category_overrides = {
        "include": list(config.article_selection.include_categories),
        "exclude": list(config.article_selection.exclude_categories),
    }

    # Initialize services
    seen_store = SeenStore(config.seen_db_path)
    article_picker = ArticlePicker(seen_store=seen_store)
    agent = ClaudeAgent()
    push_service = WikipediaPushService()
    source_finder = SourceFinder()

    @app.route("/")
    def index():
        """Home page showing queue of proposals."""
        return render_template("index.html", proposals=list(proposals.values()))

    def scan_events() -> Iterator[dict]:
        """Scan candidate articles, yielding progress events for the UI.

        Keeps scanning (up to agent.max_candidates_per_fetch) until it finds one
        where Claude could confidently source at least one citation. Emits an
        event per candidate so a reviewer can watch the agent work: which page it
        is reading, its flagged {{Citation needed}} claims, what it skipped, and
        the final selection. The terminal event is "selected", "failed", or "error".
        """
        max_scan = config.agent.max_candidates_per_fetch
        skipped: list[str] = []

        try:
            yield {"type": "scan_start", "max": max_scan, "category": config.article_selection.category}

            found_any = False
            for candidate in article_picker.fetch_candidates(
                limit=max_scan,
                include_categories=category_overrides["include"],
                exclude_categories=category_overrides["exclude"],
            ):
                found_any = True
                scanned = len(skipped) + 1
                # Show the same focused excerpt Claude sees: lead + flagged paragraphs.
                excerpt = build_focused_excerpt(candidate.wikitext)
                preview = [line for line in excerpt.splitlines() if line.strip()][:14]

                yield {
                    "type": "candidate",
                    "title": candidate.title,
                    "url": candidate.url,
                    "revision_id": candidate.revision_id,
                    "body_lines": candidate.body_line_count,
                    "claims": candidate.citation_needed_claims[:3],
                    "preview": preview,
                    "scanned": scanned,
                    "skipped": len(skipped),
                }

                article = Article(
                    title=candidate.title,
                    url=candidate.url,
                    wikitext=candidate.wikitext,
                    revision_id=candidate.revision_id,
                    fetched_at=candidate.fetched_at,
                    citation_needed_claims=candidate.citation_needed_claims,
                )

                yield {"type": "analyzing", "title": candidate.title, "model": config.agent.model, "claims": candidate.citation_needed_claims[:3]}

                # Stream the agent's own progress (source searches, model call, edits).
                proposal = None
                for event in agent.analyze_article_events(article):
                    if event["type"] == "analyzed":
                        proposal = event["proposal"]
                    else:
                        yield {**event, "title": candidate.title}

                if proposal.has_confident_citation():
                    proposals[proposal.id] = proposal
                    seen_store.mark_seen(candidate.title, candidate.revision_id, "selected")
                    yield {
                        "type": "selected",
                        "proposal_id": proposal.id,
                        "title": candidate.title,
                        "edit_count": len(proposal.edits),
                        "scanned": scanned,
                    }
                    return

                skipped.append(candidate.title)
                seen_store.mark_seen(candidate.title, candidate.revision_id, "skipped")
                yield {"type": "skipped", "title": candidate.title, "reason": "no confidently-sourced citation", "edit_count": len(proposal.edits)}

            if not found_any:
                yield {"type": "failed", "error": "No candidate articles found", "skipped": []}
            else:
                yield {
                    "type": "failed",
                    "error": f"Scanned {len(skipped)} candidate article(s) but couldn't confidently source a citation for any of them.",
                    "skipped": skipped,
                }
        except Exception as e:
            yield {"type": "error", "error": str(e)}

    @app.route("/api/fetch-article")
    def fetch_article():
        """Fetch a new article and generate edit proposals (single JSON result)."""
        terminal = None
        for event in scan_events():
            if event["type"] == "selected":
                return jsonify(
                    {
                        "proposal_id": event["proposal_id"],
                        "article_title": event["title"],
                        "edit_count": event["edit_count"],
                        "scanned": event["scanned"],
                    }
                )
            terminal = event

        if terminal and terminal["type"] == "error":
            return jsonify({"error": terminal["error"]}), 500
        return jsonify({"error": terminal["error"], "skipped": terminal.get("skipped", [])}), 404

    @app.route("/api/fetch-article/stream")
    def fetch_article_stream():
        """Stream fetch progress as Server-Sent Events, so the UI can show the
        agent working in real time ("look over the agent's shoulder")."""

        def generate() -> Iterator[str]:
            for event in scan_events():
                yield f"data: {json.dumps(event)}\n\n"

        return Response(
            stream_with_context(generate()),
            mimetype="text/event-stream",
            headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
        )

    @app.route("/api/proposals")
    def get_proposals():
        """Get all proposals."""
        return jsonify(
            [
                {
                    "id": p.id,
                    "title": p.article.title,
                    "url": p.article.url,
                    "status": p.status,
                    "edit_count": len(p.edits),
                    "approved_count": len(p.get_approved_edits()),
                    "created_at": p.created_at.isoformat(),
                }
                for p in proposals.values()
            ]
        )

    @app.route("/api/proposals/<proposal_id>")
    def get_proposal(proposal_id: str):
        """Get a specific proposal."""
        if proposal_id not in proposals:
            return jsonify({"error": "Proposal not found"}), 404

        proposal = proposals[proposal_id]

        return jsonify(
            {
                "id": proposal.id,
                "article": {
                    "title": proposal.article.title,
                    "url": proposal.article.url,
                    "wikitext": proposal.article.wikitext,
                    "revision_id": proposal.article.revision_id,
                },
                "edits": [
                    {
                        "edit_type": edit.edit_type.value,
                        "original_text": edit.original_text,
                        "proposed_text": edit.proposed_text,
                        "rationale": edit.rationale,
                        "policy_reference": edit.policy_reference,
                        "confidence": edit.confidence,
                        "approved": edit.approved,
                    }
                    for edit in proposal.edits
                ],
                "status": proposal.status,
            }
        )

    @app.route("/api/categories/search")
    def search_categories():
        """Search Wikipedia Category-namespace page names by prefix, for the
        dashboard's search-and-select. Read-only; no local index."""
        q = request.args.get("q", "").strip()
        if not q:
            return jsonify({"error": "query parameter 'q' is required"}), 400
        try:
            pages = article_picker.site.allpages(prefix=q, namespace=14, limit=20)
            names = [p.name.split(":", 1)[-1] for p in pages]
        except Exception as e:
            return jsonify({"error": str(e)}), 502
        return jsonify({"categories": names})

    def _valid_category_list(value) -> bool:
        return isinstance(value, list) and all(isinstance(x, str) for x in value)

    @app.route("/api/settings/categories")
    def get_category_settings():
        """Return the active include/exclude lists (override if set, else the
        config.yaml defaults it was seeded from)."""
        return jsonify({"include": category_overrides["include"], "exclude": category_overrides["exclude"]})

    @app.route("/api/settings/categories", methods=["POST"])
    def set_category_settings():
        """Update the in-memory override. Rejects malformed payloads without
        mutating the previous override."""
        data = request.get_json(silent=True) or {}
        include = data.get("include", category_overrides["include"])
        exclude = data.get("exclude", category_overrides["exclude"])
        if not _valid_category_list(include) or not _valid_category_list(exclude):
            return jsonify({"error": "include and exclude must be lists of strings"}), 400
        category_overrides["include"] = list(include)
        category_overrides["exclude"] = list(exclude)
        return jsonify({"include": category_overrides["include"], "exclude": category_overrides["exclude"]})

    @app.route("/api/proposals/<proposal_id>/edits/<int:edit_index>/source-preview")
    def source_preview(proposal_id: str, edit_index: int):
        """Fetch a preview (title/description/site) of the source cited by an edit.

        Lets a reviewer sanity-check what the citation actually points to
        without leaving the dashboard.
        """
        if proposal_id not in proposals:
            return jsonify({"error": "Proposal not found"}), 404

        proposal = proposals[proposal_id]

        if edit_index < 0 or edit_index >= len(proposal.edits):
            return jsonify({"error": "Invalid edit index"}), 400

        edit = proposal.edits[edit_index]
        source_url = extract_citation_url(edit.proposed_text)

        if not source_url:
            return jsonify({"ok": False, "error": "No source URL found in this edit"})

        preview = source_finder.fetch_page_preview(source_url)
        return jsonify(preview)

    @app.route("/api/proposals/<proposal_id>/approve-edit/<int:edit_index>", methods=["POST"])
    def approve_edit(proposal_id: str, edit_index: int):
        """Approve a specific edit."""
        if proposal_id not in proposals:
            return jsonify({"error": "Proposal not found"}), 404

        proposal = proposals[proposal_id]

        if edit_index < 0 or edit_index >= len(proposal.edits):
            return jsonify({"error": "Invalid edit index"}), 400

        proposal.edits[edit_index].approved = True

        return jsonify({"success": True})

    @app.route("/api/proposals/<proposal_id>/reject-edit/<int:edit_index>", methods=["POST"])
    def reject_edit(proposal_id: str, edit_index: int):
        """Reject a specific edit."""
        if proposal_id not in proposals:
            return jsonify({"error": "Proposal not found"}), 404

        proposal = proposals[proposal_id]

        if edit_index < 0 or edit_index >= len(proposal.edits):
            return jsonify({"error": "Invalid edit index"}), 400

        proposal.edits[edit_index].approved = False

        return jsonify({"success": True})

    @app.route("/api/proposals/<proposal_id>/update-edit/<int:edit_index>", methods=["POST"])
    def update_edit(proposal_id: str, edit_index: int):
        """Update a specific edit's text."""
        if proposal_id not in proposals:
            return jsonify({"error": "Proposal not found"}), 404

        proposal = proposals[proposal_id]

        if edit_index < 0 or edit_index >= len(proposal.edits):
            return jsonify({"error": "Invalid edit index"}), 400

        data = request.get_json()
        if "proposed_text" in data:
            proposal.edits[edit_index].proposed_text = data["proposed_text"]

        return jsonify({"success": True})

    @app.route("/api/proposals/<proposal_id>/push", methods=["POST"])
    def push_proposal(proposal_id: str):
        """Push approved edits to Wikipedia."""
        if proposal_id not in proposals:
            return jsonify({"error": "Proposal not found"}), 404

        proposal = proposals[proposal_id]

        # Get approved edits
        approved_edits = proposal.get_approved_edits()

        if not approved_edits:
            return jsonify({"error": "No edits approved"}), 400

        # Apply edits
        modified_text = agent.apply_edits(proposal.article, approved_edits)

        # Push to Wikipedia
        success, message = push_service.push_edits(proposal, modified_text)

        if success:
            proposal.status = "pushed"
            proposal.reviewed_at = datetime.now()
            seen_store.mark_seen(proposal.article.title, proposal.article.revision_id, "pushed")
            return jsonify({"success": True, "message": message})

        return jsonify({"error": message}), 500

    @app.route("/api/proposals/<proposal_id>/preview")
    def preview_proposal(proposal_id: str):
        """Preview the diff for a proposal."""
        if proposal_id not in proposals:
            return jsonify({"error": "Proposal not found"}), 404

        proposal = proposals[proposal_id]
        approved_edits = proposal.get_approved_edits()

        if not approved_edits:
            return jsonify({"diff": "No edits approved"})

        # Apply edits
        modified_text = agent.apply_edits(proposal.article, approved_edits)

        # Generate diff
        diff = push_service.preview_diff(proposal, modified_text)

        return jsonify({"diff": diff})

    @app.route("/review/<proposal_id>")
    def review_proposal_page(proposal_id: str):
        """Review page for a specific proposal."""
        if proposal_id not in proposals:
            return "Proposal not found", 404

        return render_template("review.html", proposal_id=proposal_id)

    return app


def main():
    """Run the Flask application."""
    app = create_app()
    debug = os.environ.get("FLASK_DEBUG", "").lower() in ("1", "true", "yes")
    host = os.environ.get("FLASK_HOST", "127.0.0.1")
    app.run(debug=debug, host=host, port=5000)


if __name__ == "__main__":
    main()
