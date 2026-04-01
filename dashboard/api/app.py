"""
Dashboard API — FastAPI backend for the review gallery and performance views.

Endpoints:
- GET  /api/review          — variants pending review (gallery data)
- POST /api/review/approve  — approve variant(s)
- POST /api/review/reject   — reject variant(s) with feedback
- GET  /api/performance     — portfolio performance overview
- GET  /api/performance/{variant_id} — single variant performance
- GET  /api/decisions       — latest scale/kill/wait decisions
- GET  /api/regression      — latest regression insights / playbook
- POST /api/intake          — submit a new idea dump
- GET  /api/variants        — all variants with filters
"""

from __future__ import annotations

from dotenv import load_dotenv
load_dotenv()

from datetime import date
from typing import Optional

from fastapi import FastAPI, HTTPException, UploadFile, File, Form
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

from engine.store import Store
from engine.intake.parser import IntakeParser
from engine.generation.generator import CreativeGenerator
from engine.generation.feedback import FeedbackProcessor
from engine.review.reviewer import ReviewPipeline
from engine.decisions.engine import DecisionEngine
from engine.regression.model import CreativeRegressionModel
from engine.notifications import SlackNotifier
from engine.deployment.deployer import AdDeployer

app = FastAPI(title="JotPsych Ads Engine", version="0.1.0")

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],  # INTERN: restrict in production
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# Serve generated image assets from data/creatives/
app.mount("/assets", StaticFiles(directory="data/creatives"), name="assets")

# Initialize services
store = Store()
review_pipeline = ReviewPipeline(store)
decision_engine = DecisionEngine(store)
regression_model = CreativeRegressionModel(store)
notifier = SlackNotifier(webhook_url=__import__("os").getenv("SLACK_WEBHOOK_URL"))
deployer = AdDeployer.from_env(store)


# ---------------------------------------------------------------------------
# Request/Response models
# ---------------------------------------------------------------------------

class IdeaInput(BaseModel):
    raw_text: str
    source: str = "manual"


class ReviewAction(BaseModel):
    variant_ids: list[str]
    reviewer: str
    notes: Optional[str] = None
    display_map: Optional[dict[str, str]] = None  # { "1": "variant-uuid", ... }


class ImageFeedback(BaseModel):
    variant_id: Optional[str] = None
    feedback: str
    aspect_ratio: Optional[str] = None


class ImageLike(BaseModel):
    variant_id: str
    note: Optional[str] = None
    aspect_ratio: Optional[str] = None


class DeployRequest(BaseModel):
    variant_id: str
    adset_id: str
    destination_url: Optional[str] = None


# ---------------------------------------------------------------------------
# Intake
# ---------------------------------------------------------------------------

@app.post("/api/intake")
async def submit_idea(idea: IdeaInput):
    """Parse a free-form idea into a brief, generate variants."""
    parser = IntakeParser()
    brief = parser.parse(idea.raw_text, source=idea.source)
    store.save_brief(brief)

    generator = CreativeGenerator()
    variants = generator.generate(brief)
    for v in variants:
        store.save_variant(v)

    notifier.notify_variants_generated(brief.id, variants)

    return {
        "brief_id": brief.id,
        "brief": brief.model_dump(),
        "variants_generated": len(variants),
    }


# ---------------------------------------------------------------------------
# Review
# ---------------------------------------------------------------------------

@app.get("/api/review")
async def get_review_queue():
    """Get all variants pending review."""
    pending = review_pipeline.get_pending_review()
    return {
        "count": len(pending),
        "variants": [v.model_dump() for v in pending],
    }


@app.post("/api/review/approve")
async def approve_variants(action: ReviewAction):
    """Approve variants for deployment."""
    approved = review_pipeline.batch_approve(action.variant_ids, action.reviewer)
    return {"approved": len(approved)}


