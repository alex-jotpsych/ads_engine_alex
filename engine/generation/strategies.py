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
import re
from abc import ABC, abstractmethod
from pathlib import Path
from typing import Optional

from anthropic import Anthropic


STYLE_REFS_DIR = Path("data/style_references")
BRAND_DIR = STYLE_REFS_DIR / "brand"


def load_style_notes(strategy_type: str = "global") -> str:
    """Load global + strategy-specific style notes for injection into prompts.

    Args:
        strategy_type: "photo" for Imagen/DALL-E, "graphic" for HTML/CSS, "global" for global-only.

    Returns:
        Combined style notes string, or empty string if no meaningful notes exist.
    """
    parts = []

    # Always load global notes
    global_path = STYLE_REFS_DIR / "style_notes_global.md"
    if global_path.exists():
        content = global_path.read_text().strip()
        if content and "(no notes yet)" not in content:
            parts.append(content)

    # Load strategy-specific notes
    if strategy_type in ("photo", "illustration", "graphic"):
        specific_path = STYLE_REFS_DIR / f"style_notes_{strategy_type}.md"
        if specific_path.exists():
            content = specific_path.read_text().strip()
            if content and "(no notes yet)" not in content:
                parts.append(content)

    return "\n\n---\n\n".join(parts) if parts else ""


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

IMAGEN_PHOTO_PROMPT = """Generate a single cohesive photograph for a Meta (Facebook/Instagram) feed ad.

Context (for mood only — do NOT illustrate the headline literally):
Product: JotPsych — AI-powered clinical documentation for behavioral health therapists.
Mood reference: "{headline}"
Visual direction: {visual_direction}
Color mood: {color_mood}
Subject: {subject_matter}

THIS IS A SINGLE PHOTOGRAPH — NOT a collage, NOT a before/after, NOT a split screen, NOT a comparison. One unified image with ONE scene, ONE moment, ONE subject.

PHOTOGRAPHIC REQUIREMENTS:
- Shot on a Canon EOS R5, 35mm lens, f/2.8, natural window light
- Real location: modern therapy office, warm wood tones, plants, soft textures
- If a person is present: candid moment, not posed. Real clothing, real skin texture, visible pores. No perfect symmetry.
- Shallow depth of field with soft background bokeh
- Color grade: slightly warm, lifted shadows, muted highlights — like an indie film still

COMPOSITION:
- ONE single focal point — one person or one object
- Leave clear negative space for text overlay (top third or bottom third)
- Square format (1:1 aspect ratio) for Meta feed
- Subject placed using rule of thirds, not centered
- Simple, uncluttered background

ABSOLUTE RESTRICTIONS — the image MUST NOT contain:
- Any text, words, letters, logos, watermarks, or UI elements
- Before/after comparisons, split screens, collages, or side-by-side compositions
- Multiple panels, frames, borders, or divided sections
- Overly smooth or plastic-looking skin
- HDR-overprocessed or oversaturated colors
- Stock photo poses (thumbs up, pointing at camera, exaggerated smiles)
- Generic corporate office backgrounds
- Lens flare or dramatic lighting effects
- Laptop/phone screens showing UI (unless subject_matter is product_ui)
"""


IMAGEN_ILLUSTRATION_PROMPT = """Generate a single cohesive illustration for a Meta (Facebook/Instagram) feed ad.

Context (for mood only — do NOT illustrate the headline literally):
Product: JotPsych — AI-powered clinical documentation for behavioral health therapists.
Mood reference: "{headline}"
Visual direction: {visual_direction}
Color mood: {color_mood}
Subject: {subject_matter}

THIS IS A STYLIZED ILLUSTRATION — NOT a photograph, NOT photorealistic, NOT 3D rendered. It should look hand-drawn, editorial, or digitally illustrated.

ILLUSTRATION STYLE:
- Modern editorial illustration style — think New Yorker covers, Headspace app, Calm app, or Slack marketing illustrations
- Flat or semi-flat design with subtle texture (grain, paper texture, soft brush strokes)
- Limited color palette: 3-5 colors maximum. Use deep navy-indigo, soft blush pink, vibrant pink, warm yellow, and electric purple tones. Keep it warm and approachable.
- Soft, rounded shapes — organic forms, not sharp geometric
- If a person is present: simplified, stylized human figure (not realistic proportions). Expressive pose, minimal facial detail. Think Notion or Linear-style character art.
- Warm, approachable, calming mood — this is for therapists, not corporate execs

COMPOSITION:
- ONE single focal point — one character or one central visual metaphor
- Leave clear negative space for text overlay (top third or bottom third)
- Square format (1:1 aspect ratio) for Meta feed
- Subject placed using rule of thirds, not centered
- Simple, uncluttered background — solid color or soft gradient

ABSOLUTE RESTRICTIONS — the image MUST NOT contain:
- Any text, words, letters, logos, watermarks, or UI elements
- Photorealistic rendering, 3D renders, or AI-generated "uncanny valley" faces
- Before/after comparisons, split screens, collages, or side-by-side compositions
- Multiple panels, frames, borders, or divided sections
- Clip art or generic stock illustration style
- Overly complex scenes with many characters or objects
- Dark, moody, or corporate aesthetics
- Lens flare, shadows, or photographic lighting effects
"""


