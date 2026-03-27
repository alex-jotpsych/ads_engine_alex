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

import base64
import json
import shutil
from pathlib import Path

from anthropic import Anthropic


STYLE_REFS_DIR = Path("data/style_references")
BRAND_CONFIG_PATH = STYLE_REFS_DIR / "brand_config.json"

# Maps visual_style taxonomy values to their liked image subdirectory
VISUAL_STYLE_TO_LIKED_DIR = {
    "photography": "liked_photo",
    "illustration": "liked_illustration",
    "mixed_media": "liked_photo",
    "text_heavy": "liked_graphic",
    "abstract": "liked_graphic",
    "screen_capture": "liked_graphic",
}

# Maps visual_style taxonomy values to their style notes file
VISUAL_STYLE_TO_NOTES_FILE = {
    "photography": "style_notes_photo.md",
    "illustration": "style_notes_illustration.md",
    "mixed_media": "style_notes_photo.md",
    "text_heavy": "style_notes_graphic.md",
    "abstract": "style_notes_graphic.md",
    "screen_capture": "style_notes_graphic.md",
}

FEEDBACK_SYSTEM_PROMPT = """You are a creative director maintaining a style guide for AI-generated ad images.

You will receive:
1. The current style notes file (markdown)
2. The generated image the reviewer is commenting on (if available)
3. A piece of feedback from a reviewer about that image

When an image is provided, look at it carefully to understand exactly what the reviewer is referring to. Use what you see to write more specific, actionable guidance than you could from the text feedback alone.

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

LIKE_SYSTEM_PROMPT = """You are a creative director maintaining a style guide for AI-generated ad images.

A reviewer has LIKED a generated image — they want to see MORE of this style in future generations.

You will receive:
1. The current style notes file (markdown)
2. The liked image
3. An optional note from the reviewer about what they liked

Your job is to examine the image carefully and update the "What We Like" section of the style notes to capture what makes this image good. Be specific about visual qualities you can see: color palette, composition, typography treatment, spacing, mood, lighting, etc.

Rules:
- DEDUPLICATE: Don't add guidance that already exists.
- BE SPECIFIC: "Warm earth tones with sage green accents" is better than "nice colors."
- FOCUS ON REPLICABLE QUALITIES: Describe things that can be reproduced — color relationships, layout patterns, typographic choices — not one-off content.
- PRESERVE: Keep all existing notes. Only add to "What We Like" unless the reviewer's note suggests changes elsewhere.
- FORMAT: Output the complete updated markdown file. Keep the same structure with the same headers. Keep bullets concise (1-2 sentences each).
- Do NOT add commentary, explanations, or metadata. Output ONLY the updated markdown file contents."""

LIKE_USER_PROMPT = """Here is the current style notes file:

---
{current_notes}
---

The reviewer liked this image{reviewer_note}. Examine it and update the style notes to reinforce what works.