@app.post("/api/review/reject")
async def reject_variants(action: ReviewAction):
    """Reject variants with feedback and route notes to style guides.

    If the notes reference specific ad numbers (e.g. "Ad 2 is too dark, Ad 5 too busy")
    and a display_map is provided, Claude parses out per-ad feedback and routes each piece
    to the correct variant's style notes file.

    If no ad numbers are detected, the full notes are applied to every rejected variant.
    """
    if not action.notes:
        raise HTTPException(status_code=400, detail="Rejection notes are required")

    rejected = review_pipeline.batch_reject(action.variant_ids, action.reviewer, action.notes)

    from engine.generation.strategies import VISUAL_STYLE_STRATEGY_MAP

    # Build a UUID→variant lookup from the rejected list for fast access
    rejected_by_id = {v.id: v for v in rejected}

    # --- Try to parse ad-number references if a display_map was supplied ---
    per_ad_feedback: list[dict] = []  # [{"ad_number": "2", "feedback": "...", "variant_id": "uuid"}]

    if action.display_map and action.notes:
        try:
            from anthropic import Anthropic
            import json as _json

            claude = Anthropic()
            parse_response = claude.messages.create(
                model="claude-sonnet-4-20250514",
                max_tokens=1000,
                system=(
                    "Extract all (ad_number, feedback_text) pairs from the reviewer's notes. "
                    "The reviewer references ads by number (e.g. 'Ad 2', '#3', 'the third one'). "
                    "Return a JSON array only, no explanation: "
                    '[{"ad_number": <int>, "feedback": "<feedback text>"}]. '
                    "If there are no ad number references, return an empty array []."
                ),
                messages=[{"role": "user", "content": action.notes}],
            )
            pairs_text = parse_response.content[0].text.strip()
            if pairs_text.startswith("```"):
                pairs_text = pairs_text.split("```")[1].split("```")[0].strip()
                if pairs_text.startswith("json"):
                    pairs_text = pairs_text[4:].strip()
            pairs: list[dict] = _json.loads(pairs_text)

            for pair in pairs:
                ad_num = str(pair.get("ad_number", ""))
                feedback_text = pair.get("feedback", "").strip()
                variant_id = action.display_map.get(ad_num)
                if feedback_text and variant_id and variant_id in rejected_by_id:
                    per_ad_feedback.append({
                        "ad_number": ad_num,
                        "feedback": feedback_text,
                        "variant_id": variant_id,
                    })
        except Exception:
            per_ad_feedback = []  # Fall through to bulk routing below

    # --- Route feedback to style notes ---
    if per_ad_feedback:
        # Per-ad routing: each piece of feedback goes only to its specific variant's style file
        for item in per_ad_feedback:
            variant = rejected_by_id[item["variant_id"]]
            try:
                visual_style = variant.taxonomy.visual_style if variant.taxonomy else None
                aspect_ratio = variant.taxonomy.aspect_ratio if variant.taxonomy else None
                strategy_name = VISUAL_STYLE_STRATEGY_MAP.get(visual_style or "", None)
                feedback_processor.process_feedback(
                    feedback=item["feedback"],
                    variant_id=variant.id,
                    visual_style=visual_style,
                    aspect_ratio=aspect_ratio,
                    strategy_name=strategy_name,
                    taxonomy=variant.taxonomy.model_dump() if variant.taxonomy else None,
                    asset_path=variant.asset_path,
                )
            except Exception:
                pass
    else:
        # Bulk routing: no ad numbers detected — apply the full notes to every rejected variant
        for variant in rejected:
            try:
                visual_style = variant.taxonomy.visual_style if variant.taxonomy else None
                aspect_ratio = variant.taxonomy.aspect_ratio if variant.taxonomy else None
                strategy_name = VISUAL_STYLE_STRATEGY_MAP.get(visual_style or "", None)
                feedback_processor.process_feedback(
                    feedback=action.notes,
                    variant_id=variant.id,
                    visual_style=visual_style,
                    aspect_ratio=aspect_ratio,
                    strategy_name=strategy_name,
                    taxonomy=variant.taxonomy.model_dump() if variant.taxonomy else None,
                    asset_path=variant.asset_path,
                )
            except Exception:
                pass

    return {
        "rejected": len(rejected),
        "feedback_routing": "per_ad" if per_ad_feedback else "bulk",
        "per_ad_feedback": per_ad_feedback,
    }


