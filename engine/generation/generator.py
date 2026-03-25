"""
Creative Generator — takes briefs, produces ad variants with auto-taxonomy tagging.

Copy generation uses Claude (Anthropic).
Image generation uses OpenAI (DALL-E 3) for single_image formats.
Video generation is not yet implemented — video variants get placeholder paths.
"""

from __future__ import annotations

import base64
import json
import os
from pathlib import Path
from typing import Optional

from anthropic import Anthropic
from openai import OpenAI

from engine.models import (
    CreativeBrief,
    AdVariant,
    CreativeTaxonomy,
    AdFormat,
    AdStatus,
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
            "visual_style": "photography|illustration|screen_capture|text_heavy|mixed_media|abstract",
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


IMAGE_PROMPT_TEMPLATE = """Create a Meta (Facebook/Instagram) feed ad image for a healthcare SaaS product called JotPsych.

Ad headline: {headline}
Visual direction: {visual_direction}
Visual style: {visual_style}
Color mood: {color_mood}
Subject matter: {subject_matter}

Requirements:
- Clean, professional ad layout suitable for a Facebook/Instagram feed
- 1080x1080 pixels (square format for Meta feed)
- The image should feel authentic and human — NOT like generic stock art or obvious AI
- If the visual style is "photography", make it look like a real photo
- If "text_heavy", include the headline text prominently in the image
- Warm, trustworthy feel appropriate for healthcare professionals
- Do NOT include any JotPsych logo or watermark
- Do NOT include any text unless the visual style is "text_heavy" or "mixed_media"
"""


class CreativeGenerator:
    """
    Generates ad variants from creative briefs.

    Copy generation uses Claude (Anthropic).
    Image generation uses OpenAI DALL-E 3 for single_image formats.
    Video formats get placeholder paths (not yet implemented).
    """

    def __init__(self, client: Optional[Anthropic] = None):
        self.client = client or Anthropic()
        api_key = os.getenv("IMAGE_GEN_API_KEY")
        self.openai_client = OpenAI(api_key=api_key) if api_key else None

    def generate_copy(self, brief: CreativeBrief) -> list[dict]:
        """Generate copy variants with taxonomy tags from a brief."""

        prompt = COPY_GENERATION_PROMPT.format(num_variants=brief.num_variants)

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

    def generate_image(self, brief: CreativeBrief, copy_data: dict, index: int) -> str:
        """Generate a single ad image via OpenAI DALL-E 3. Returns the saved file path."""
        assets_dir = Path("data/creatives") / brief.id
        assets_dir.mkdir(parents=True, exist_ok=True)

        taxonomy = copy_data["taxonomy"]
        prompt = IMAGE_PROMPT_TEMPLATE.format(
            headline=copy_data["headline"],
            visual_direction=brief.visual_direction,
            visual_style=taxonomy.get("visual_style", "photography"),
            color_mood=taxonomy.get("color_mood", "brand_primary"),
            subject_matter=taxonomy.get("subject_matter", "clinician_at_work"),
        )

        response = self.openai_client.images.generate(
            model="dall-e-3",
            prompt=prompt,
            n=1,
            size="1024x1024",
            quality="standard",
            response_format="b64_json",
        )

        image_data = base64.b64decode(response.data[0].b64_json)
        file_path = assets_dir / f"variant_{index}.png"
        file_path.write_bytes(image_data)

        print(f"  [IMG] Generated {file_path}")
        return str(file_path)

    def generate_assets(self, brief: CreativeBrief, copy_variants: list[dict]) -> list[str]:
        """
        Generate visual assets for each copy variant.
        - single_image: generates real images via OpenAI DALL-E 3
        - video: placeholder path (not yet implemented)
        """
        asset_paths = []
        for i, variant in enumerate(copy_variants):
            if self.openai_client:
                try:
                    path = self.generate_image(brief, variant, i)
                except Exception as e:
                    print(f"  [IMG] Failed for variant {i}: {e}")
                    path = f"data/creatives/{brief.id}/variant_{i}_placeholder.json"
            else:
                print(f"  [IMG] No IMAGE_GEN_API_KEY set — skipping image generation")
                path = f"data/creatives/{brief.id}/variant_{i}_placeholder.json"
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

                    # Use the real image for single_image, placeholder for video
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
