# JotPsych Ads Engine

A quant-style ad operations system: idea → creative variants → review → deploy → measure → learn → repeat.

## Quick Start

```bash
# Set up environment
python -m venv venv
source venv/bin/activate
pip install -r requirements.txt
playwright install chromium

# Add API keys to .env
cp .env.example .env
# Fill in: ANTHROPIC_API_KEY, OPENAI_API_KEY, GOOGLE_GEMINI_API_KEY

# Generate ads from an idea
python -m engine.orchestrator idea "Your ad concept here"

# View results in the dashboard
uvicorn dashboard.api.app:app --reload
open dashboard/frontend/pages/review.html
```

---

## Pipeline Overview

```
┌─────────────┐     ┌──────────────┐     ┌─────────────────────┐     ┌──────────┐
│  1. INTAKE   │────▶│ 2. COPY GEN  │────▶│  3. IMAGE GEN       │────▶│ 4. STORE │
│  (parser.py) │     │ (generator.py│     │  (strategies.py)    │     │(store.py)│
│              │     │              │     │                     │     │          │
│ Raw idea ──▶ │     │ Brief ──▶    │     │ ┌─ imagen (Google)  │     │ JSON     │
│ Claude       │     │ Claude       │     │ ├─ dalle (OpenAI)   │     │ files in │
│ structures   │     │ writes 6     │     │ └─ html_css (Claude │     │ data/    │
│ into brief   │     │ copy variants│     │    + Playwright)    │     │          │
└─────────────┘     └──────────────┘     └─────────────────────┘     └──────────┘
                                                                           │
        ┌──────────────────────────────────────────────────────────────────┘
        ▼
┌──────────────┐     ┌──────────────┐     ┌──────────────┐     ┌──────────────┐
│  5. REVIEW   │────▶│  6. DEPLOY   │────▶│  7. TRACK    │────▶│ 8. DECIDE    │
│ (review.html)│     │ (deployer.py)│     │ (tracker.py) │     │ (engine.py)  │
│              │     │              │     │              │     │              │
│ Dashboard    │     │ Push to Meta │     │ Pull daily   │     │ Scale / Kill │
│ approve or   │     │ (STUB)       │     │ metrics      │     │ / Wait       │
│ reject       │     │              │     │ (STUB)       │     │              │
└──────────────┘     └──────────────┘     └──────────────┘     └──────┬───────┘
                                                                      │
                                                               ┌──────▼───────┐
                                                               │ 9. REGRESS   │
                                                               │ (model.py)   │
                                                               │              │
                                                               │ OLS on       │
                                                               │ taxonomy →   │
                                                               │ what works?  │
                                                               └──────────────┘
```

---

## CLI Commands

| Command | Description |
|---------|-------------|
| `python -m engine.orchestrator idea "text"` | Parse idea → generate copy + images → save variants |
| `python -m engine.orchestrator review` | List all variants pending review |
| `python -m engine.orchestrator daily` | Run full daily cycle (track → decide → regress → notify) |
| `python -m engine.orchestrator regression` | Run regression model, output creative playbook |

---

## Step 1: Intake (parser.py)

**What it does:** Takes a free-form idea (text, voice transcript, Slack message) and uses Claude to structure it into a `CreativeBrief`.

**Input:** Any text string — can be messy, informal, just a concept.

**Output:** A JSON `CreativeBrief` with structured fields: target_audience, value_proposition, pain_point, desired_action, tone_direction, visual_direction, key_phrases.

**User interaction:** You provide the idea text as a CLI argument.

---

## Step 2: Copy Generation (generator.py)

**What it does:** Takes the brief and uses Claude to generate 6 distinct ad copy variants. Each variant includes headline, primary_text, description, CTA button text, and full taxonomy tags.

**Input:** A `CreativeBrief` + the user's chosen visual style.

**Output:** 6 copy variant dicts with taxonomy tags for regression.

**User interaction:** The visual style you selected in the menu gets baked into every variant's taxonomy so it's tracked as a regression variable.