@app.post("/api/review/return-to-review")
async def return_to_review(action: ReviewAction):
    """Move variant(s) back to draft status for re-review."""
    returned = []
    for vid in action.variant_ids:
        try:
            variant = store.get_variant(vid)
            variant.status = "draft"
            variant.review_notes = None
            variant.reviewer = None
            variant.reviewed_at = None
            store.save_variant(variant)
            returned.append(vid)
        except FileNotFoundError:
            pass
    return {"returned": len(returned)}


# ---------------------------------------------------------------------------
# Image Feedback — iterative prompt refinement
# ---------------------------------------------------------------------------

feedback_processor = FeedbackProcessor()


@app.post("/api/feedback/image")
async def submit_image_feedback(fb: ImageFeedback):
    """Submit natural language feedback on a generated image.

    The feedback is processed by Claude, which updates the appropriate
    style notes file (photo, graphic, or global) based on the variant's
    visual_style. All future generations of that type pick up the changes.
    """
    visual_style = None
    aspect_ratio = fb.aspect_ratio
    strategy_name = None
    taxonomy = None
    asset_path = None

    if fb.variant_id:
        try:
            variant = store.get_variant(fb.variant_id)
            asset_path = variant.asset_path
            taxonomy = variant.taxonomy.model_dump() if variant.taxonomy else None
            if taxonomy:
                visual_style = taxonomy.get("visual_style")
                if aspect_ratio is None:
                    aspect_ratio = taxonomy.get("aspect_ratio", "1:1")
                from engine.generation.strategies import VISUAL_STYLE_STRATEGY_MAP
                strategy_name = VISUAL_STYLE_STRATEGY_MAP.get(visual_style or "", None)
        except (FileNotFoundError, Exception):
            pass  # Proceed without context — feedback still useful

    result = feedback_processor.process_feedback(
        feedback=fb.feedback,
        variant_id=fb.variant_id,
        visual_style=visual_style,
        aspect_ratio=aspect_ratio,
        strategy_name=strategy_name,
        taxonomy=taxonomy,
        asset_path=asset_path,
    )

    response = {
        "status": "processed",
        "notes_file": result["notes_file"],
        "updated_notes": result["updated_notes"],
    }
    if result.get("config_updates"):
        response["config_updates"] = result["config_updates"]
    return response


@app.post("/api/feedback/like")
async def like_image(like: ImageLike):
    """Like a generated image — saves it as a positive reference and updates style notes.

    The image is copied to the appropriate liked_photo/ or liked_graphic/ directory
    based on the variant's visual_style, and Claude updates the style notes with
    what makes this image good.
    """
    visual_style = None
    aspect_ratio = like.aspect_ratio
    asset_path = None
    taxonomy = None

    try:
        variant = store.get_variant(like.variant_id)
        asset_path = variant.asset_path
        taxonomy = variant.taxonomy.model_dump() if variant.taxonomy else None
        if taxonomy:
            visual_style = taxonomy.get("visual_style")
            if aspect_ratio is None:
                aspect_ratio = taxonomy.get("aspect_ratio", "1:1")
    except (FileNotFoundError, Exception):
        pass

    result = feedback_processor.process_like(
        visual_style=visual_style,
        aspect_ratio=aspect_ratio,
        asset_path=asset_path,
        note=like.note,
        taxonomy=taxonomy,
        variant_id=like.variant_id,
    )

    return {
        "status": "liked",
        "notes_file": result["notes_file"],
        "reference_path": result["reference_path"],
        "updated_notes": result["updated_notes"],
    }


@app.get("/api/feedback/style-notes")
async def get_style_notes():
    """Get all style notes files for display in the feedback UI."""
    return feedback_processor.get_all_notes()


