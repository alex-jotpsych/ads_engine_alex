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

### Style References

The engine supports learning from existing ad creatives. Place reference materials in `data/style_references/`:
- `*.html` — HTML/CSS ad examples (will be included as few-shot examples in generation prompts)
- `*.png` / `*.jpg` — Static imagery from freelancers or past campaigns (fed to Claude as visual references during critique)
- `style_notes.md` — Free-form notes on what works and what doesn't (injected into system prompts)

See the **Style References** section below for details.

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
└── style_references/          # Reference ads for style learning
    ├── style_notes.md         # Human feedback notes
    └── (example images/HTML)
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

**User interaction:** Click cards to select, then "Approve Selected" or "Reject Selected" (rejection requires feedback notes that inform future generation).

---

## Steps 6-9: Deploy → Track → Decide → Regress

These stages form the operational loop once ads are live. Currently:
- **Deploy (deployer.py):** STUB — Meta and Google API integration not yet built
- **Track (tracker.py):** STUB — daily metric pulls not yet built
- **Decide (engine.py):** IMPLEMENTED — scale/kill/wait logic ready, needs live data
- **Regress (model.py):** IMPLEMENTED — OLS regression on taxonomy, needs 20+ observations

---

## Style References

To improve image generation quality, you can provide reference materials that the engine learns from.

### Adding References

```bash
mkdir -p data/style_references
```

**For HTML/CSS style learning:**
- Drop `.html` files of ads you like into `data/style_references/`
- These get included as few-shot examples in the generation prompt

**For visual style learning (images from freelancers, past campaigns):**
- Drop `.png` or `.jpg` files into `data/style_references/`
- These get shown to Claude during the critique pass as "this is what good looks like"

**For free-form feedback:**
- Edit `data/style_references/style_notes.md` with notes like:
  ```
  - We prefer warm cream backgrounds over pure white
  - CTAs should always be pill-shaped with the gold accent color
  - Never use more than 2 colors besides black/white
  - The best-performing ad used a simple gradient with large bold text
  ```
- These notes get injected into the system prompt for all generation

### Workflow

1. Generate a batch of ads
2. Review in dashboard — note what you like and don't like
3. Save good examples to `data/style_references/`
4. Add notes to `style_notes.md`
5. Next generation run picks up these references automatically

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