---

## Step 3: Image Generation (strategies.py)

**What it does:** Generates one image per copy variant using the strategy you selected.

### Available Strategies

| Strategy | Menu Options | How It Works | Cost/Image |
|----------|-------------|--------------|------------|
| **imagen** | 1. photography, 2. illustration, 3. mixed_media | Google Imagen 3 via Gemini API | ~$0.03 |
| **dalle** | (fallback if no Gemini key) | OpenAI DALL-E 3 | ~$0.04 |
| **html_css** | 4. text_heavy, 5. abstract, 6. screen_capture | Claude writes HTML/CSS → Playwright screenshots → Claude critiques screenshot → fixes → final screenshot | ~$0.01 |

### Visual Style Selection Menu

When you run `idea`, an interactive prompt appears:

```
--- Visual Style Selection ---
  1. photography      Photorealistic — real people, offices, candid moments
  2. illustration     Illustrated — stylized, artistic, hand-drawn feel
  3. mixed_media      Mixed media — photos combined with graphic elements
  4. text_heavy       Text-heavy graphic — bold typography, gradients, patterns
  5. abstract         Abstract graphic — geometric shapes, color blocks, modern
  6. screen_capture   Product UI — app screenshots, workflow mockups

Select visual style [1-6]:
```

### HTML/CSS Strategy Details

The HTML/CSS strategy uses a two-pass process:
1. **Generate:** Claude creates a self-contained HTML file following a strict brand style guide (color palettes, typography, layout rules)
2. **Critique:** Playwright screenshots the HTML, feeds the screenshot back to Claude, asks it to identify and fix visual issues (alignment, color harmony, hierarchy)
3. **Fix:** Claude outputs corrected HTML, Playwright takes the final screenshot

Both the HTML source and PNG are saved in `data/creatives/<brief_id>/`.

### Brand Kit Integration

