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

from datetime import date
from typing import Optional

from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

from engine.store import Store
from engine.intake.parser import IntakeParser
from engine.generation.generator import CreativeGenerator
from engine.review.reviewer import ReviewPipeline
from engine.decisions.engine import DecisionEngine
from engine.regression.model import CreativeRegressionModel
from engine.notifications import SlackNotifier

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
notifier = SlackNotifier()


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
    """Reject variants with feedback."""
    if not action.notes:
        raise HTTPException(status_code=400, detail="Rejection notes are required")
    rejected = review_pipeline.batch_reject(action.variant_ids, action.reviewer, action.notes)
    return {"rejected": len(rejected)}


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
