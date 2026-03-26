"""
Image generation strategies — pluggable backends for producing ad visuals.

Each strategy implements generate_image(brief, copy_data, index) -> str (file path).
The generator routes to the appropriate strategy based on the user's visual_style choice.

Strategies:
- ImagenStrategy: Google Imagen 3 for photographic / illustration styles
- DalleStrategy: OpenAI DALL-E 3 (fallback / alternative)
- HtmlCssStrategy: Claude generates HTML/CSS ad layout, Playwright screenshots it

Adding a new strategy:
1. Create a class with a generate_image(brief, copy_data, index, assets_dir) method
2. Register it in STRATEGY_REGISTRY at the bottom of this file
"""

from __future__ import annotations

import base64
import json
import os
from abc import ABC, abstractmethod
from pathlib import Path
from typing import Optional

from anthropic import Anthropic


# ---------------------------------------------------------------------------
# Base class
# ---------------------------------------------------------------------------

class ImageStrategy(ABC):
    """Base class for image generation strategies."""

    name: str = "base"
    description: str = ""

    @abstractmethod
    def generate_image(
        self,
        brief,
        copy_data: dict,
        index: int,
        assets_dir: Path,
    ) -> str:
        """Generate one ad image. Returns the saved file path."""
        ...

    @abstractmethod
    def is_available(self) -> bool:
        """Check if this strategy has the required API keys / dependencies."""
        ...


# ---------------------------------------------------------------------------
# Google Imagen 3 (via Gemini API)
# ---------------------------------------------------------------------------

IMAGEN_PROMPT = """Generate a high-quality ad image for a Meta (Facebook/Instagram) feed advertisement.

Product: JotPsych — AI-powered clinical documentation for behavioral health therapists.
Ad headline: "{headline}"
Visual direction: {visual_direction}
Color mood: {color_mood}
Subject: {subject_matter}

PHOTOGRAPHIC REQUIREMENTS:
- Shot on a Canon EOS R5, 35mm lens, f/2.8, natural window light
- Real location: modern therapy office, warm wood tones, plants, soft textures
- If a person is present: candid moment, not posed. Real clothing, real skin texture, visible pores. No perfect symmetry.
- Shallow depth of field with soft background bokeh
- Color grade: slightly warm, lifted shadows, muted highlights — like an indie film still

COMPOSITION:
- Leave clear space for text overlay (top third or bottom third)
- Square format (1:1 aspect ratio) for Meta feed
- Subject placed using rule of thirds, not centered

ABSOLUTE RESTRICTIONS — the image MUST NOT contain:
- Any text, words, letters, logos, watermarks, or UI elements
- Overly smooth or plastic-looking skin
- HDR-overprocessed or oversaturated colors
- Stock photo poses (thumbs up, pointing at camera, exaggerated smiles)
- Generic corporate office backgrounds
- Lens flare or dramatic lighting effects
- Multiple competing focal points
"""


class ImagenStrategy(ImageStrategy):
    """Google Imagen 3 via the Gemini API."""

    name = "imagen"
    description = "Google Imagen 3 — photorealistic imagery and illustrations"

    def __init__(self):
        self._client = None

    @property
    def client(self):
        if self._client is None:
            from google import genai
            api_key = os.getenv("GOOGLE_GEMINI_API_KEY")
            self._client = genai.Client(api_key=api_key)
        return self._client

    def is_available(self) -> bool:
        return bool(os.getenv("GOOGLE_GEMINI_API_KEY"))

    def generate_image(self, brief, copy_data: dict, index: int, assets_dir: Path) -> str:
        taxonomy = copy_data["taxonomy"]
        prompt = IMAGEN_PROMPT.format(
            headline=copy_data["headline"],
            visual_direction=brief.visual_direction,
            color_mood=taxonomy.get("color_mood", "warm_earth"),
            subject_matter=taxonomy.get("subject_matter", "clinician_at_work"),
        )

        response = self.client.models.generate_images(
            model="imagen-3.0-generate-002",
            prompt=prompt,
            config={
                "number_of_images": 1,
                "aspect_ratio": "1:1",
            },
        )

        image = response.generated_images[0]
        file_path = assets_dir / f"variant_{index}.png"
        file_path.write_bytes(image.image.image_bytes)

        print(f"  [IMAGEN] Generated {file_path}")
        return str(file_path)


