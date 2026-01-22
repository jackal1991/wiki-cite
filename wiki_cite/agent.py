"""
Claude Agent for proposing minimal edits to Wikipedia articles.
"""

import json
import re
import uuid
from typing import Any

from anthropic import Anthropic

from wiki_cite.config import get_config
from wiki_cite.guardrails import EditGuardrails
from wiki_cite.models import Article, EditProposal, EditType, ProposedEdit
from wiki_cite.source_finder import SourceFinder


AGENT_SYSTEM_PROMPT = """You are a Wikipedia copyeditor and citation assistant. Your task is to make
MINIMAL improvements to stub articles. You must follow these strict rules:

## ABSOLUTE CONSTRAINTS
1. DO NOT add new facts, claims, or information
2. DO NOT expand the article's scope or coverage
3. DO NOT add new sentences or paragraphs of content
4. DO NOT remove content unless it clearly violates policy
5. PRESERVE the author's voice and intent

## PERMITTED EDITS

### Citation Addition
- Find reliable sources that verify EXISTING claims in the article
- Add <ref> tags with proper {{cite}} templates
- Only cite claims already present—never add information from sources

### Grammar & Spelling
- Fix grammatical errors
- Correct spelling mistakes
- Fix punctuation

### Style (per WP:MOS)
- Fix capitalization issues
- Correct date formats
- Fix number formatting
- Ensure proper use of italics/bold

### Wikilinks
- Add [[wikilinks]] to existing mentions of notable topics
- Do not over-link (link first occurrence only)
- Do not link common words

### Policy Compliance
- Flag or remove unsourced contentious claims (WP:BLP)
- Neutralize promotional language (WP:NPOV) with minimal rewording
- Fix any copyright concerns

### Formatting
- Add/fix categories
- Correct stub template
- Fix malformed wikitext

## OUTPUT FORMAT
You must respond with a JSON array of edits. Each edit must have:
- edit_type: one of "citation", "grammar", "style", "wikilink", "policy", "formatting"
- original_text: the exact text being changed
- proposed_text: the replacement text
- rationale: explanation for the change
- policy_reference: relevant Wikipedia policy (if applicable)
- confidence: "high", "medium", or "low"

Example response:
```json
[
  {
    "edit_type": "citation",
    "original_text": "were accused of raping a white woman in 1949",
    "proposed_text": "were accused of raping a white woman in 1949<ref>{{cite book |last=Green |first=Ben |title=Before His Time |year=1999 |publisher=Free Press}}</ref>",
    "rationale": "Adding citation for existing claim about the accusation",
    "policy_reference": "WP:CITE",
    "confidence": "high"
  },
  {
    "edit_type": "grammar",
    "original_text": "The four men was arrested",
    "proposed_text": "The four men were arrested",
    "rationale": "Subject-verb agreement error",
    "policy_reference": null,
    "confidence": "high"
  }
]
```

If you cannot verify a claim with reliable sources, note this in your response but DO NOT
remove the claim unless it violates BLP policy.

Respond ONLY with the JSON array, no other text.
"""