@app.post("/api/feedback/voice")
async def submit_voice_feedback(
    audio: UploadFile = File(...),
    display_map: str = Form(...),
):
    """Process a voice recording containing feedback on numbered ads.

    The audio is transcribed with Whisper, then Claude parses the transcript
    to extract (ad_number, feedback_text) pairs. Each pair is routed to the
    correct style notes file via the variant's visual_style × aspect_ratio.

    Form fields:
    - audio: audio file (.m4a, .mp3, .wav)
    - display_map: JSON string mapping display number → variant UUID
                   e.g. {"1": "uuid-abc", "2": "uuid-def"}
    """
    import json
    import tempfile
    import os
    from openai import OpenAI
    from anthropic import Anthropic

    # Parse display_map
    try:
        num_to_uuid: dict[str, str] = json.loads(display_map)
    except json.JSONDecodeError:
        raise HTTPException(status_code=400, detail="display_map must be valid JSON")

    # Save audio to a temp file and transcribe with Whisper
    audio_bytes = await audio.read()
    suffix = os.path.splitext(audio.filename or "audio.m4a")[1] or ".m4a"
    with tempfile.NamedTemporaryFile(suffix=suffix, delete=False) as tmp:
        tmp.write(audio_bytes)
        tmp_path = tmp.name

    try:
        oai = OpenAI()
        with open(tmp_path, "rb") as f:
            transcription = oai.audio.transcriptions.create(model="whisper-1", file=f)
        transcript = transcription.text
    finally:
        os.unlink(tmp_path)

    # Ask Claude to extract (ad_number, feedback) pairs from the transcript
    claude = Anthropic()
    parse_response = claude.messages.create(
        model="claude-sonnet-4-20250514",
        max_tokens=1000,
        system=(
            "Extract all (ad_number, feedback_text) pairs from the transcript. "
            "The speaker references ads by number (e.g. 'Ad 2', '#3', 'the third one'). "
            "Return a JSON array only, no explanation: "
            '[{"ad_number": <int>, "feedback": "<feedback text>"}]'
        ),
        messages=[{"role": "user", "content": transcript}],
    )

    try:
        pairs_text = parse_response.content[0].text.strip()
        if pairs_text.startswith("```"):
            pairs_text = pairs_text.split("```")[1].split("```")[0].strip()
            if pairs_text.startswith("json"):
                pairs_text = pairs_text[4:].strip()
        pairs: list[dict] = json.loads(pairs_text)
    except (json.JSONDecodeError, Exception):
        pairs = []

    # Route each feedback to the correct style notes file
    processed = []
    for pair in pairs:
        ad_num = str(pair.get("ad_number", ""))
        feedback_text = pair.get("feedback", "").strip()
        if not feedback_text or ad_num not in num_to_uuid:
            continue

        variant_id = num_to_uuid[ad_num]
        visual_style = None
        aspect_ratio = None
        asset_path = None
        taxonomy = None

        try:
            variant = store.get_variant(variant_id)
            asset_path = variant.asset_path
            taxonomy = variant.taxonomy.model_dump() if variant.taxonomy else None
            if taxonomy:
                visual_style = taxonomy.get("visual_style")
                aspect_ratio = taxonomy.get("aspect_ratio", "1:1")
        except (FileNotFoundError, Exception):
            pass

        result = feedback_processor.process_feedback(
            feedback=feedback_text,
            variant_id=variant_id,
            visual_style=visual_style,
            aspect_ratio=aspect_ratio,
            taxonomy=taxonomy,
            asset_path=asset_path,
        )
        processed.append({
            "ad_number": int(ad_num),
            "variant_id": variant_id,
            "notes_file": result["notes_file"],
        })

    return {
        "transcript": transcript,
        "feedback_processed": processed,
    }


# ---------------------------------------------------------------------------
# Performance
# ---------------------------------------------------------------------------

@app.get("/api/performance")
async def get_portfolio_performance():
    """Portfolio-level performance summary."""
    snapshots = store.get_all_snapshots()
    if not snapshots:
        return {"status": "no_data"}

    total_spend = sum(s.spend for s in snapshots)
    total_notes = sum(s.first_note_completions for s in snapshots)
    total_clicks = sum(s.clicks for s in snapshots)
    total_impressions = sum(s.impressions for s in snapshots)

    return {
        "total_spend": total_spend,
        "total_first_notes": total_notes,
        "blended_cpa": total_spend / total_notes if total_notes > 0 else None,
        "total_clicks": total_clicks,
        "total_impressions": total_impressions,
        "blended_ctr": total_clicks / total_impressions if total_impressions > 0 else 0,
        "active_variants": len(store.get_variants_by_status("live")),
    }


