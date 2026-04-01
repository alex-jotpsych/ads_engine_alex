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
│ structures   │     │ writes N     │     │ └─ html_css (Claude │     │ data/    │
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

### `idea` — generate ads from a concept

```bash
python -m engine.orchestrator idea "Your ad concept here"
```

Running `idea` launches four interactive menus in sequence — no flags required:

```
--- Visual Style Selection ---
  1. photography      Photorealistic — real people, offices, candid moments
  2. illustration     Illustrated — stylized, artistic, hand-drawn feel
  3. mixed_media      Mixed media — photos combined with graphic elements
  4. text_heavy       Text-heavy graphic — bold typography, gradients, patterns
  5. abstract         Abstract graphic — geometric shapes, color blocks, modern
  6. screen_capture   Product UI — app screenshots, workflow mockups

Select visual style [1-6]:

--- Aspect Ratio ---
  1. 1:1    Meta feed — square (1080×1080)
  2. 3:4    Meta portrait feed (1080×1440)
  3. 9:16   Stories / Reels — full vertical (1080×1920)

Select aspect ratio [1-3]:

--- Number of Variants ---
  1. 2    quick test
  2. 4    small batch
  3. 6    standard (default)
  4. 8    wider coverage
  5. 12   broad sweep

Select number of variants [1-5]:

--- Asset Format ---
  1. single_image                Image only — fastest, generates PNGs
  2. video                       Video only — placeholder (not yet built)
  3. single_image, video         Both image and video

Select format [1-3]:
```

Any menu can be bypassed with a CLI flag — the interactive prompt is skipped for that option:

| Flag | Values | Effect |
|------|--------|--------|
| `--aspect-ratio` | `1:1`, `3:4`, `9:16` | Skip aspect ratio prompt |
| `--variants N` | integer | Skip variants count prompt |
| `--formats` | `single_image` `video` | Skip format prompt |
| `--platforms` | `meta` `google` | (no interactive prompt — defaults to `meta`) |

**Examples:**

```bash
# Fully interactive — prompts for all 4 options
python -m engine.orchestrator idea "burnout among therapists"

# Skip specific prompts with flags
python -m engine.orchestrator idea "burnout" --variants 4 --aspect-ratio 9:16

# Fully explicit — no interactive prompts
python -m engine.orchestrator idea "burnout" --variants 6 --formats single_image --aspect-ratio 3:4 --platforms meta
```

### Other commands

| Command | Description |
|---------|-------------|
| `python -m engine.orchestrator review` | List all variants pending review |
| `python -m engine.orchestrator daily` | Run full daily cycle (track → decide → regress → notify) |
| `python -m engine.orchestrator regression` | Run regression model, print creative playbook |

---

## Step 1: Intake (parser.py)

**What it does:** Takes a free-form idea (text, voice transcript, Slack message) and uses Claude to structure it into a `CreativeBrief`.

**Input:** Any text string — can be messy, informal, just a concept.

**Output:** A JSON `CreativeBrief` with structured fields: target_audience, value_proposition, pain_point, desired_action, tone_direction, visual_direction, key_phrases.

---

## Step 2: Copy Generation (generator.py)

**What it does:** Takes the brief and uses Claude to generate N distinct ad copy variants. Each variant includes headline, primary_text, description, CTA button text, and full taxonomy tags.

**Product context grounding:** If `data/style_references/product_context.md` exists, its contents are injected into the generation prompt at runtime. This file holds JotPsych-specific facts (time savings stats, customer quotes, revenue claims, verbatim brand phrases) so copy is grounded in real data rather than generic claims.

**Input:** A `CreativeBrief` + the user's chosen visual style.

**Output:** N copy variant dicts with taxonomy tags for regression.

---

## Step 3: Image Generation (strategies.py)

**What it does:** Generates one image per copy variant using the strategy selected in the visual style menu.

### Available Strategies

| Strategy | Visual Styles | How It Works | Approx Cost |
|----------|--------------|--------------|-------------|
| **imagen** | photography, illustration, mixed_media | Google Imagen 3 via Gemini API | ~$0.03/image |
| **dalle** | (fallback if no Gemini key) | OpenAI DALL-E 3 | ~$0.04/image |
| **html_css** | text_heavy, abstract, screen_capture | Claude writes HTML/CSS → Playwright screenshots → Claude critiques → fixes → final screenshot | ~$0.01/image |

Strategies that are missing their required API key are shown as `[unavailable]` in the interactive menu.

### Aspect Ratio Support

All three strategies support three canvas sizes:

| Ratio | Canvas | Use Case |
|-------|--------|----------|
| 1:1 | 1080×1080 | Meta feed (square) |
| 3:4 | 1080×1440 | Meta portrait feed |
| 9:16 | 1080×1920 | Stories / Reels |

> **Note:** The portrait ratio is 3:4 (not 4:5). The Imagen API does not support 4:5 — its supported values are 1:1, 9:16, 16:9, 4:3, and 3:4. Meta accepts 3:4 for portrait feed placements.

The HTML/CSS strategy uses larger typography at taller ratios (9:16 gets 68–88px headlines vs. 58–72px at 1:1). The Playwright viewport is resized to match. Imagen and DALL-E use the native aspect ratio parameters of their respective APIs.

### HTML/CSS Strategy Details

Two-pass generation process:
1. **Generate:** Claude creates a self-contained HTML file following the brand style guide (color palettes, typography, layout rules, style notes)
2. **Critique:** Playwright screenshots the HTML, feeds the image back to Claude, asks it to identify and fix visual issues (alignment, color harmony, hierarchy)
3. **Fix:** Claude outputs corrected HTML, Playwright takes the final screenshot

Both the HTML source and PNG are saved in `data/creatives/<brief_id>/`.

### Brand Kit Integration

The HTML/CSS strategy uses JotPsych's official brand kit:
- **Colors:** Midnight (#1C1E85), Warm Light (#FFF2F5), Sunset Glow (#FD96C9), Deep Night (#1E125E), Daylight (#FFF3C4), Afterglow (#813FE8) — organized into three palettes (Warm Trust, Cool Clinical, Bold Energy)
- **Typography:** Archivo (headings) and Inter (body) — variable TTFs loaded from `data/style_references/brand/fonts/`, base64-encoded and injected at Playwright render time
- **Logo:** SVG loaded from `data/style_references/brand/logos/`, injected into a `<div class="jotpsych-logo">` placeholder after Claude generates the HTML — Claude never handles raw SVG data

### Illustration vs. Photography

Options 1 (photography) and 2 (illustration) use separate prompt templates:
- **Photography prompt** — camera specs (Canon EOS R5, 35mm, f/2.8), real locations, candid moments, shallow DOF
- **Illustration prompt** — flat/semi-flat editorial style, limited 3–5 color palette, simplified character art (Headspace/Calm/Notion aesthetic), written as flowing prose to prevent Imagen from rendering prompt text visually

---

## Step 4: Storage (store.py)

**What it does:** Saves all generated objects as JSON files.

**Directory structure:**
```
data/
├── briefs/                    # CreativeBrief JSON files
├── creatives/
│   ├── variants/              # AdVariant JSON files (one per variant)
│   └── <brief_id>/            # Generated assets (PNG, HTML)
├── performance/
│   ├── snapshots/             # Daily performance pulls (STUB)
│   └── decisions/             # Scale/kill/wait records
├── models/                    # Regression results
└── style_references/          # Style learning & brand assets
    ├── brand/
    │   ├── logos/             # SVG logos (primary_dark, primary_light, logomark_*)
    │   └── fonts/             # Archivo-Variable.ttf, Inter-Variable.ttf
    ├── brand_config.json      # Tunable numeric params (logo size, font ranges, padding)
    ├── product_context.md     # JotPsych product facts for copy grounding
    ├── style_notes_global.md  # Cross-cutting prefs (all strategies)
    ├── style_notes_photo_1x1.md      # Photography/mixed_media · 1:1
    ├── style_notes_photo_3x4.md      # Photography/mixed_media · 3:4
    ├── style_notes_photo_9x16.md     # Photography/mixed_media · 9:16
    ├── style_notes_illustration_1x1.md
    ├── style_notes_illustration_3x4.md
    ├── style_notes_illustration_9x16.md
    ├── style_notes_graphic_1x1.md    # Text-heavy/abstract/screen_capture · 1:1
    ├── style_notes_graphic_3x4.md    # Text-heavy/abstract/screen_capture · 3:4
    ├── style_notes_graphic_9x16.md   # Text-heavy/abstract/screen_capture · 9:16
    ├── liked_photo/           # Liked photo/mixed_media references
    ├── liked_illustration/    # Liked illustration references
    └── liked_graphic/         # Liked graphic references
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
- Click cards to select, then "Approve" or "Reject" (rejection requires feedback notes)
- **Double-click** a card to expand it — full image with zoom, all text, taxonomy tags, and single-variant actions
- Cards show **numbered badges** (#1, #2, #3...) and respect the actual aspect ratio of the generated image
- **Like** a variant to save it as a positive reference — image copied to the appropriate `liked_*/` directory, Claude updates "What We Like" in the matching style notes file
- **Feedback** button — type natural language feedback; Claude sees the actual image and updates the correct style notes file; if feedback targets numeric parameters (e.g. "make the logo bigger"), `brand_config.json` is also updated
- **Voice Feedback** button — record audio directly in the browser; reference ads by number ("Ad 2 needs more whitespace, Ad 5 is too dark"). Whisper transcribes it, Claude extracts the (ad number, feedback) pairs, each routed to the correct style notes file. An "upload file instead" fallback accepts .m4a/.mp3/.wav for recordings made on another device

### Gallery Layout

Variants are grouped by the idea run (brief) that generated them, newest batch first. Each group has a collapsible header row showing:
- **Concept** — the raw idea text (truncated); hover to see the full prompt in a tooltip
- **Visual style + aspect ratio** — pill badges showing what type of ads are in the group
- **Variant count + date** — how many cards and when the batch was generated

**Toolbar controls:**
- **Filter chips** — filter by visual style, aspect ratio, or platform; filters update the gallery in real time without reloading
- **Collapse All / Expand All** — fold or unfold all brief groups at once
- **Show Archive / Show Review** — toggle between draft variants and previously reviewed ones

**Per-group actions:** Each brief group header has **Approve All** and **Reject All** buttons (right-aligned, left of the metadata). These select all variants in the group and trigger the same approve/reject flow — Reject All opens the rejection notes modal and applies the feedback to the whole group.

### Variant Lifecycle & Viewing Previous Batches

Every generated variant starts in `draft` status. The review dashboard shows **only draft variants** — once you approve or reject them, they leave the review queue but are **not deleted**.

**Archive mode:** Click "Show Archive" to view all previously approved and rejected variants. Filter by All / Approved / Rejected.

**Return to Review:** From the archive, send any variant back to `draft` using "Return to Review" in the expanded card view.

API access:
- **All variants:** `GET /api/variants`
- **By status:** `GET /api/variants?status=draft|approved|rejected`

---

## Feedback Loop (feedback.py)

**What it does:** Translates reviewer feedback into style guidance that feeds future generation.

**Flow:**
1. Reviewer sees a generated ad in the dashboard
2. Clicks "Feedback" and types natural language (e.g. "too much empty space", "body text too small")
3. `FeedbackProcessor` sends the feedback + the actual image to Claude
4. Claude updates the appropriate style notes file (routed by visual_style × aspect_ratio)
5. All future generations for that visual type + ratio pick up the changes automatically

### Style Notes — 2D Routing

Feedback is routed to the specific style file for the variant's visual style **and** aspect ratio. This means feedback on a 9:16 illustration doesn't bleed into 1:1 illustrations:

| Visual Style | Aspect Ratio | File |
|---|---|---|
| photography / mixed_media | 1:1 | `style_notes_photo_1x1.md` |
| photography / mixed_media | 3:4 | `style_notes_photo_3x4.md` |
| photography / mixed_media | 9:16 | `style_notes_photo_9x16.md` |
| illustration | 1:1 | `style_notes_illustration_1x1.md` |
| illustration | 3:4 | `style_notes_illustration_3x4.md` |
| illustration | 9:16 | `style_notes_illustration_9x16.md` |
| text_heavy / abstract / screen_capture | 1:1 | `style_notes_graphic_1x1.md` |
| text_heavy / abstract / screen_capture | 3:4 | `style_notes_graphic_3x4.md` |
| text_heavy / abstract / screen_capture | 9:16 | `style_notes_graphic_9x16.md` |
| unknown / no style | any | `style_notes_global.md` |

**Quantitative feedback** (logo size, text size, padding) updates `brand_config.json` directly.

**Like system:** Liked images are saved as positive references:
- PNG files shown to Claude during the critique pass as "this is what good looks like"
- HTML source files (for HTML/CSS variants) included as few-shot examples in generation
- Saved to `liked_photo/`, `liked_illustration/`, or `liked_graphic/` by visual type

### Rejection Note Routing

When rejecting a batch, the rejection notes field supports ad-number references — the same intelligence as voice feedback, but for typed text.

If notes say things like _"Ad 2 is too dark, Ad 5 needs better contrast"_, Claude parses out the per-ad feedback and routes each piece to the correct variant's style notes file (matched by visual_style × aspect_ratio). If no ad numbers are mentioned, the full notes are applied to every rejected variant as a bulk update.

The toast notification shows which routing path was used: _"Rejected 3 variant(s) (routed to 2 ads)"_ vs. _"Rejected 3 variant(s)"_.

### Voice Feedback

Click "Voice Feedback" in the toolbar to open the recording modal. The workflow:
1. **Record** — browser microphone via `MediaRecorder` API; timer + animated waveform shown during recording
2. **Process** — audio sent to backend; Whisper transcribes it; Claude extracts (ad number, feedback) pairs
3. **Route** — each pair matched to a variant UUID using the gallery's display numbers at the time the modal was opened, then routed to the correct style notes file

Card numbers are **frozen when the modal opens** — changing filters (which renumber cards) after you've recorded does not corrupt the mapping.

Fallback: click "upload file instead" to supply a pre-recorded .m4a/.mp3/.wav from another device.

---

## Step 6: Deploy (deployer.py)

Approved variants can be deployed to Meta directly from the dashboard. The deployer uses the `facebook-business` Python SDK.

### How to deploy an ad

1. Generate and approve a variant in the review dashboard
2. Switch to **Archive** (approved variants live here)
3. Double-click a card to expand it → click **Deploy to Meta**
4. Select an **Ad Set** from the dropdown (populated from your Meta account)
5. Enter or confirm the **destination URL**
6. Check the confirmation box and click **Deploy**

The ad is created in **PAUSED** status so it appears in Ads Manager but does not enter Meta's review queue yet. Go to Meta Ads Manager, confirm the creative looks correct, then set it to Active to begin review. You'll receive a Slack notification when it's submitted and another when Meta approves or rejects it.

### Meta review outcomes

- **Approved** → Slack posts "Ad approved and live." The variant card shows a green "Meta: Live" badge.
- **Rejected** → Slack posts the rejection reason(s). The variant is automatically moved back to Draft so you can edit and redeploy.

Review status polling runs automatically as part of the daily cycle. You can also trigger it manually:
```
POST /api/meta/poll-status
```

### Environment variables required for deployment

| Variable | Description |
|---|---|
| `META_ACCESS_TOKEN` | Long-lived token with `ads_management`, `ads_read`, `pages_manage_ads` permissions |
| `META_AD_ACCOUNT_ID` | Your ad account ID (format: `act_XXXXXXXXXX`) |
| `META_APP_ID` | Meta app ID |
| `META_APP_SECRET` | Meta app secret |
| `META_PAGE_ID` | Facebook Page ID (required to create AdCreatives) |
| `META_DESTINATION_URL` | Default landing page URL (can be overridden per deploy) |

If `META_ACCESS_TOKEN` is not set, the deployer initializes without Meta support — idea generation and everything else works normally.

---

## Steps 7-9: Track → Decide → Regress

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

1. Generate a batch of ads
2. Review in the dashboard
3. **Like** ads you want more of — image saved as reference, style notes updated
4. **Feedback** on ads that need improvement — natural language processed into style guidance
5. Next generation run picks up all changes automatically

### Brand Config

`data/style_references/brand_config.json` controls tunable numeric parameters per aspect ratio:

```json
{
    "logo": { "width_px": 320, "top_px": 40, "left_px": 40 },
    "typography_by_ratio": {
        "1:1":  { "headline_size_range": [58, 72], "body_size_range": [22, 28], "cta_size_range": [20, 24] },
        "3:4":  { "headline_size_range": [60, 76], "body_size_range": [22, 28], "cta_size_range": [20, 24] },
        "9:16": { "headline_size_range": [68, 88], "body_size_range": [26, 32], "cta_size_range": [22, 26] }
    },
    "layout": { "padding_min_px": 80 }
}
```

These values are injected into the generation prompt. The feedback processor can update them automatically when you submit quantitative feedback (e.g. "make the logo bigger"), or you can edit the file directly.

---

## API Endpoints

| Endpoint | Method | Description |
|----------|--------|-------------|
| `/api/intake` | POST | Submit a new idea → parse → generate variants |
| `/api/review` | GET | Get all variants pending review |
| `/api/review/approve` | POST | Approve variant(s) for deployment |
| `/api/review/reject` | POST | Reject variant(s) with feedback notes |
| `/api/review/return-to-review` | POST | Reset archived variant(s) back to draft |
| `/api/feedback/image` | POST | Submit natural language feedback on a generated image |
| `/api/feedback/like` | POST | Like an image — save as reference, update style notes |
| `/api/feedback/voice` | POST | Upload audio recording — transcribe, parse ad numbers, route feedback |
| `/api/feedback/style-notes` | GET | Get all style notes files for display |
| `/api/briefs` | GET | List all briefs sorted newest first (used for gallery group headers) |
| `/api/variants` | GET | List all variants (optional `?status=` filter) |
| `/api/meta/adsets` | GET | List active Meta ad sets (for deploy modal dropdown) |
| `/api/deploy` | POST | Deploy an approved variant to Meta |
| `/api/meta/poll-status` | POST | Manually poll Meta review status for all live variants |
| `/api/performance` | GET | Portfolio-level performance summary |
| `/api/performance/{id}` | GET | Single variant performance data |
| `/api/decisions` | GET | Run and return latest scale/kill/wait decisions |
| `/api/regression` | GET | Get latest regression playbook |

---

## Environment Variables

| Variable | Required For | Description |
|----------|-------------|-------------|
| `ANTHROPIC_API_KEY` | Intake, copy gen, HTML/CSS strategy, feedback | Claude API key |
| `OPENAI_API_KEY` | DALL-E strategy, voice feedback (Whisper) | OpenAI API key |
| `GOOGLE_GEMINI_API_KEY` | Imagen strategy | Google Gemini API key |
| `META_ACCESS_TOKEN` | Meta deployment | Long-lived token with ads_management, ads_read, pages_manage_ads |
| `META_AD_ACCOUNT_ID` | Meta deployment | Ad account ID (format: `act_XXXXXXXXXX`) |
| `META_APP_ID` | Meta deployment | Meta app ID |
| `META_APP_SECRET` | Meta deployment | Meta app secret |
| `META_PAGE_ID` | Meta deployment | Facebook Page ID for AdCreative |
| `META_DESTINATION_URL` | Meta deployment | Default landing page (default: `https://jotpsych.com`) |
| `SLACK_WEBHOOK_URL` | Slack notifications | Incoming webhook URL for `#ads-engine` channel |

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
2. Implement `generate_image(brief, copy_data, index, assets_dir, aspect_ratio) -> str`
3. Implement `is_available() -> bool`
4. Register it in `STRATEGY_REGISTRY` at the bottom of the file
5. Add a menu entry in `prompt_visual_style()` in `generator.py`
6. Add a row to `VISUAL_STYLE_TO_TYPE` in `feedback.py` and `VISUAL_STYLE_STRATEGY_MAP` in `strategies.py`
