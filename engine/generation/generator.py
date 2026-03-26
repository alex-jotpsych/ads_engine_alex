"""
Creative Generator — takes briefs, produces ad variants with auto-taxonomy tagging.

Copy generation uses Claude (Anthropic).
Image generation is handled by pluggable strategies (see strategies.py):
  - imagen: Google Imagen 3 (photorealistic / illustration)
  - dalle: OpenAI DALL-E 3 (fallback)
  - html_css: Claude generates HTML/CSS layout, Playwright screenshots it
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Optional

from anthropic import Anthropic

from engine.models import (
    CreativeBrief,
    AdVariant,
    CreativeTaxonomy,
    AdFormat,
    AdStatus,
)
from engine.generation.strategies import (
    ImageStrategy,
    get_strategy,
    get_available_strategies,
)


COPY_GENERATION_PROMPT = """You are a direct-response copywriter for JotPsych, a clinical documentation AI for behavioral health.

Given a creative brief, generate {num_variants} distinct ad copy variants.
Each variant must be meaningfully different — not just word swaps. Vary the:
- Hook (how it opens)
- Message angle (what benefit/pain it leads with)
- Tone (within the brief's direction)
- CTA phrasing

CRITICAL: Do NOT write like an AI. No "revolutionize", no "streamline your workflow",
no "in today's fast-paced world". Write like a human copywriter who has talked to
100 burned-out therapists. Be specific. Be real.

The visual_style for ALL variants MUST be set to: "{visual_style}"
This is the user's chosen generation method — do not override it.

For each variant, also provide taxonomy tags. Output JSON array:
[
    {{
        "headline": "...",
        "primary_text": "...",
        "description": "...",
        "cta_button": "...",
        "taxonomy": {{
            "message_type": "value_prop|pain_point|social_proof|urgency|education|comparison",
            "hook_type": "question|statistic|testimonial|provocative_claim|scenario|direct_benefit",
            "cta_type": "try_free|book_demo|learn_more|see_how|start_saving_time|watch_video",
            "tone": "clinical|warm|urgent|playful|authoritative|empathetic",
            "visual_style": "{visual_style}",
            "subject_matter": "clinician_at_work|patient_interaction|product_ui|workflow_comparison|conceptual|data_viz",
            "color_mood": "brand_primary|warm_earth|cool_clinical|high_contrast|muted_soft|bold_saturated",
            "text_density": "headline_only|headline_subhead|detailed_copy|minimal_overlay",
            "headline_word_count": <int>,
            "uses_number": <bool>,
            "uses_question": <bool>,
            "uses_first_person": <bool>,
            "uses_social_proof": <bool>,
            "copy_reading_level": <float>
        }}
    }}
]
"""


# Maps visual_style values to their generation strategy
VISUAL_STYLE_TO_STRATEGY = {
    # AI image generation styles
    "photography": "imagen",
    "illustration": "imagen",
    "mixed_media": "imagen",
    # HTML/CSS generation styles
    "text_heavy": "html_css",
    "abstract": "html_css",
    "screen_capture": "html_css",
}


def prompt_visual_style() -> tuple[str, str]:
    """Interactive CLI prompt for the user to choose a visual style.
    Returns (visual_style, strategy_name)."""

    available = get_available_strategies()

    styles = [
        ("photography", "imagen", "Photorealistic — real people, offices, candid moments"),
        ("illustration", "imagen", "Illustrated — stylized, artistic, hand-drawn feel"),
        ("mixed_media", "imagen", "Mixed media — photos combined with graphic elements"),
        ("text_heavy", "html_css", "Text-heavy graphic — bold typography, gradients, patterns"),
        ("abstract", "html_css", "Abstract graphic — geometric shapes, color blocks, modern"),
        ("screen_capture", "html_css", "Product UI — app screenshots, workflow mockups"),
    ]

    print("\n--- Visual Style Selection ---")
    valid_choices = []
    for i, (style, strategy, desc) in enumerate(styles, 1):
        if strategy in available:
            status = ""
            valid_choices.append(i)
        else:
            status = " [unavailable — missing API key]"
        print(f"  {i}. {style:<16} {desc}{status}")

    while True:
        try:
            choice = input(f"\nSelect visual style [1-{len(styles)}]: ").strip()
            idx = int(choice) - 1
            if 0 <= idx < len(styles) and (idx + 1) in valid_choices:
                style, strategy, desc = styles[idx]
                print(f"  → Using {style} ({strategy} strategy)\n")
                return style, strategy
            else:
                print("  Invalid choice or strategy unavailable. Try again.")
        except (ValueError, EOFError):
            print("  Please enter a number.")


class CreativeGenerator:
    """
    Generates ad variants from creative briefs.

    Copy generation uses Claude (Anthropic).
    Image generation is delegated to a pluggable strategy.
    Video formats get placeholder paths (not yet implemented).
    """

    def __init__(self, client: Optional[Anthropic] = None, strategy: Optional[ImageStrategy] = None):
        self.client = client or Anthropic()
        self.strategy = strategy
        self.visual_style: Optional[str] = None

    def set_strategy(self, strategy_name: str) -> None:
        """Set the image generation strategy by name."""
        self.strategy = get_strategy(strategy_name)

    def generate_copy(self, brief: CreativeBrief) -> list[dict]:
        """Generate copy variants with taxonomy tags from a brief."""

        visual_style = self.visual_style or "photography"
        prompt = COPY_GENERATION_PROMPT.format(
            num_variants=brief.num_variants,
            visual_style=visual_style,
        )

        response = self.client.messages.create(
            model="claude-sonnet-4-20250514",
            max_tokens=4000,
            system=prompt,
            messages=[
                {
                    "role": "user",
                    "content": f"""Creative Brief:
Target: {brief.target_audience}
Value Prop: {brief.value_proposition}
Pain Point: {brief.pain_point}
Desired Action: {brief.desired_action}
Tone: {brief.tone_direction}
Visual Direction: {brief.visual_direction}
Key Phrases: {', '.join(brief.key_phrases)}
Formats: {[f.value for f in brief.formats_requested]}
""",
                }
            ],
        )

        text = response.content[0].text
        if "```json" in text:
            text = text.split("```json")[1].split("```")[0]
        elif "```" in text:
            text = text.split("```")[1].split("```")[0]

        return json.loads(text.strip())

    def generate_assets(self, brief: CreativeBrief, copy_variants: list[dict]) -> list[str]:
        """Generate visual assets using the configured strategy."""
        assets_dir = Path("data/creatives") / brief.id
        assets_dir.mkdir(parents=True, exist_ok=True)

        asset_paths = []
        for i, variant in enumerate(copy_variants):
            if self.strategy:
                try:
                    path = self.strategy.generate_image(brief, variant, i, assets_dir)
                except Exception as e:
                    print(f"  [ERROR] Failed for variant {i}: {e}")
                    path = str(assets_dir / f"variant_{i}_placeholder.json")
            else:
                print(f"  [SKIP] No image strategy configured — skipping generation")
                path = str(assets_dir / f"variant_{i}_placeholder.json")
            asset_paths.append(path)
        return asset_paths

    def generate(self, brief: CreativeBrief) -> list[AdVariant]:
        """Full generation pipeline: copy → assets → tagged variants."""

        copy_variants = self.generate_copy(brief)
        asset_paths = self.generate_assets(brief, copy_variants)

        variants = []
        for copy_data, asset_path in zip(copy_variants, asset_paths):
            tax_data = copy_data["taxonomy"]

            for fmt in brief.formats_requested:
                for platform in brief.platforms:
                    taxonomy = CreativeTaxonomy(
                        **tax_data,
                        format=fmt,
                        platform=platform,
                        placement="feed",
                    )

                    if fmt == AdFormat.SINGLE_IMAGE:
                        variant_asset = asset_path
                    else:
                        variant_asset = asset_path.replace(".png", "_video_placeholder.json")

                    variant = AdVariant(
                        brief_id=brief.id,
                        headline=copy_data["headline"],
                        primary_text=copy_data["primary_text"],
                        description=copy_data.get("description", ""),
                        cta_button=copy_data.get("cta_button", "Learn More"),
                        asset_path=variant_asset,
                        asset_type="image" if fmt != AdFormat.VIDEO else "video",
                        taxonomy=taxonomy,
                        status=AdStatus.DRAFT,
                    )
                    variants.append(variant)

        return variants
