"""
Flask web application for reviewing and approving article edits.
"""

import json
from datetime import datetime
from pathlib import Path
from typing import Any

from flask import Flask, jsonify, redirect, render_template, request, session, url_for
from flask_cors import CORS

from wiki_cite.agent import ClaudeAgent
from wiki_cite.article_picker import ArticlePicker
from wiki_cite.config import get_config
from wiki_cite.models import Article, EditProposal
from wiki_cite.wikipedia_push import WikipediaPushService


def create_app() -> Flask:
    """Create and configure the Flask application."""
    app = Flask(__name__)
    config = get_config()

    app.config["SECRET_KEY"] = config.flask_secret_key
    CORS(app)

    # In-memory storage for proposals (in production, use a database)
    proposals: dict[str, EditProposal] = {}

    # Initialize services
    article_picker = ArticlePicker()
    agent = ClaudeAgent()
    push_service = WikipediaPushService()

    @app.route("/")
    def index():
        """Home page showing queue of proposals."""
        return render_template("index.html", proposals=list(proposals.values()))

    @app.route("/api/fetch-article")
    def fetch_article():
        """Fetch a new article and generate edit proposals."""
        try:
            # Get one candidate article
            candidates = list(article_picker.fetch_candidates(limit=1))

            if not candidates:
                return jsonify({"error": "No candidate articles found"}), 404

            candidate = candidates[0]

            # Convert to Article
            article = Article(
                title=candidate.title,
                url=candidate.url,
                wikitext=candidate.wikitext,
                revision_id=candidate.revision_id,
                fetched_at=candidate.fetched_at,
            )

            # Analyze with Claude
            proposal = agent.analyze_article(article)

            # Store the proposal
            proposals[proposal.id] = proposal

            return jsonify({
                "proposal_id": proposal.id,
                "article_title": article.title,
                "edit_count": len(proposal.edits),
            })

        except Exception as e:
            return jsonify({"error": str(e)}), 500

    @app.route("/api/proposals")
    def get_proposals():
        """Get all proposals."""
        return jsonify([
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
        ])

    @app.route("/api/proposals/<proposal_id>")
    def get_proposal(proposal_id: str):
        """Get a specific proposal."""
        if proposal_id not in proposals:
            return jsonify({"error": "Proposal not found"}), 404

        proposal = proposals[proposal_id]

        return jsonify({
            "id": proposal.id,
            "article": {
                "title": proposal.article.title,
                "url": proposal.article.url,
                "wikitext": proposal.article.wikitext,
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
        })

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
            return jsonify({"success": True, "message": message})
        else:
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
    app.run(debug=True, host="0.0.0.0", port=5000)


if __name__ == "__main__":
    main()
