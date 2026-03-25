"""
Core data models for the ads engine.
Every ad, brief, and performance record flows through these.
"""

from __future__ import annotations

import uuid
from datetime import datetime, date
from enum import Enum
from typing import Optional

from pydantic import BaseModel, Field


# ---------------------------------------------------------------------------
# Enums
# ---------------------------------------------------------------------------

class Platform(str, Enum):
    META = "meta"
    GOOGLE = "google"


class AdFormat(str, Enum):
    SINGLE_IMAGE = "single_image"
    CAROUSEL = "carousel"
    VIDEO = "video"
    GIF = "gif"
    STORY = "story"
    REELS = "reels"
    SEARCH_TEXT = "search_text"
    DISPLAY = "display"


class AdStatus(str, Enum):
    DRAFT = "draft"           # Generated, not yet reviewed
    APPROVED = "approved"     # Passed review, ready to deploy
    REJECTED = "rejected"     # Failed review
    LIVE = "live"             # Currently running
    PAUSED = "paused"         # Temporarily pulled
    KILLED = "killed"         # Permanently stopped
    GRADUATED = "graduated"   # Scaled up — proven winner


class DecisionVerdict(str, Enum):
    SCALE = "scale"
    KILL = "kill"
    WAIT = "wait"


# ---------------------------------------------------------------------------
# Creative Brief — what comes out of intake
# ---------------------------------------------------------------------------

class CreativeBrief(BaseModel):
    id: str = Field(default_factory=lambda: str(uuid.uuid4()))
    created_at: datetime = Field(default_factory=datetime.utcnow)

    # Raw input
    raw_input: str                    # The free-form dump (text, transcript, etc.)
    source: str = "manual"            # "manual", "slack", "voice", "swipe_file"

    # AI-structured fields
    target_audience: str              # "bh_clinicians" or "smb_clinic_owners"
    value_proposition: str            # Core promise
    pain_point: str                   # What problem are we poking?
    desired_action: str               # What should the viewer do?
    tone_direction: str               # e.g. "warm but urgent", "clinical authority"
    visual_direction: str             # e.g. "real clinician at desk, warm lighting"
    key_phrases: list[str] = []       # Specific language to try
    references: list[str] = []        # URLs, competitor examples, swipe file paths

    # Generation config
    num_variants: int = 6
    formats_requested: list[AdFormat] = [AdFormat.SINGLE_IMAGE, AdFormat.VIDEO]
    platforms: list[Platform] = [Platform.META]


# ---------------------------------------------------------------------------
# Creative Taxonomy — MECE element decomposition for regression
# ---------------------------------------------------------------------------

class CreativeTaxonomy(BaseModel):
    """
    Every ad variant gets auto-tagged across these dimensions.
    Each dimension is MECE — mutually exclusive, collectively exhaustive.
    The regression model uses these as features.
    """

    # MESSAGE LAYER — what the ad says
    message_type: str          # value_prop | pain_point | social_proof | urgency | education | comparison
    hook_type: str             # question | statistic | testimonial | provocative_claim | scenario | direct_benefit
    cta_type: str              # try_free | book_demo | learn_more | see_how | start_saving_time | watch_video
    tone: str                  # clinical | warm | urgent | playful | authoritative | empathetic

    # VISUAL LAYER — what the ad looks like
    visual_style: str          # photography | illustration | screen_capture | text_heavy | mixed_media | abstract
    subject_matter: str        # clinician_at_work | patient_interaction | product_ui | workflow_comparison | conceptual | data_viz
    color_mood: str            # brand_primary | warm_earth | cool_clinical | high_contrast | muted_soft | bold_saturated
    text_density: str          # headline_only | headline_subhead | detailed_copy | minimal_overlay

    # STRUCTURAL LAYER — how the ad is built
    format: AdFormat
    platform: Platform
    placement: str             # feed | story | reels | search | display | discover

    # COPY SPECIFICS — granular text features
    headline_word_count: int
    uses_number: bool          # "Save 2 hours/day" vs "Save time"
    uses_question: bool
    uses_first_person: bool    # "I" / "my" vs "you" / "your"
    uses_social_proof: bool    # mentions other clinicians, stats, testimonials
    copy_reading_level: float  # Flesch-Kincaid grade level


