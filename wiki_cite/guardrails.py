"""
Edit Guardrails and validation for ensuring edits are minimal and safe.
"""

import difflib
import re

import mwparserfromhell

from wiki_cite.config import get_config
from wiki_cite.models import ProposedEdit


class EditGuardrails:
    """Validates that proposed edits meet safety and minimality requirements."""

    def __init__(self):
        """Initialize the guardrails."""
        self.config = get_config()

    def count_words(self, text: str) -> int:
        """Count words in text, excluding templates and wikimarkup.

        Args:
            text: The text to count words in

        Returns:
            Number of words
        """
        # Remove templates
        cleaned = re.sub(r"\{\{[^}]+\}\}", "", text)
        # Remove ref tags
        cleaned = re.sub(r"<ref[^>]*>.*?</ref>", "", cleaned, flags=re.DOTALL)
        cleaned = re.sub(r"<ref[^>]*/>", "", cleaned)
        # Remove wikilinks but keep text
        cleaned = re.sub(r"\[\[(?:[^|\]]+\|)?([^\]]+)\]\]", r"\1", cleaned)
        # Count words
        words = cleaned.split()
        return len(words)

    def calculate_similarity(self, text1: str, text2: str) -> float:
        """Calculate similarity ratio between two texts.

        Args:
            text1: First text
            text2: Second text

        Returns:
            Similarity ratio between 0 and 1
        """
        return difflib.SequenceMatcher(None, text1, text2).ratio()

    def count_removed_content(self, original: str, modified: str) -> int:
        """Count the amount of content removed (as a percentage).

        Args:
            original: Original text
            modified: Modified text

        Returns:
            Percentage of content removed (0-100)
        """
        original_words = self.count_words(original)
        if original_words == 0:
            return 0

        modified_words = self.count_words(modified)
        removed = max(0, original_words - modified_words)

        return int((removed / original_words) * 100)

    def is_citation_or_template(self, text: str) -> bool:
        """Check if text is primarily a citation or template.

        Args:
            text: The text to check

        Returns:
            True if text is a citation or template
        """
        # Check for ref tags
        if re.search(r"<ref[^>]*>", text, re.IGNORECASE):
            return True

        # Check for citation templates
        if re.search(r"\{\{cite", text, re.IGNORECASE):
            return True

        # Check if it's mostly templates
        wikicode = mwparserfromhell.parse(text)
        templates = list(wikicode.filter_templates())
        if templates and len(str(wikicode).strip()) > 0:
            template_ratio = sum(len(str(t)) for t in templates) / len(str(wikicode))
            if template_ratio > 0.7:  # 70% templates
                return True

        return False

    def validate_edit(
        self, edit: ProposedEdit, full_original: str, full_modified: str
    ) -> tuple[bool, str]:  # pylint: disable=unused-argument
        """Validate that an edit meets all guardrail requirements.

        Args:
            edit: The proposed edit to validate
            full_original: The full original article text (reserved for future use)
            full_modified: The full modified article text (reserved for future use)

        Returns:
            Tuple of (is_valid, reason_if_invalid)
        """
        # Skip validation for citation additions - they're always allowed
        if edit.edit_type.value == "citation":
            return True, ""

        # Calculate similarity
        similarity = self.calculate_similarity(edit.original_text, edit.proposed_text)
        if similarity < self.config.guardrails.min_similarity_ratio:
            return False, f"Edit changes too much (similarity: {similarity:.2f})"

        # Check for added words (excluding citations/templates)
        original_words = self.count_words(edit.original_text)
        proposed_words = self.count_words(edit.proposed_text)
        added_words = max(0, proposed_words - original_words)

        if added_words > self.config.guardrails.max_new_words:
            # Check if it's just citations/templates
            if not self.is_citation_or_template(edit.proposed_text):
                return False, f"Adds too many new words ({added_words} words)"

        # Check for content removal
        removal_pct = self.count_removed_content(edit.original_text, edit.proposed_text)
        if removal_pct > self.config.guardrails.max_content_removal_pct:
            return False, f"Removes too much content ({removal_pct}%)"

        return True, ""

    def validate_full_article_edit(self, original: str, modified: str) -> tuple[bool, str]:
        """Validate the entire article edit.

        Args:
            original: Original article text
            modified: Modified article text

        Returns:
            Tuple of (is_valid, reason_if_invalid)
        """
        # Check overall similarity
        similarity = self.calculate_similarity(original, modified)
        if similarity < self.config.guardrails.min_similarity_ratio:
            return False, f"Overall edit changes too much (similarity: {similarity:.2f})"

        # Check for content removal
        removal_pct = self.count_removed_content(original, modified)
        if removal_pct > self.config.guardrails.max_content_removal_pct:
            return False, f"Removes too much content ({removal_pct}%)"

        # Count added words (excluding citations)
        original_words = self.count_words(original)
        modified_words = self.count_words(modified)
        added_words = max(0, modified_words - original_words)

        if added_words > self.config.guardrails.max_new_words:
            return False, f"Adds too many new words ({added_words} words)"

        return True, ""

    def check_policy_violations(self, text: str) -> list[str]:
        """Check for obvious policy violations in text.

        Args:
            text: The text to check

        Returns:
            List of policy violations found
        """
        violations = []

        # Check for promotional language
        promotional_words = [
            "best",
            "greatest",
            "leading",
            "premier",
            "top-rated",
            "award-winning",
            "world-class",
            "cutting-edge",
            "revolutionary",
        ]
        text_lower = text.lower()
        for word in promotional_words:
            if word in text_lower:
                violations.append(f"Potential promotional language: '{word}'")

        # Check for peacock terms
        peacock_terms = [
            "clearly",
            "obviously",
            "undoubtedly",
            "of course",
            "naturally",
            "essentially",
            "basically",
        ]
        for term in peacock_terms:
            if term in text_lower:
                violations.append(f"Peacock term: '{term}'")

        # Check for weasel words
        weasel_words = [
            "some say",
            "many believe",
            "it is said",
            "critics say",
            "experts claim",
            "arguably",
        ]
        for weasel in weasel_words:
            if weasel in text_lower:
                violations.append(f"Weasel words: '{weasel}'")

        return violations