class ClaudeAgent:
    """Claude-powered agent for proposing article edits."""

    def __init__(self):
        """Initialize the agent."""
        self.config = get_config()
        self.client = Anthropic(api_key=self.config.anthropic_api_key)
        self.source_finder = SourceFinder()
        self.guardrails = EditGuardrails()

    def _extract_json_from_response(self, text: str) -> list[dict[str, Any]]:
        """Extract JSON array from Claude's response.

        Args:
            text: The response text

        Returns:
            List of edit dictionaries
        """
        # Try to find JSON array in the response
        # Look for content between ```json and ``` or just raw JSON
        json_match = re.search(r"```json\s*(.*?)\s*```", text, re.DOTALL)
        if json_match:
            json_text = json_match.group(1)
        else:
            # Try to find raw JSON array
            json_match = re.search(r"\[\s*\{.*?\}\s*\]", text, re.DOTALL)
            if json_match:
                json_text = json_match.group(0)
            else:
                json_text = text

        try:
            return json.loads(json_text)
        except json.JSONDecodeError as e:
            print(f"Failed to parse JSON: {e}")
            print(f"Response text: {text[:500]}")
            return []

    def _build_sources_context(self, article: Article) -> str:
        """Build context about available sources for the article.

        Args:
            article: The article to find sources for

        Returns:
            String describing available sources
        """
        # Extract claims from the article
        claims = self.source_finder.extract_claims(article.wikitext)

        if not claims:
            return "No clear factual claims found to cite."

        # Find sources for key claims (limit to first 3 to avoid overwhelming)
        sources_context = "## Available Sources for Citation\n\n"

        for i, claim in enumerate(claims[:3], 1):
            sources = self.source_finder.find_sources_for_claim(claim, max_results=2)

            if sources:
                sources_context += f"### Claim {i}: \"{claim[:100]}...\"\n"
                for j, source in enumerate(sources, 1):
                    citation = source.to_citation_template()
                    sources_context += f"{j}. {citation}\n"
                sources_context += "\n"

        return sources_context

    def analyze_article(self, article: Article) -> EditProposal:
        """Analyze an article and propose minimal edits.

        Args:
            article: The article to analyze

        Returns:
            EditProposal with suggested edits
        """
        # Build context with available sources
        sources_context = self._build_sources_context(article)

        # Prepare the prompt
        user_prompt = f"""Please analyze this Wikipedia article and propose minimal edits:

## Article Title
{article.title}

## Article Text
{article.wikitext}

{sources_context}

Remember:
1. Only cite EXISTING claims, never add new information
2. Keep edits minimal - grammar, style, wikilinks, citations only
3. Do not change the article's scope or add new content
4. Respond with JSON only

Propose your edits now:
"""

        # Call Claude
        try:
            response = self.client.messages.create(
                model=self.config.agent.model,
                max_tokens=4096,
                system=AGENT_SYSTEM_PROMPT,
                messages=[
                    {"role": "user", "content": user_prompt}
                ]
            )

            response_text = response.content[0].text

            # Parse the JSON response
            edits_data = self._extract_json_from_response(response_text)

            # Convert to ProposedEdit objects
            proposed_edits = []
            for edit_data in edits_data[:self.config.agent.max_edits_per_article]:
                try:
                    edit_type = EditType[edit_data["edit_type"].upper().replace(" ", "_")]
                except (KeyError, ValueError):
                    # Skip invalid edit types
                    continue

                edit = ProposedEdit(
                    edit_type=edit_type,
                    original_text=edit_data.get("original_text", ""),
                    proposed_text=edit_data.get("proposed_text", ""),
                    rationale=edit_data.get("rationale", ""),
                    policy_reference=edit_data.get("policy_reference"),
                    confidence=edit_data.get("confidence", "medium"),
                    source=None,  # Could extract from citation if needed
                )

                # Validate the edit
                is_valid, reason = self.guardrails.validate_edit(
                    edit,
                    article.wikitext,
                    article.wikitext  # For now, just check individual edit
                )

                if is_valid:
                    proposed_edits.append(edit)
                else:
                    print(f"Rejected edit: {reason}")

            # Create the proposal
            proposal = EditProposal(
                id=str(uuid.uuid4()),
                article=article,
                edits=proposed_edits,
                status="pending",
            )

            return proposal

        except Exception as e:
            print(f"Error calling Claude API: {e}")
            # Return empty proposal
            return EditProposal(
                id=str(uuid.uuid4()),
                article=article,
                edits=[],
                status="rejected",
                reviewer_notes=f"Error: {e}"
            )

    def apply_edits(self, article: Article, edits: list[ProposedEdit]) -> str:
        """Apply approved edits to an article.

        Args:
            article: The original article
            edits: List of approved edits to apply

        Returns:
            Modified article wikitext
        """
        modified_text = article.wikitext

        # Sort edits by position in text (to apply from end to start)
        # This prevents position shifts from affecting later edits
        sorted_edits = sorted(
            edits,
            key=lambda e: modified_text.find(e.original_text),
            reverse=True
        )

        for edit in sorted_edits:
            # Find and replace the original text
            if edit.original_text in modified_text:
                modified_text = modified_text.replace(
                    edit.original_text,
                    edit.proposed_text,
                    1  # Replace only first occurrence
                )

        return modified_text