# ---------------------------------------------------------------------------
# Ad Variant — a single generated creative
# ---------------------------------------------------------------------------

class AdVariant(BaseModel):
    id: str = Field(default_factory=lambda: str(uuid.uuid4()))
    brief_id: str
    created_at: datetime = Field(default_factory=datetime.utcnow)

    # Content
    headline: str
    primary_text: str
    description: Optional[str] = None
    cta_button: str = "Learn More"
    asset_path: str               # Path to image/video file
    asset_type: str               # "image" or "video"

    # Taxonomy (auto-tagged)
    taxonomy: CreativeTaxonomy

    # Status
    status: AdStatus = AdStatus.DRAFT
    review_notes: Optional[str] = None
    reviewer: Optional[str] = None
    reviewed_at: Optional[datetime] = None

    # Platform IDs (populated after deployment)
    meta_ad_id: Optional[str] = None
    google_ad_id: Optional[str] = None


# ---------------------------------------------------------------------------
# Performance Snapshot — daily pull from platforms
# ---------------------------------------------------------------------------

class PerformanceSnapshot(BaseModel):
    id: str = Field(default_factory=lambda: str(uuid.uuid4()))
    ad_variant_id: str
    platform: Platform
    date: date
    pulled_at: datetime = Field(default_factory=datetime.utcnow)

    # Spend & reach
    spend: float
    impressions: int
    reach: int

    # Engagement
    clicks: int
    ctr: float                     # clicks / impressions
    cpc: float                     # spend / clicks

    # Conversion funnel
    landing_page_views: int
    signups: int
    first_note_completions: int    # PRIMARY CONVERSION

    # Derived
    cost_per_signup: Optional[float] = None
    cost_per_first_note: Optional[float] = None
    signup_to_note_rate: Optional[float] = None

    # Platform-specific quality scores
    meta_relevance_score: Optional[float] = None
    google_quality_score: Optional[float] = None


# ---------------------------------------------------------------------------
# Decision Record — daily scale/kill/wait output
# ---------------------------------------------------------------------------

class DecisionRecord(BaseModel):
    id: str = Field(default_factory=lambda: str(uuid.uuid4()))
    ad_variant_id: str
    date: date
    created_at: datetime = Field(default_factory=datetime.utcnow)

    verdict: DecisionVerdict
    confidence: float              # 0-1, based on data volume + signal strength
    reasoning: str                 # Human-readable explanation

    # Key metrics at time of decision
    total_spend: float
    total_first_notes: int
    cost_per_first_note: float
    days_live: int
    trend: str                     # "improving" | "stable" | "declining"

    # Action taken
    executed: bool = False
    executed_at: Optional[datetime] = None


# ---------------------------------------------------------------------------
# Regression Result — what the model learned
# ---------------------------------------------------------------------------

class RegressionResult(BaseModel):
    id: str = Field(default_factory=lambda: str(uuid.uuid4()))
    run_date: date
    created_at: datetime = Field(default_factory=datetime.utcnow)

    # Model metadata
    n_observations: int
    r_squared: float
    adjusted_r_squared: float

    # Feature coefficients with significance
    coefficients: dict[str, float]        # feature_name → coefficient
    p_values: dict[str, float]            # feature_name → p-value
    confidence_intervals: dict[str, tuple[float, float]]  # feature_name → (low, high)

    # Top insights (auto-generated)
    top_positive_features: list[str]      # Features that drive performance
    top_negative_features: list[str]      # Features that hurt performance
    insignificant_features: list[str]     # Features with p > 0.05

    # Diagnostics
    vif_scores: dict[str, float]          # Variance inflation factors (covariance check)
    durbin_watson: float                  # Autocorrelation check
    condition_number: float               # Multicollinearity diagnostic