# ---------------------------------------------------------------------------
# OpenAI DALL-E 3 (fallback)
# ---------------------------------------------------------------------------

DALLE_PROMPT = """Generate a high-quality ad image for a Meta (Facebook/Instagram) feed advertisement.

Product: JotPsych — AI-powered clinical documentation for behavioral health therapists.
Ad headline: "{headline}"
Visual direction: {visual_direction}
Color mood: {color_mood}
Subject: {subject_matter}

PHOTOGRAPHIC REQUIREMENTS:
- Shot on a Canon EOS R5, 35mm lens, f/2.8, natural window light
- Real location: modern therapy office, warm wood tones, plants, soft textures
- If a person is present: candid moment, not posed. Real clothing, real skin texture. No perfect symmetry.
- Shallow depth of field with soft background bokeh
- Color grade: slightly warm, lifted shadows, muted highlights

COMPOSITION:
- Leave clear space for text overlay (top third or bottom third)
- Square format (1:1 aspect ratio) for Meta feed
- Subject placed using rule of thirds, not centered

ABSOLUTE RESTRICTIONS — the image MUST NOT contain:
- Any text, words, letters, logos, watermarks, or UI elements
- Overly smooth or plastic-looking skin
- HDR-overprocessed or oversaturated colors
- Stock photo poses (thumbs up, pointing at camera, exaggerated smiles)
- Generic corporate office backgrounds
- Lens flare or dramatic lighting effects
"""