class ImagenStrategy(ImageStrategy):
    """Google Imagen 4 via the Gemini API."""

    name = "imagen"
    description = "Google Imagen 4 — photorealistic imagery and illustrations"

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
        visual_style = taxonomy.get("visual_style", "photography")

        # Pick prompt template based on visual style
        if visual_style == "illustration":
            prompt_template = IMAGEN_ILLUSTRATION_PROMPT
        else:
            prompt_template = IMAGEN_PHOTO_PROMPT

        prompt = prompt_template.format(
            headline=copy_data["headline"],
            visual_direction=brief.visual_direction,
            color_mood=taxonomy.get("color_mood", "warm_earth"),
            subject_matter=taxonomy.get("subject_matter", "clinician_at_work"),
        )

        # Inject style feedback specific to the visual style
        notes_type = "illustration" if visual_style == "illustration" else "photo"
        style_notes = load_style_notes(notes_type)
        if style_notes:
            prompt += f"\n\nREVIEWER STYLE GUIDANCE (follow these preferences):\n{style_notes}\n"

        response = self.client.models.generate_images(
            model="imagen-4.0-generate-001",
            prompt=prompt,
            config={
                "number_of_images": 1,
                "aspect_ratio": "1:1",
                "person_generation": "allow_adult",
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

DALLE_PHOTO_PROMPT = """Generate a single cohesive photograph for a Meta (Facebook/Instagram) feed ad.

Context (for mood only — do NOT illustrate the headline literally):
Product: JotPsych — AI-powered clinical documentation for behavioral health therapists.
Mood reference: "{headline}"
Visual direction: {visual_direction}
Color mood: {color_mood}
Subject: {subject_matter}

THIS IS A SINGLE PHOTOGRAPH — NOT a collage, NOT a before/after, NOT a split screen, NOT a comparison. One unified image with ONE scene, ONE moment, ONE subject.

PHOTOGRAPHIC REQUIREMENTS:
- Shot on a Canon EOS R5, 35mm lens, f/2.8, natural window light
- Real location: modern therapy office, warm wood tones, plants, soft textures
- If a person is present: candid moment, not posed. Real clothing, real skin texture. No perfect symmetry.
- Shallow depth of field with soft background bokeh
- Color grade: slightly warm, lifted shadows, muted highlights

COMPOSITION:
- ONE single focal point — one person or one object
- Leave clear negative space for text overlay (top third or bottom third)
- Square format (1:1 aspect ratio) for Meta feed
- Subject placed using rule of thirds, not centered
- Simple, uncluttered background

ABSOLUTE RESTRICTIONS — the image MUST NOT contain:
- Any text, words, letters, logos, watermarks, or UI elements
- Before/after comparisons, split screens, collages, or side-by-side compositions
- Multiple panels, frames, borders, or divided sections
- Overly smooth or plastic-looking skin
- HDR-overprocessed or oversaturated colors
- Stock photo poses (thumbs up, pointing at camera, exaggerated smiles)
- Generic corporate office backgrounds
- Lens flare or dramatic lighting effects
- Laptop/phone screens showing UI (unless subject_matter is product_ui)
"""


DALLE_ILLUSTRATION_PROMPT = """Generate a single cohesive illustration for a Meta (Facebook/Instagram) feed ad.

Context (for mood only — do NOT illustrate the headline literally):
Product: JotPsych — AI-powered clinical documentation for behavioral health therapists.
Mood reference: "{headline}"
Visual direction: {visual_direction}
Color mood: {color_mood}
Subject: {subject_matter}

THIS IS A STYLIZED ILLUSTRATION — NOT a photograph, NOT photorealistic, NOT 3D rendered. It should look hand-drawn, editorial, or digitally illustrated.

ILLUSTRATION STYLE:
- Modern editorial illustration style — think New Yorker covers, Headspace app, Calm app, or Slack marketing illustrations
- Flat or semi-flat design with subtle texture (grain, paper texture, soft brush strokes)
- Limited color palette: 3-5 colors maximum. Use deep navy-indigo, soft blush pink, vibrant pink, warm yellow, and electric purple tones. Keep it warm and approachable.
- Soft, rounded shapes — organic forms, not sharp geometric
- If a person is present: simplified, stylized human figure (not realistic proportions). Expressive pose, minimal facial detail. Think Notion or Linear-style character art.
- Warm, approachable, calming mood — this is for therapists, not corporate execs

COMPOSITION:
- ONE single focal point — one character or one central visual metaphor
- Leave clear negative space for text overlay (top third or bottom third)
- Square format (1:1 aspect ratio) for Meta feed
- Subject placed using rule of thirds, not centered
- Simple, uncluttered background — solid color or soft gradient

ABSOLUTE RESTRICTIONS — the image MUST NOT contain:
- Any text, words, letters, logos, watermarks, or UI elements
- Photorealistic rendering, 3D renders, or AI-generated "uncanny valley" faces
- Before/after comparisons, split screens, collages, or side-by-side compositions
- Multiple panels, frames, borders, or divided sections
- Clip art or generic stock illustration style
- Overly complex scenes with many characters or objects
- Dark, moody, or corporate aesthetics
- Lens flare, shadows, or photographic lighting effects
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
        visual_style = taxonomy.get("visual_style", "photography")

        if visual_style == "illustration":
            prompt_template = DALLE_ILLUSTRATION_PROMPT
        else:
            prompt_template = DALLE_PHOTO_PROMPT

        prompt = prompt_template.format(
            headline=copy_data["headline"],
            visual_direction=brief.visual_direction,
            color_mood=taxonomy.get("color_mood", "warm_earth"),
            subject_matter=taxonomy.get("subject_matter", "clinician_at_work"),
        )

        # Inject style feedback specific to the visual style
        notes_type = "illustration" if visual_style == "illustration" else "photo"
        style_notes = load_style_notes(notes_type)
        if style_notes:
            prompt += f"\n\nREVIEWER STYLE GUIDANCE (follow these preferences):\n{style_notes}\n"

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

BRAND_CONFIG_PATH = STYLE_REFS_DIR / "brand_config.json"


def load_brand_config() -> dict:
    """Load tunable brand parameters from brand_config.json."""
    if BRAND_CONFIG_PATH.exists():
        return json.loads(BRAND_CONFIG_PATH.read_text())
    return {}


def build_html_system_prompt() -> str:
    """Build the HTML system prompt with current brand_config values injected."""
    cfg = load_brand_config()
    logo = cfg.get("logo", {})
    typo = cfg.get("typography", {})
    layout = cfg.get("layout", {})

    logo_width = logo.get("width_px", 160)
    logo_top = logo.get("top_px", 40)
    logo_left = logo.get("left_px", 40)
    hl_size = typo.get("headline_size_range", [52, 72])
    hl_weight = typo.get("headline_weight_range", [600, 800])
    body_size = typo.get("body_size_range", [22, 28])
    body_weight = typo.get("body_weight_range", [400, 500])
    cta_size = typo.get("cta_size_range", [20, 24])
    pad = layout.get("padding_min_px", 80)

    return f"""You are a senior digital ad designer at a top agency. You create Meta (Facebook/Instagram) feed ad images using pure HTML and CSS.

Generate a single self-contained HTML file that renders as a 1080x1080px ad creative.

## BRAND STYLE GUIDE — JotPsych

### Color Palettes (pick ONE palette per ad, never mix between palettes)
Use primary colors ~3× more often than secondary colors.

**Warm Light (default — light backgrounds):**
- Background: #FFF2F5 (Warm Light)
- Primary text: #1C1E85 (Midnight)
- Accent: #FD96C9 (Sunset Glow)
- Secondary accent: #813FE8 (Afterglow) — use sparingly
- Muted: #1C1E85 at 40% opacity

**Deep Night (dark backgrounds):**
- Background: #1E125E (Deep Night)
- Primary text: #FFF2F5 (Warm Light)
- Accent: #FD96C9 (Sunset Glow)
- Secondary accent: #FFF3C4 (Daylight) — use sparingly
- Muted: #FFF2F5 at 40% opacity

**Midnight Bold (high contrast):**
- Background: #1C1E85 (Midnight)
- Primary text: #FFFFFF
- Accent: #FD96C9 (Sunset Glow)
- Secondary accent: #FFF3C4 (Daylight) — use sparingly
- Muted: #FFFFFF at 30% opacity

### Brand Colors Reference
- Midnight: #1C1E85 (primary — deep blue-indigo)
- Warm Light: #FFF2F5 (primary — soft blush pink)
- Sunset Glow: #FD96C9 (primary — vibrant pink)
- Deep Night: #1E125E (secondary — dark indigo)
- Daylight: #FFF3C4 (secondary — soft warm yellow)
- Afterglow: #813FE8 (secondary — electric purple)

### Typography
Do NOT write any @font-face or @import rules for fonts. The fonts are injected automatically after generation. Just reference the font families by name:
- Headline: 'Archivo', sans-serif — weight {hl_weight[0]}-{hl_weight[1]}, size {hl_size[0]}-{hl_size[1]}px
- Body: 'Inter', sans-serif — weight {body_weight[0]}-{body_weight[1]}, size {body_size[0]}-{body_size[1]}px, line-height 1.5
- CTA button text: 'Inter', sans-serif — weight 600, size {cta_size[0]}-{cta_size[1]}px, ALL CAPS, letter-spacing 1-2px

### Logo
The JotPsych logo SVG will be automatically injected into your HTML after generation.
You MUST include an empty placeholder div for it:
  <div class="jotpsych-logo"></div>
Place it in the TOP-LEFT corner of the ad. Style it with CSS only:
- Position: absolute, top: {logo_top}px, left: {logo_left}px
- Width: {logo_width}px, z-index: 10
- Add a subtle drop shadow via CSS filter for contrast
- Do NOT attempt to write SVG paths or recreate the logo — just place the empty div with the class name

### Layout Rules
- Root container: exactly 1080px × 1080px, overflow hidden
- Use CSS flexbox for ALL centering and alignment — never use absolute positioning for text
- Padding: minimum {pad}px on all sides (content must not touch edges)
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
- NO borders, dividers, icons, or illustrations (the logo SVG is the only graphic element)

### Headline Styling
- Parse headlines into natural clauses and use contrasting colors to distinguish each clause
  (e.g., "Your Documentation Stress" in Midnight, "Ends Here" in Sunset Glow)
- Emphasize key statements (numbers, time metrics, strong claims) with visual accents like underlines, hand-drawn strokes, or geometric highlights

## CRITICAL RULES
- Output ONLY the raw HTML. No explanation, no markdown fences, no commentary whatsoever.
- Completely self-contained (all CSS inline in a <style> tag, no external resources)
- Do NOT write any @font-face, @import, or <link> tags for fonts — they are injected automatically. Just use font-family: 'Archivo' and 'Inter' in your CSS.
- Do NOT write any <svg> tags or SVG path data — the logo is injected automatically into the .jotpsych-logo div
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

Remember:
- Use Archivo for headings and Inter for body text (fonts are injected automatically)
- Include an empty <div class="jotpsych-logo"></div> in the top-left (the real logo SVG is injected automatically)
- Do NOT write any SVG code — just the empty div with the class
- Pick ONE color palette from the brand guide based on the color mood
- Use flexbox centering, keep it minimal
"""

CRITIQUE_PROMPT = """You are a senior art director reviewing a rendered ad image. The ad was generated as HTML/CSS and screenshotted.

Look at this ad image carefully and identify specific visual problems:
- Is all text perfectly horizontally centered?
- Are colors harmonious (from one JotPsych brand palette, not clashing)?
- Is there enough padding/whitespace around the edges?
- Is the visual hierarchy clear (headline dominant, CTA obvious)?
- Does anything look clunky, misaligned, or amateurish?
- Is the CTA button properly styled and centered?
- Is the JotPsych logo present in the top-left corner with proper sizing and clearspace?
- Are Archivo (headings) and Inter (body) fonts specified correctly?
- Are headline clauses styled with contrasting colors for emphasis?

Then output ONLY the corrected HTML that fixes every issue you found. Apply the same style guide rules. Output raw HTML only — no explanation, no markdown fences."""


class HtmlCssStrategy(ImageStrategy):
    """Claude generates HTML/CSS, Playwright screenshots it, then self-critiques and fixes."""

    name = "html_css"
    description = "HTML/CSS graphic ads — gradients, typography, patterns (no AI imagery)"

    # Cached brand assets (loaded once per process, heavy font data stays out of prompts)
    _brand_font_faces: Optional[str] = None  # ~2MB base64 — only injected at screenshot time
    _brand_logo_svg: Optional[str] = None    # ~14KB SVG — safe to include in prompts

    def __init__(self):
        self.anthropic = Anthropic()

    def is_available(self) -> bool:
        return bool(os.getenv("ANTHROPIC_API_KEY"))

    def _load_logo_svg(self) -> str:
        """Load the primary logo SVG for injection into prompts (~14KB, safe for context)."""
        if HtmlCssStrategy._brand_logo_svg is not None:
            return HtmlCssStrategy._brand_logo_svg
        logo_path = BRAND_DIR / "logos" / "primary_dark.svg"
        if logo_path.exists():
            HtmlCssStrategy._brand_logo_svg = logo_path.read_text().strip()
        else:
            HtmlCssStrategy._brand_logo_svg = ""
        return HtmlCssStrategy._brand_logo_svg

    def _load_font_face_css(self) -> str:
        """Load @font-face CSS with base64-encoded fonts. Only for Playwright rendering, never for prompts.

        Chromium blocks file:// URLs for @font-face when page is loaded via
        set_content(), so we must use data: URIs with base64-encoded TTFs.
        """
        if HtmlCssStrategy._brand_font_faces is not None:
            return HtmlCssStrategy._brand_font_faces
        fonts = [
            ("Archivo", BRAND_DIR / "fonts" / "Archivo-Variable.ttf"),
            ("Inter", BRAND_DIR / "fonts" / "Inter-Variable.ttf"),
        ]
        parts = []
        for family, ttf_path in fonts:
            if ttf_path.exists():
                b64 = base64.b64encode(ttf_path.read_bytes()).decode("utf-8")
                parts.append(
                    f"@font-face {{\n"
                    f"  font-family: '{family}';\n"
                    f"  src: url('data:font/ttf;base64,{b64}') format('truetype');\n"
                    f"  font-weight: 100 900;\n"
                    f"  font-display: swap;\n"
                    f"}}"
                )
        HtmlCssStrategy._brand_font_faces = "\n".join(parts)
        return HtmlCssStrategy._brand_font_faces

    def _inject_brand(self, html: str) -> str:
        """Inject @font-face CSS and logo SVG into HTML right before Playwright screenshot.

        NOT sent to Claude — only used at render time so Playwright can
        resolve the Archivo/Inter font families and render the real logo.
        """

        # Strip ALL font-loading attempts Claude wrote — @font-face, @import,
        # and <link> tags for Google Fonts. We inject our own file:// @font-face
        # declarations that Playwright can actually resolve.
        html = re.sub(r'@font-face\s*\{[^}]*\}\s*', '', html)
        html = re.sub(r'@import\s+url\([^)]*\)\s*;?\s*', '', html)
        html = re.sub(r'<link[^>]*fonts\.googleapis\.com[^>]*/?\s*>', '', html)

        font_face_css = self._load_font_face_css()
        if font_face_css and "<style>" in html:
            html = html.replace("<style>", f"<style>\n{font_face_css}\n", 1)

        logo_svg = self._load_logo_svg()
        if logo_svg:
            # Inject real SVG into the placeholder div Claude left for us
            placeholder = '<div class="jotpsych-logo"></div>'
            if placeholder in html:
                html = html.replace(
                    placeholder,
                    f'<div class="jotpsych-logo">{logo_svg}</div>',
                    1,
                )
            # Also handle case where Claude put content inside (shouldn't, but defensive)
            elif 'class="jotpsych-logo"' in html:
                        html = re.sub(
                    r'<div class="jotpsych-logo"[^>]*>.*?</div>',
                    f'<div class="jotpsych-logo">{logo_svg}</div>',
                    html,
                    count=1,
                    flags=re.DOTALL,
                )

        return html

    def _load_style_context(self) -> str:
        """Load style references to inject into the generation prompt."""
        parts = []

        # Load global + graphic-specific style notes
        style_notes = load_style_notes("graphic")
        if style_notes:
            parts.append(f"## Reviewer Feedback on Style\n\n{style_notes}")

        # Load HTML examples as few-shot references (root + liked_graphic/)
        html_examples = sorted(STYLE_REFS_DIR.glob("*.html"))
        liked_html_dir = STYLE_REFS_DIR / "liked_graphic"
        if liked_html_dir.exists():
            html_examples += sorted(liked_html_dir.glob("*.html"))
        for i, html_path in enumerate(html_examples[:5]):  # Max 5 examples
            html = html_path.read_text().strip()
            source = "liked" if "liked_" in html_path.parent.name else "reference"
            parts.append(
                f"## {source.title()} Example {i + 1} ({html_path.name})\n"
                f"This is an ad we liked. Match this level of quality:\n\n{html}"
            )

        return "\n\n---\n\n".join(parts) if parts else ""

    def _load_reference_images(self) -> list[dict]:
        """Load liked graphic reference images to show Claude during critique."""
        images = []
        # Load from liked_graphic/ (type-specific liked images)
        liked_dir = STYLE_REFS_DIR / "liked_graphic"
        # Also check root style_references/ for legacy references
        search_dirs = [liked_dir, STYLE_REFS_DIR]

        for search_dir in search_dirs:
            if not search_dir.exists():
                continue
            for ext in ("*.png", "*.jpg", "*.jpeg"):
                for img_path in sorted(search_dir.glob(ext)):
                    if len(images) >= 5:  # Max 5 reference images
                        return images
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

    def generate_image(
        self, brief, copy_data: dict, index: int, assets_dir: Path,
        critique: bool = True,
    ) -> str:
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
        system = build_html_system_prompt()
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

        if not critique:
            # Skip critique — render directly
            html_path = assets_dir / f"variant_{index}.html"
            html_path.write_text(html_content)
            file_path = assets_dir / f"variant_{index}.png"
            self._screenshot(self._inject_brand(html_content), file_path)
            print(f"  [HTML] Generated {file_path} (single-pass)")
            return str(file_path)

        # Screenshot the first pass (inject fonts only for Playwright, not for Claude)
        draft_path = assets_dir / f"variant_{index}_draft.png"
        self._screenshot(self._inject_brand(html_content), draft_path)
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

        # Save final HTML (without font blobs — keeps it readable for debugging)
        html_path = assets_dir / f"variant_{index}.html"
        html_path.write_text(fixed_html)

        # Screenshot the fixed version (inject fonts only for Playwright rendering)
        file_path = assets_dir / f"variant_{index}.png"
        self._screenshot(self._inject_brand(fixed_html), file_path)

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

    # Persistent browser — avoids ~2s cold start per screenshot
    _playwright = None
    _browser = None

    @classmethod
    def _get_browser(cls):
        """Return a shared Chromium instance, launching once per process."""
        if cls._browser is None:
            from playwright.sync_api import sync_playwright
            cls._playwright = sync_playwright().start()
            cls._browser = cls._playwright.chromium.launch()
        return cls._browser

    def _screenshot(self, html: str, output_path: Path) -> None:
        browser = self._get_browser()
        page = browser.new_page(viewport={"width": 1080, "height": 1080})
        page.set_content(html, wait_until="load")
        # Wait for @font-face fonts to fully load before screenshotting
        page.evaluate("() => document.fonts.ready")
        page.screenshot(path=str(output_path), type="png")
        page.close()


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