Output the updated style notes file."""

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
        self.client = Anthropic(max_retries=3)

    def process_feedback(
        self,
        feedback: str,
        variant_id: str | None = None,
        visual_style: str | None = None,
        strategy_name: str | None = None,
        taxonomy: dict | None = None,
        asset_path: str | None = None,
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
            asset_path: Optional path to the generated image file

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

        # Build message content — multimodal if image is available
        message_content = self._build_message_content(
            current_notes, feedback, variant_context, asset_path
        )

        # Ask Claude to synthesize the feedback
        response = self.client.messages.create(
            model="claude-sonnet-4-20250514",
            max_tokens=2000,
            system=FEEDBACK_SYSTEM_PROMPT,
            messages=[{"role": "user", "content": message_content}],
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

        # Check if feedback targets quantitative brand parameters (logo size, font size, etc.)
        config_updates = self._maybe_update_brand_config(feedback, asset_path)

        return {
            "updated_notes": updated_notes,
            "notes_file": notes_file,
            "visual_style": visual_style,
            "config_updates": config_updates,
        }

    def process_like(
        self,
        visual_style: str | None = None,
        asset_path: str | None = None,
        note: str | None = None,
        taxonomy: dict | None = None,
        variant_id: str | None = None,
    ) -> dict:
        """
        Process a liked image — save as reference and update style notes positively.

        1. Copies the image to the appropriate liked_photo/ or liked_graphic/ directory
        2. For HTML/CSS variants, also copies the source .html as a few-shot reference
        3. Sends the image to Claude to extract what's good and update style notes

        Args:
            visual_style: The variant's visual_style taxonomy value
            asset_path: Path to the generated image file
            note: Optional reviewer note about what they liked
            taxonomy: Optional taxonomy dict for context
            variant_id: Optional variant ID for naming

        Returns:
            Dict with updated_notes, notes_file, and reference_path
        """
        # Determine target directory and notes file
        liked_dir_name = VISUAL_STYLE_TO_LIKED_DIR.get(visual_style or "", "liked_photo")
        liked_dir = STYLE_REFS_DIR / liked_dir_name
        liked_dir.mkdir(parents=True, exist_ok=True)

        notes_file = self._resolve_notes_file(visual_style)
        notes_path = STYLE_REFS_DIR / notes_file

        reference_path = None

        # Copy the image as a reference
        if asset_path:
            src = Path(asset_path)
            if src.exists():
                # Name it by variant_id or incrementing number
                existing = list(liked_dir.glob("*.png")) + list(liked_dir.glob("*.jpg"))
                idx = len(existing) + 1
                dest_name = f"liked_{idx}{src.suffix}"
                dest = liked_dir / dest_name
                shutil.copy2(src, dest)
                reference_path = str(dest)

                # For HTML/CSS variants, also copy the source HTML
                html_src = src.with_suffix(".html")
                if html_src.exists():
                    html_dest = liked_dir / f"liked_{idx}.html"
                    shutil.copy2(html_src, html_dest)

        # Update style notes with positive signal via Claude
        current_notes = self._read_notes(notes_path)

        reviewer_note = f" and said: \"{note}\"" if note else ""
        text_prompt = LIKE_USER_PROMPT.format(
            current_notes=current_notes,
            reviewer_note=reviewer_note,
        )

        # Build multimodal message with the liked image
        if asset_path:
            message_content = self._build_image_message(
                asset_path, "The reviewer LIKED this image:", text_prompt
            )
        else:
            message_content = text_prompt

        response = self.client.messages.create(
            model="claude-sonnet-4-20250514",
            max_tokens=2000,
            system=LIKE_SYSTEM_PROMPT,
            messages=[{"role": "user", "content": message_content}],
        )

        updated_notes = self._strip_fences(response.content[0].text.strip())
        self._write_notes(notes_path, updated_notes)

        return {
            "updated_notes": updated_notes,
            "notes_file": notes_file,
            "visual_style": visual_style,
            "reference_path": reference_path,
        }

    def _build_image_message(
        self, asset_path: str, preamble: str, text_prompt: str
    ) -> list[dict] | str:
        """Build a multimodal message with an image. Returns plain text if image fails to load."""
        image_path = Path(asset_path)
        if not image_path.exists():
            return text_prompt

        try:
            image_bytes = image_path.read_bytes()
            media_type = "image/png" if image_path.suffix == ".png" else "image/jpeg"
            image_b64 = base64.b64encode(image_bytes).decode("utf-8")
            return [
                {"type": "text", "text": preamble},
                {
                    "type": "image",
                    "source": {
                        "type": "base64",
                        "media_type": media_type,
                        "data": image_b64,
                    },
                },
                {"type": "text", "text": text_prompt},
            ]
        except Exception:
            return text_prompt

    def _build_message_content(
        self,
        current_notes: str,
        feedback: str,
        variant_context: str,
        asset_path: str | None,
    ) -> list[dict] | str:
        """Build the user message — includes the image if asset_path is available."""
        text_prompt = FEEDBACK_USER_PROMPT.format(
            current_notes=current_notes,
            feedback=feedback,
            variant_context=variant_context,
        )

        if not asset_path:
            return text_prompt

        return self._build_image_message(
            asset_path,
            "Here is the generated image the reviewer is commenting on:",
            text_prompt,
        )

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

    def _maybe_update_brand_config(self, feedback: str, asset_path: str | None = None) -> dict | None:
        """Check if feedback targets quantitative brand params and update brand_config.json.

        Uses Claude to interpret feedback like "make the logo 2x bigger" or
        "increase body text size" into concrete config changes.
        """
        current_config = {}
        if BRAND_CONFIG_PATH.exists():
            current_config = json.loads(BRAND_CONFIG_PATH.read_text())

        if not current_config:
            return None

        message_content = [
            {
                "type": "text",
                "text": f"""You are a design system engineer. A reviewer gave feedback on an ad image.

Current brand_config.json:
```json
{json.dumps(current_config, indent=2)}
```

Reviewer feedback: "{feedback}"

Does this feedback require changes to any numeric values in brand_config.json?
Examples of feedback that WOULD require changes:
- "make the logo bigger" → increase logo.width_px
- "body text is too small" → increase body_size_range
- "too much padding" → decrease layout.padding_min_px
- "make the logo 2x bigger" → double logo.width_px

Examples that would NOT (these are style notes, not config):
- "use warmer colors" → no config change
- "the CTA doesn't pop" → no config change
- "too clinical" → no config change

If changes are needed, respond with ONLY the updated JSON (complete file, not a patch).
If no changes are needed, respond with exactly: NO_CHANGES""",
            },
        ]

        # Include the image for context if available
        if asset_path:
            img_path = Path(asset_path)
            if img_path.exists():
                try:
                    img_bytes = img_path.read_bytes()
                    media_type = "image/png" if img_path.suffix == ".png" else "image/jpeg"
                    message_content.insert(0, {
                        "type": "text",
                        "text": "Here is the ad the reviewer is commenting on:",
                    })
                    message_content.insert(1, {
                        "type": "image",
                        "source": {
                            "type": "base64",
                            "media_type": media_type,
                            "data": base64.b64encode(img_bytes).decode("utf-8"),
                        },
                    })
                except Exception:
                    pass

        response = self.client.messages.create(
            model="claude-sonnet-4-20250514",
            max_tokens=1000,
            messages=[{"role": "user", "content": message_content}],
        )

        result_text = response.content[0].text.strip()

        if result_text == "NO_CHANGES":
            return None

        # Parse the updated config
        try:
            # Strip markdown fences if present
            if result_text.startswith("```"):
                lines = result_text.split("\n")
                if lines[0].startswith("```"):
                    lines = lines[1:]
                if lines and lines[-1].strip() == "```":
                    lines = lines[:-1]
                result_text = "\n".join(lines).strip()

            updated_config = json.loads(result_text)
            BRAND_CONFIG_PATH.write_text(json.dumps(updated_config, indent=4) + "\n")
            return updated_config
        except (json.JSONDecodeError, Exception):
            return None

    def _write_notes(self, path: Path, content: str) -> None:
        """Write updated style notes back to file."""
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(content + "\n")

    def get_all_notes(self) -> dict:
        """Return all style notes files for display in UI."""
        result = {}
        for filename in ("style_notes_global.md", "style_notes_photo.md", "style_notes_illustration.md", "style_notes_graphic.md"):
            path = STYLE_REFS_DIR / filename
            label = filename.replace("style_notes_", "").replace(".md", "")
            result[label] = self._read_notes(path)
        return result