@app.get("/api/performance/{variant_id}")
async def get_variant_performance(variant_id: str):
    """Performance data for a specific variant."""
    try:
        variant = store.get_variant(variant_id)
    except FileNotFoundError:
        raise HTTPException(status_code=404, detail="Variant not found")

    snapshots = store.get_snapshots_for_variant(variant_id)
    decisions = store.get_decisions_for_variant(variant_id)

    return {
        "variant": variant.model_dump(),
        "snapshots": [s.model_dump() for s in snapshots],
        "decisions": [d.model_dump() for d in decisions],
    }


# ---------------------------------------------------------------------------
# Decisions
# ---------------------------------------------------------------------------

@app.get("/api/decisions")
async def get_latest_decisions():
    """Get the most recent decision batch."""
    decisions = decision_engine.run_daily()
    return {
        "date": date.today().isoformat(),
        "decisions": [d.model_dump() for d in decisions],
        "summary": {
            "scale": len([d for d in decisions if d.verdict.value == "scale"]),
            "kill": len([d for d in decisions if d.verdict.value == "kill"]),
            "wait": len([d for d in decisions if d.verdict.value == "wait"]),
        },
    }


# ---------------------------------------------------------------------------
# Regression
# ---------------------------------------------------------------------------

@app.get("/api/regression")
async def get_regression_insights():
    """Get the latest regression playbook."""
    playbook = regression_model.get_creative_playbook()
    return playbook


# ---------------------------------------------------------------------------
# Variants
# ---------------------------------------------------------------------------

@app.get("/api/variants")
async def list_variants(status: Optional[str] = None):
    """List all variants, optionally filtered by status."""
    if status:
        variants = store.get_variants_by_status(status)
    else:
        variants = store.get_all_variants()

    return {
        "count": len(variants),
        "variants": [v.model_dump() for v in variants],
    }


@app.get("/api/briefs")
async def list_briefs():
    """Return brief metadata for group headers in the review gallery."""
    briefs = store.get_all_briefs()
    briefs.sort(key=lambda b: b.created_at, reverse=True)
    return [
        {
            "id": b.id,
            "raw_input": b.raw_input,
            "value_proposition": b.value_proposition,
            "created_at": b.created_at.isoformat(),
        }
        for b in briefs
    ]


# ---------------------------------------------------------------------------
# Meta Deployment
# ---------------------------------------------------------------------------

@app.get("/api/meta/adsets")
async def list_meta_adsets():
    """List active adsets in the Meta account — used to populate the deploy modal dropdown."""
    if not deployer.meta:
        raise HTTPException(
            status_code=503,
            detail="Meta deployer not configured — set META_ACCESS_TOKEN in .env",
        )
    try:
        adsets = deployer.meta.list_adsets()
        return {"adsets": adsets}
    except Exception as e:
        raise HTTPException(status_code=502, detail=f"Meta API error: {e}")


@app.post("/api/deploy")
async def deploy_variant_to_meta(body: DeployRequest):
    """
    Deploy an APPROVED variant to Meta.

    Sets status = LIVE, meta_review_status = pending_review.
    Sends a Slack notification.
    """
    if not deployer.meta:
        raise HTTPException(
            status_code=503,
            detail="Meta deployer not configured — set META_ACCESS_TOKEN in .env",
        )
    try:
        variant = store.get_variant(body.variant_id)
    except FileNotFoundError:
        raise HTTPException(status_code=404, detail="Variant not found")

    try:
        deployed = deployer.deploy_variant(
            variant,
            adset_id=body.adset_id,
            destination_url=body.destination_url,
        )
        notifier.notify_meta_submitted(deployed, deployed.meta_ad_id)
        return {
            "status": "submitted",
            "meta_ad_id": deployed.meta_ad_id,
            "meta_review_status": deployed.meta_review_status,
        }
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    except Exception as e:
        raise HTTPException(status_code=502, detail=f"Meta API error: {e}")


@app.post("/api/meta/poll-status")
async def poll_meta_status():
    """
    Manually trigger ad review status polling for all LIVE Meta variants.
    Also runs automatically in the daily orchestrator cycle.
    """
    if not deployer.meta:
        raise HTTPException(
            status_code=503,
            detail="Meta deployer not configured — set META_ACCESS_TOKEN in .env",
        )
    updates = deployer.poll_meta_ad_statuses(notifier=notifier)
    return {"updates_processed": len(updates), "updates": updates}