The HTML/CSS strategy uses JotPsych's official brand kit:
- **Colors:** Midnight (#1C1E85), Warm Light (#FFF2F5), Sunset Glow (#FD96C9), Deep Night (#1E125E), Daylight (#FFF3C4), Afterglow (#813FE8) — organized into three palettes (Warm Light, Deep Night, Midnight Bold)
- **Typography:** Archivo (headings) and Inter (body) — variable TTFs loaded from `data/style_references/brand/fonts/`, base64-encoded and injected at Playwright render time
- **Logo:** SVG loaded from `data/style_references/brand/logos/`, injected into a `<div class="jotpsych-logo">` placeholder after Claude generates the HTML — Claude never handles raw SVG data

### Illustration vs. Photography

Options 1 (photography) and 2 (illustration) use separate prompt templates with distinct aesthetic constraints:
- **Photography prompt** — camera specs (Canon EOS R5, 35mm, f/2.8), real locations, candid moments, shallow DOF
- **Illustration prompt** — flat/semi-flat editorial style, limited 3–5 color palette, simplified character art (Headspace/Calm/Notion aesthetic), no photorealism

### Style References & Feedback Loop

The engine learns from reviewer feedback and reference materials. Style guidance is split by visual type:
- `style_notes_global.md` — applies to all strategies
- `style_notes_photo.md` — photography and mixed_media (Imagen/DALL-E)
- `style_notes_illustration.md` — illustration only (separate from photo)
- `style_notes_graphic.md` — text_heavy, abstract, screen_capture (HTML/CSS)

Feedback submitted through the dashboard is routed to the correct file based on the variant's visual style. See the **Feedback Loop** section below for details.

---

## Step 4: Storage (store.py)

**What it does:** Saves all generated objects as JSON files.

**Directory structure:**
```
data/
├── briefs/                    # CreativeBrief JSON files
├── creatives/
│   ├── variants/              # AdVariant JSON files (one per variant)
│   └── <brief_id>/           # Generated assets (PNG, HTML)
├── performance/
│   ├── snapshots/             # Daily performance pulls (STUB)
│   └── decisions/             # Scale/kill/wait records
├── models/                    # Regression results
└── style_references/          # Style learning & brand assets
    ├── brand/
    │   ├── logos/              # SVG logos (primary_dark, primary_light, logomark_*)
    │   └── fonts/             # Archivo-Variable.ttf, Inter-Variable.ttf
    ├── brand_config.json      # Tunable numeric params (logo size, font ranges, padding)
    ├── brand_config.json          # Tunable numeric params (logo size, font ranges, padding)
    ├── style_notes_global.md      # Cross-cutting style preferences (all strategies)
    ├── style_notes_photo.md       # Photography + mixed_media guidance
    ├── style_notes_illustration.md# Illustration-only guidance
    ├── style_notes_graphic.md     # Text-heavy/abstract/screen_capture guidance
    ├── product_context.md         # JotPsych product facts, stats, testimonials for copy (planned)
    ├── liked_photo/               # Liked photo references (auto-populated on Like)
    ├── liked_illustration/        # Liked illustration references (auto-populated on Like)
    ├── liked_graphic/             # Liked graphic references (auto-populated on Like)
    └── (example images/HTML)      # Manual reference ads
```

---

## Step 5: Review (dashboard)

**What it does:** Web gallery where Nate + Jackson approve or reject generated variants.

**Start the server:**
```bash
uvicorn dashboard.api.app:app --reload
```

**Open the gallery:**
```
dashboard/frontend/pages/review.html
```

**User interaction:**
- Click cards to select, then "Approve Selected" or "Reject Selected" (rejection requires feedback notes)
- **Double-click** a card to expand it — full image with zoom, all text, taxonomy tags, and single-variant actions
- **Like** a variant to save it as a positive reference — the image is copied to the appropriate `liked_*/` directory by visual type, and Claude updates "What We Like" in the corresponding style notes file
- **Feedback** button lets you type natural language feedback on a specific variant — Claude sees the actual image and updates the correct style notes file. If the feedback targets numeric parameters (e.g. "make the logo bigger"), `brand_config.json` is also updated

### Variant Lifecycle & Viewing Previous Batches

Every generated variant starts in `draft` status. The review dashboard shows **only draft variants** — once you approve or reject them, they leave the review queue but are **not deleted**.

**Archive mode:** Click "Show Archive" in the dashboard to view all previously approved and rejected variants. Filter by All / Approved / Rejected. Each card shows the status badge, reviewer, review notes, and timestamp.

**Return to Review:** From the archive, you can send any variant back to `draft` status using the "Return to Review" button in the expanded card view.

To view variants via API:
- **All variants:** `GET /api/variants` — returns everything regardless of status
- **By status:** `GET /api/variants?status=draft`, `?status=approved`, `?status=rejected`

Each new `idea` run generates a fresh batch of draft variants. Previous approved/rejected batches won't appear in the review queue.

---

## Feedback Loop (feedback.py)

**What it does:** Translates casual reviewer feedback into structured style guidance that feeds future generation.

**Flow:**
1. Reviewer sees a generated ad in the dashboard
2. Clicks "Feedback" and types natural language (e.g. "too much empty space", "body text too small")
3. `FeedbackProcessor` sends the feedback + the actual image to Claude
4. Claude updates the appropriate style notes file (routed by visual_style)
5. If feedback targets numeric values (logo size, font size, padding), `brand_config.json` is also updated
6. All future generations pick up the changes automatically

**Qualitative feedback** (colors, mood, composition) updates `style_notes_*.md` files, routed by visual style:
- photography → `style_notes_photo.md`
- illustration → `style_notes_illustration.md`
- mixed_media → `style_notes_photo.md`
- text_heavy / abstract / screen_capture → `style_notes_graphic.md`

**Quantitative feedback** (logo size, text size, padding) updates `data/style_references/brand_config.json`, which directly controls the values in the generation prompt.

**Like system:** Liked images are saved as positive references and used in two ways:
- PNG files shown to Claude during the critique pass as "this is what good looks like"
- HTML source files (for HTML/CSS variants) included as few-shot examples in the generation prompt

Liked images are saved to the correct directory by visual type: `liked_photo/`, `liked_illustration/`, or `liked_graphic/`.

---

## Steps 6-9: Deploy → Track → Decide → Regress

These stages form the operational loop once ads are live. Currently:
- **Deploy (deployer.py):** STUB — Meta and Google API integration not yet built
- **Track (tracker.py):** STUB — daily metric pulls not yet built
- **Decide (engine.py):** IMPLEMENTED — scale/kill/wait logic ready, needs live data
- **Regress (model.py):** IMPLEMENTED — OLS regression on taxonomy, needs 20+ observations

---

## Style References

### Manual References

Drop reference materials into `data/style_references/`:
- `.html` files — included as few-shot examples in the HTML/CSS generation prompt
- `.png` / `.jpg` files — shown to Claude during the critique pass as visual targets

### Dashboard Feedback (Recommended)

The dashboard provides a more effective workflow than manual file editing:

1. Generate a batch of ads
2. Review in the dashboard
3. **Like** ads you want more of — image saved as reference, style notes updated positively
4. **Feedback** on ads that need improvement — natural language processed into style guidance
5. Next generation run picks up all changes automatically

### Brand Config

`data/style_references/brand_config.json` controls tunable numeric parameters:

```json
{
    "logo": { "width_px": 160, "top_px": 40, "left_px": 40 },
    "typography": {
        "headline_size_range": [52, 72],
        "body_size_range": [22, 28]
    },
    "layout": { "padding_min_px": 80 }
}
```

These values are injected into the generation prompt. The feedback processor can update them automatically when you submit quantitative feedback (e.g. "make the logo bigger"), or you can edit the file directly.

---

## API Endpoints

| Endpoint | Method | Description |
|----------|--------|-------------|
| `/api/intake` | POST | Submit a new idea → parse into brief → generate variants |
| `/api/review` | GET | Get all variants pending review |
| `/api/review/approve` | POST | Approve variant(s) for deployment |
| `/api/review/reject` | POST | Reject variant(s) with feedback notes |
| `/api/review/return-to-review` | POST | Reset archived variant(s) back to draft status |
| `/api/feedback/image` | POST | Submit natural language feedback on a generated image |
| `/api/feedback/like` | POST | Like an image — save as reference, update style notes |
| `/api/feedback/style-notes` | GET | Get all style notes files for display |
| `/api/variants` | GET | List all variants (optional `?status=` filter) |
| `/api/performance` | GET | Portfolio-level performance summary |
| `/api/performance/{id}` | GET | Single variant performance data |
| `/api/decisions` | GET | Run and return latest scale/kill/wait decisions |
| `/api/regression` | GET | Get latest regression playbook |

---

## Environment Variables

| Variable | Required For | Description |
|----------|-------------|-------------|
| `ANTHROPIC_API_KEY` | Intake, copy gen, HTML/CSS strategy | Claude API key |
| `OPENAI_API_KEY` | DALL-E strategy | OpenAI API key |
| `GOOGLE_GEMINI_API_KEY` | Imagen strategy | Google Gemini API key |
| `META_ACCESS_TOKEN` | Deploy, Track (future) | Meta Marketing API |
| `SLACK_WEBHOOK_URL` | Notifications (future) | Slack incoming webhook |

---

## Development

### Cleaning Up / Starting Fresh

```bash
# Delete all generated variants and briefs
rm data/creatives/variants/*.json data/briefs/*.json 2>/dev/null

# Delete generated images
rm -rf data/creatives/*/

# Keep style references intact
```

### Adding a New Image Generation Strategy

1. Create a class in `engine/generation/strategies.py` that extends `ImageStrategy`
2. Implement `generate_image(brief, copy_data, index, assets_dir) -> str`
3. Implement `is_available() -> bool`
4. Register it in `STRATEGY_REGISTRY` at the bottom of the file
5. Add a menu entry in `prompt_visual_style()` in `generator.py`
