"""
Image Feedback Processor — translates casual user feedback into structured style guidance.

Flow:
1. User views a generated image in the dashboard review gallery
2. User types natural language feedback ("too clinical", "needs warmer colors")
3. This module reads the variant's visual_style to determine which style notes file to update
4. Claude receives the current notes + feedback and returns an updated version
5. The correct file is overwritten — strategies pick up changes on next generation

Style notes are split by strategy:
- style_notes_global.md  — applies to ALL strategies (brand identity, cross-cutting prefs)
- style_notes_photo.md   — photography, illustration, mixed_media (Imagen/DALL-E)
- style_notes_graphic.md — text_heavy, abstract, screen_capture (HTML/CSS)

The LLM layer handles:
- Deduplication (won't add "no split screens" twice)
- Translation to prompt-friendly language ("too clinical" → specific guidance)
- Categorization into the existing sections (What We Like / Don't Like / General Direction)
"""

from __future__ import annotations

from pathlib import Path

from anthropic import Anthropic


STYLE_REFS_DIR = Path("data/style_references")

# Maps visual_style taxonomy values to their style notes file
VISUAL_STYLE_TO_NOTES_FILE = {
    "photography": "style_notes_photo.md",
    "illustration": "style_notes_photo.md",
    "mixed_media": "style_notes_photo.md",
    "text_heavy": "style_notes_graphic.md",
    "abstract": "style_notes_graphic.md",
    "screen_capture": "style_notes_graphic.md",
}

FEEDBACK_SYSTEM_PROMPT = """You are a creative director maintaining a style guide for AI-generated ad images.

You will receive:
1. The current style notes file (markdown)
2. A piece of feedback from a reviewer about a specific generated image

Your job is to update the style notes file to incorporate this feedback. Rules:

- DEDUPLICATE: If the feedback overlaps with an existing bullet, strengthen/refine the existing one instead of adding a duplicate.
- TRANSLATE: Convert casual feedback into specific, actionable guidance that an image generator can follow.
  Example: "too clinical" → "Avoid sterile, hospital-like environments. Prefer warm therapy offices with natural light, plants, and wood tones."
  Example: "the text is hard to read" → "Ensure minimum contrast ratio between text and background. Use dark text on light backgrounds or vice versa — never mid-tone on mid-tone."
- CATEGORIZE: Place guidance in the right section:
  - "What We Like" — things to do MORE of
  - "What We Don't Like" — things to AVOID (negative prompts)
  - "General Direction" — overall strategy shifts
- PRESERVE: Keep all existing notes that aren't contradicted by the new feedback. If new feedback contradicts an old note, update the old note.
- FORMAT: Output the complete updated markdown file. Keep the same structure with the same headers. Keep bullets concise (1-2 sentences each).
- Do NOT add commentary, explanations, or metadata. Output ONLY the updated markdown file contents."""

FEEDBACK_USER_PROMPT = """Here is the current style notes file:

---
{current_notes}
---

A reviewer just provided this feedback on a generated image:

"{feedback}"

{variant_context}

Output the updated style notes file incorporating this feedback."""


class FeedbackProcessor:
    """Processes user feedback on generated images and updates style guidance."""

    def __init__(self):
        self.client = Anthropic()

    def process_feedback(
        self,
        feedback: str,
        variant_id: str | None = None,
        visual_style: str | None = None,
        strategy_name: str | None = None,
        taxonomy: dict | None = None,
    ) -> dict:
        """
        Process user feedback and update the appropriate style notes file.

        Routes feedback to the correct file based on the variant's visual_style:
        - photography/illustration/mixed_media → style_notes_photo.md
        - text_heavy/abstract/screen_capture → style_notes_graphic.md
        - unknown/None → style_notes_global.md

        Args:
            feedback: Natural language feedback from the reviewer
            variant_id: Optional variant ID for context
            visual_style: The variant's visual_style taxonomy value (determines routing)
            strategy_name: Which strategy generated the image (for context in prompt)
            taxonomy: Optional taxonomy dict for additional context

        Returns:
            Dict with updated_notes content and which file was updated
        """
        # Determine which file to update based on visual_style
        notes_file = self._resolve_notes_file(visual_style)
        notes_path = STYLE_REFS_DIR / notes_file

        # Read current notes from the target file
        current_notes = self._read_notes(notes_path)

        # Build context about the variant
        variant_context = self._build_variant_context(visual_style, strategy_name, taxonomy)

        # Ask Claude to synthesize the feedback
        response = self.client.messages.create(
            model="claude-sonnet-4-20250514",
            max_tokens=2000,
            system=FEEDBACK_SYSTEM_PROMPT,
            messages=[
                {
                    "role": "user",
                    "content": FEEDBACK_USER_PROMPT.format(
                        current_notes=current_notes,
                        feedback=feedback,
                        variant_context=variant_context,
                    ),
                }
            ],
        )

        updated_notes = response.content[0].text.strip()

        # Strip markdown fences if Claude wraps the output
        if updated_notes.startswith("```"):
            lines = updated_notes.split("\n")
            if lines[0].startswith("```"):
                lines = lines[1:]
            if lines and lines[-1].strip() == "```":
                lines = lines[:-1]
            updated_notes = "\n".join(lines).strip()

        # Write updated notes back to the correct file
        self._write_notes(notes_path, updated_notes)

        return {
            "updated_notes": updated_notes,
            "notes_file": notes_file,
            "visual_style": visual_style,
        }

    def _resolve_notes_file(self, visual_style: str | None) -> str:
        """Determine which style notes file to update based on visual_style."""
        if visual_style and visual_style in VISUAL_STYLE_TO_NOTES_FILE:
            return VISUAL_STYLE_TO_NOTES_FILE[visual_style]
        return "style_notes_global.md"

    def _read_notes(self, path: Path) -> str:
        """Read a style notes file, returning default content if missing."""
        if path.exists():
            return path.read_text().strip()
        # Return minimal scaffold
        name = path.stem.replace("style_notes_", "").title()
        return (
            f"# Style Notes — {name}\n\n"
            f"## What We Like\n- (no notes yet)\n\n"
            f"## What We Don't Like\n- (no notes yet)\n\n"
            f"## General Direction\n- (no notes yet)\n"
        )

    def _build_variant_context(
        self,
        visual_style: str | None,
        strategy_name: str | None,
        taxonomy: dict | None,
    ) -> str:
        """Build optional context string about the variant."""
        parts = []
        if visual_style:
            parts.append(f"Visual style: {visual_style}.")
        if strategy_name:
            parts.append(f"Generated using the '{strategy_name}' strategy.")
        if taxonomy:
            relevant = {
                k: v
                for k, v in taxonomy.items()
                if k in ("color_mood", "subject_matter", "tone")
                and v  # skip empty values
            }
            if relevant:
                tags = ", ".join(f"{k}={v}" for k, v in relevant.items())
                parts.append(f"Variant tags: {tags}")
        return " ".join(parts) if parts else ""

    def _write_notes(self, path: Path, content: str) -> None:
        """Write updated style notes back to file."""
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(content + "\n")

    def get_all_notes(self) -> dict:
        """Return all style notes files for display in UI."""
        result = {}
        for filename in ("style_notes_global.md", "style_notes_photo.md", "style_notes_graphic.md"):
            path = STYLE_REFS_DIR / filename
            label = filename.replace("style_notes_", "").replace(".md", "")
            result[label] = self._read_notes(path)
        return result