class DalleStrategy(ImageStrategy):
    """OpenAI DALL-E 3."""

    name = "dalle"
    description = "OpenAI DALL-E 3 — general purpose image generation"

    def __init__(self):
        self._client = None

    @property
    def client(self):
        if self._client is None:
            from openai import OpenAI
            self._client = OpenAI(api_key=os.getenv("OPENAI_API_KEY"))
        return self._client

    def is_available(self) -> bool:
        return bool(os.getenv("OPENAI_API_KEY"))

    def generate_image(self, brief, copy_data: dict, index: int, assets_dir: Path) -> str:
        taxonomy = copy_data["taxonomy"]
        prompt = DALLE_PROMPT.format(
            headline=copy_data["headline"],
            visual_direction=brief.visual_direction,
            color_mood=taxonomy.get("color_mood", "warm_earth"),
            subject_matter=taxonomy.get("subject_matter", "clinician_at_work"),
        )

        response = self.client.images.generate(
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

        print(f"  [DALLE] Generated {file_path}")
        return str(file_path)


# ---------------------------------------------------------------------------
# HTML/CSS Ad Generation (Claude + Playwright + Self-Critique)
# ---------------------------------------------------------------------------

HTML_SYSTEM_PROMPT = """You are a senior digital ad designer at a top agency. You create Meta (Facebook/Instagram) feed ad images using pure HTML and CSS.

Generate a single self-contained HTML file that renders as a 1080x1080px ad creative.

## BRAND STYLE GUIDE — JotPsych

### Color Palettes (pick ONE palette per ad, never mix between palettes)

**Warm Trust (default):**
- Primary: #2D5A3D (deep sage green)
- Accent: #E8B86D (warm gold)
- Background: #FAF7F2 (warm cream)
- Text: #1A1A1A (near-black)
- Muted: #8B9D83 (sage mid-tone)

**Cool Clinical:**
- Primary: #1E3A5F (deep navy)
- Accent: #4A9EC5 (calm blue)
- Background: #F5F8FA (ice white)
- Text: #1A1A1A
- Muted: #7A8FA6 (steel blue)

**Bold Energy:**
- Primary: #C4421A (burnt orange)
- Accent: #2D5A3D (sage green)
- Background: #1A1A1A (dark)
- Text: #FFFFFF
- Muted: #D4A574 (warm tan)

### Typography
- Headline: system-ui, -apple-system, 'Segoe UI', sans-serif — weight 700-800, size 52-72px
- Body: same font stack — weight 400, size 22-28px, line-height 1.5
- CTA button text: weight 600, size 20-24px, ALL CAPS, letter-spacing 1-2px
- JotPsych wordmark: weight 300, size 14px, bottom-right corner, subtle opacity

### Layout Rules
- Root container: exactly 1080px × 1080px, overflow hidden
- Use CSS flexbox for ALL centering and alignment — never use absolute positioning for text
- Padding: minimum 80px on all sides (content must not touch edges)
- Headline in the top 40% of the ad
- CTA button in the bottom 30%, horizontally centered
- Description text between headline and CTA
- Maximum 3 visual elements total (background + headline + CTA). Simplicity wins.

### CTA Button Style
- Pill shape: border-radius 50px, padding 18px 48px
- Background: the palette's Accent color
- Text: white or dark depending on contrast
- Subtle box-shadow for depth: 0 4px 16px rgba(0,0,0,0.15)

### Decorative Elements (optional, max ONE per ad)
- Subtle gradient overlay on background (radial or linear, using palette colors at low opacity)
- One geometric accent shape (circle, rounded rectangle) using the Muted color at 20-30% opacity
- NO borders, dividers, icons, or illustrations

## CRITICAL RULES
- Output ONLY the raw HTML. No explanation, no markdown fences, no commentary whatsoever.
- Completely self-contained (all CSS inline in a <style> tag, no external resources)
- Root element must be exactly 1080px × 1080px
- Use the provided headline and CTA text EXACTLY as given — do not rewrite copy
- Every text element must be perfectly centered horizontally
- Test your layout mentally: would a designer at Pentagram approve this? If not, simplify.
"""

HTML_USER_PROMPT = """Create a 1080x1080px Meta feed ad.

Headline: {headline}
Description: {description}
CTA Button: {cta_button}

Color mood: {color_mood}
Tone: {tone}

Remember: use flexbox centering, pick ONE color palette from the style guide, keep it minimal.
"""

CRITIQUE_PROMPT = """You are a senior art director reviewing a rendered ad image. The ad was generated as HTML/CSS and screenshotted.

Look at this ad image carefully and identify specific visual problems:
- Is all text perfectly horizontally centered?
- Are colors harmonious (from one palette, not clashing)?
- Is there enough padding/whitespace around the edges?
- Is the visual hierarchy clear (headline dominant, CTA obvious)?
- Does anything look clunky, misaligned, or amateurish?
- Is the CTA button properly styled and centered?

Then output ONLY the corrected HTML that fixes every issue you found. Apply the same style guide rules. Output raw HTML only — no explanation, no markdown fences."""


class HtmlCssStrategy(ImageStrategy):
    """Claude generates HTML/CSS, Playwright screenshots it, then self-critiques and fixes."""

    name = "html_css"
    description = "HTML/CSS graphic ads — gradients, typography, patterns (no AI imagery)"

    STYLE_REFS_DIR = Path("data/style_references")

    def __init__(self):
        self.anthropic = Anthropic()

    def is_available(self) -> bool:
        return bool(os.getenv("ANTHROPIC_API_KEY"))

    def _load_style_context(self) -> str:
        """Load style references to inject into the generation prompt."""
        parts = []

        # Load style notes (free-form human feedback)
        notes_path = self.STYLE_REFS_DIR / "style_notes.md"
        if notes_path.exists():
            notes = notes_path.read_text().strip()
            if notes:
                parts.append(f"## Human Feedback on Style\n\n{notes}")

        # Load HTML examples as few-shot references
        html_examples = sorted(self.STYLE_REFS_DIR.glob("*.html"))
        for i, html_path in enumerate(html_examples[:3]):  # Max 3 examples
            html = html_path.read_text().strip()
            parts.append(
                f"## Reference Example {i + 1} ({html_path.name})\n"
                f"This is an ad we liked. Match this level of quality:\n\n{html}"
            )

        return "\n\n---\n\n".join(parts) if parts else ""

    def _load_reference_images(self) -> list[dict]:
        """Load reference images to show Claude during critique."""
        images = []
        for ext in ("*.png", "*.jpg", "*.jpeg"):
            for img_path in sorted(self.STYLE_REFS_DIR.glob(ext))[:3]:  # Max 3
                img_bytes = img_path.read_bytes()
                media_type = "image/png" if img_path.suffix == ".png" else "image/jpeg"
                images.append({
                    "type": "image",
                    "source": {
                        "type": "base64",
                        "media_type": media_type,
                        "data": base64.b64encode(img_bytes).decode("utf-8"),
                    },
                })
        return images

    def generate_image(self, brief, copy_data: dict, index: int, assets_dir: Path) -> str:
        taxonomy = copy_data["taxonomy"]

        user_prompt = HTML_USER_PROMPT.format(
            headline=copy_data["headline"],
            description=copy_data.get("description", ""),
            cta_button=copy_data.get("cta_button", "Learn More"),
            color_mood=taxonomy.get("color_mood", "brand_primary"),
            tone=taxonomy.get("tone", "warm"),
        )

        # Inject style references into the system prompt
        style_context = self._load_style_context()
        system = HTML_SYSTEM_PROMPT
        if style_context:
            system += f"\n\n---\n\n# STYLE REFERENCES (learn from these)\n\n{style_context}"

        # --- Pass 1: Generate initial HTML ---
        response = self.anthropic.messages.create(
            model="claude-sonnet-4-20250514",
            max_tokens=4000,
            system=system,
            messages=[{"role": "user", "content": user_prompt}],
        )

        html_content = self._extract_html(response.content[0].text)

        # Screenshot the first pass
        draft_path = assets_dir / f"variant_{index}_draft.png"
        self._screenshot(html_content, draft_path)
        print(f"  [HTML] Draft {index} rendered, running critique...")

        # --- Pass 2: Critique the screenshot and fix ---
        draft_bytes = draft_path.read_bytes()
        draft_b64 = base64.b64encode(draft_bytes).decode("utf-8")

        # Build critique content: reference images (if any) + draft + critique text
        critique_content = []

        ref_images = self._load_reference_images()
        if ref_images:
            critique_content.append({
                "type": "text",
                "text": "Here are reference ads we like. Match their quality level:",
            })
            critique_content.extend(ref_images)

        critique_content.append({
            "type": "text",
            "text": "Here is the ad you just generated:",
        })
        critique_content.append({
            "type": "image",
            "source": {
                "type": "base64",
                "media_type": "image/png",
                "data": draft_b64,
            },
        })
        critique_content.append({
            "type": "text",
            "text": CRITIQUE_PROMPT,
        })

        critique_response = self.anthropic.messages.create(
            model="claude-sonnet-4-20250514",
            max_tokens=4000,
            system=system,
            messages=[
                {"role": "user", "content": user_prompt},
                {"role": "assistant", "content": html_content},
                {"role": "user", "content": critique_content},
            ],
        )

        fixed_html = self._extract_html(critique_response.content[0].text)

        # Save final HTML for debugging
        html_path = assets_dir / f"variant_{index}.html"
        html_path.write_text(fixed_html)

        # Screenshot the fixed version
        file_path = assets_dir / f"variant_{index}.png"
        self._screenshot(fixed_html, file_path)

        # Clean up draft
        draft_path.unlink(missing_ok=True)

        print(f"  [HTML] Generated {file_path} (critique-corrected)")
        return str(file_path)

    def _extract_html(self, text: str) -> str:
        """Strip markdown fences if Claude wraps the HTML."""
        if "```html" in text:
            text = text.split("```html")[1].split("```")[0]
        elif "```" in text:
            text = text.split("```")[1].split("```")[0]
        return text.strip()

    def _screenshot(self, html: str, output_path: Path) -> None:
        from playwright.sync_api import sync_playwright

        with sync_playwright() as p:
            browser = p.chromium.launch()
            page = browser.new_page(viewport={"width": 1080, "height": 1080})
            page.set_content(html)
            page.wait_for_timeout(500)
            page.screenshot(path=str(output_path), type="png")
            browser.close()


# ---------------------------------------------------------------------------
# Strategy registry
# ---------------------------------------------------------------------------

STRATEGY_REGISTRY: dict[str, ImageStrategy] = {
    "imagen": ImagenStrategy(),
    "dalle": DalleStrategy(),
    "html_css": HtmlCssStrategy(),
}

# Maps visual_style taxonomy values to their recommended strategy
VISUAL_STYLE_STRATEGY_MAP = {
    "photography": "imagen",
    "illustration": "imagen",
    "screen_capture": "html_css",
    "text_heavy": "html_css",
    "mixed_media": "imagen",
    "abstract": "html_css",
}


def get_strategy(name: str) -> ImageStrategy:
    """Get a strategy by name. Raises KeyError if not found."""
    return STRATEGY_REGISTRY[name]


def get_available_strategies() -> dict[str, ImageStrategy]:
    """Return only strategies that have their required API keys configured."""
    return {k: v for k, v in STRATEGY_REGISTRY.items() if v.is_available()}
