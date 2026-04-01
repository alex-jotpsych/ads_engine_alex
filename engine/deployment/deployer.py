"""
Ad Deployer — pushes approved variants to Meta and Google.

Meta: Marketing API via facebook-business SDK (v21.0+)
Google: Google Ads API (v17+) — stub, not yet implemented

Environment variables required for Meta:
  META_APP_ID, META_APP_SECRET, META_ACCESS_TOKEN,
  META_AD_ACCOUNT_ID, META_PAGE_ID, META_DESTINATION_URL
"""

from __future__ import annotations

import os
from datetime import datetime
from typing import Optional

from engine.models import AdVariant, AdStatus, Platform
from engine.store import Store

# ---------------------------------------------------------------------------
# CTA button text → Meta CTA type
# ---------------------------------------------------------------------------

CTA_MAP: dict[str, str] = {
    "learn more":     "LEARN_MORE",
    "sign up":        "SIGN_UP",
    "get started":    "SIGN_UP",
    "try free":       "SIGN_UP",
    "start free":     "SIGN_UP",
    "book a demo":    "CONTACT_US",
    "contact us":     "CONTACT_US",
    "request demo":   "CONTACT_US",
    "download":       "DOWNLOAD",
    "watch video":    "WATCH_VIDEO",
    "shop now":       "SHOP_NOW",
    "subscribe":      "SUBSCRIBE",
    "apply now":      "APPLY_NOW",
    "get offer":      "GET_OFFER",
}

def _resolve_cta(cta_text: str) -> str:
    """Map our CTA button text to a Meta CTA type string."""
    return CTA_MAP.get(cta_text.lower().strip(), "LEARN_MORE")


class MetaDeployer:
    """
    Deploys ads to Meta (Facebook/Instagram) via the Marketing API.

    Requires env vars:
      META_APP_ID, META_APP_SECRET, META_ACCESS_TOKEN,
      META_AD_ACCOUNT_ID, META_PAGE_ID, META_DESTINATION_URL
    """

    def __init__(
        self,
        access_token: str,
        ad_account_id: str,
        app_id: str,
        app_secret: str,
        page_id: str,
        destination_url: str,
    ):
        self.access_token = access_token
        self.ad_account_id = ad_account_id
        self.page_id = page_id
        self.destination_url = destination_url

        from facebook_business.api import FacebookAdsApi
        from facebook_business.adobjects.adaccount import AdAccount

        FacebookAdsApi.init(
            app_id=app_id,
            app_secret=app_secret,
            access_token=access_token,
        )
        self.account = AdAccount(ad_account_id)

    # ------------------------------------------------------------------
    # Asset upload
    # ------------------------------------------------------------------

    def upload_asset(self, variant: AdVariant) -> str:
        """
        Upload the variant's image to Meta's CDN.
        Returns the image_hash used to reference it in AdCreative.
        """
        from facebook_business.adobjects.adimage import AdImage

        image = AdImage(parent_id=self.ad_account_id)
        image[AdImage.Field.filename] = variant.asset_path
        image.remote_create()
        return image[AdImage.Field.hash]

    # ------------------------------------------------------------------
    # Create ad (full 4-step flow)
    # ------------------------------------------------------------------

    def create_ad(
        self,
        variant: AdVariant,
        adset_id: str,
        destination_url: Optional[str] = None,
    ) -> str:
        """
        Deploy a single approved variant to Meta.

        Steps:
        1. Upload image → get image_hash
        2. Create AdCreative
        3. Create Ad (starts in PENDING_REVIEW on Meta's side)

        Returns the Meta ad ID.
        """
        from facebook_business.adobjects.adcreative import AdCreative
        from facebook_business.adobjects.ad import Ad

        url = destination_url or self.destination_url
        cta_type = _resolve_cta(variant.cta_button)

        # 1. Upload image
        image_hash = self.upload_asset(variant)

        # 2. Create AdCreative
        creative_params = {
            AdCreative.Field.name: variant.headline[:100],
            AdCreative.Field.object_story_spec: {
                "page_id": self.page_id,
                "link_data": {
                    "image_hash": image_hash,
                    "link": url,
                    "message": variant.primary_text,
                    "name": variant.headline,
                    "description": variant.description or "",
                    "call_to_action": {"type": cta_type},
                },
            },
        }
        creative = self.account.create_ad_creative(creative_params)

        # 3. Create Ad
        ad_params = {
            Ad.Field.name: variant.headline[:100],
            Ad.Field.adset_id: adset_id,
            Ad.Field.creative: {"creative_id": creative.get_id()},
            Ad.Field.status: Ad.Status.paused,  # Start paused — activate manually in Ads Manager after review
        }
        ad = self.account.create_ad(ad_params)
        return ad.get_id()

    # ------------------------------------------------------------------
    # Ad status polling
    # ------------------------------------------------------------------

    def get_ad_status(self, meta_ad_id: str) -> dict:
        """
        Poll Meta for the current review status of an ad.

        Returns:
          {
            "status": "pending_review" | "active" | "disapproved" | ...,
            "reasons": ["MISLEADING_CLAIMS", ...]   # only on disapproval
          }
        """
        from facebook_business.adobjects.ad import Ad

        ad = Ad(meta_ad_id)
        ad.api_get(fields=["effective_status", "review_feedback"])

        effective_status = ad.get("effective_status", "unknown").lower()
        review_feedback = ad.get("review_feedback") or []

        reasons: list[str] = []
        for feedback in review_feedback:
            reasons.extend(feedback.get("rejection_reasons", []))

        return {"status": effective_status, "reasons": reasons}

    # ------------------------------------------------------------------
    # Ad management
    # ------------------------------------------------------------------

    def pause_ad(self, meta_ad_id: str) -> bool:
        from facebook_business.adobjects.ad import Ad
        Ad(meta_ad_id).api_update({"status": "PAUSED"})
        return True

    def resume_ad(self, meta_ad_id: str) -> bool:
        from facebook_business.adobjects.ad import Ad
        Ad(meta_ad_id).api_update({"status": "ACTIVE"})
        return True

    def delete_ad(self, meta_ad_id: str) -> bool:
        from facebook_business.adobjects.ad import Ad
        Ad(meta_ad_id).api_delete()
        return True

    # ------------------------------------------------------------------
    # Account introspection
    # ------------------------------------------------------------------

    def list_adsets(self) -> list[dict]:
        """
        Return all active adsets in the account.
        Used to populate the deploy modal dropdown.
        """
        from facebook_business.adobjects.adset import AdSet

        adsets = self.account.get_ad_sets(
            fields=["id", "name", "daily_budget", "campaign_id", "status"]
        )
        return [
            {
                "id": a["id"],
                "name": a["name"],
                "daily_budget_cents": a.get("daily_budget"),
                "campaign_id": a.get("campaign_id"),
                "status": a.get("status"),
            }
            for a in adsets
            if a.get("status") in ("ACTIVE", "PAUSED")
        ]


# ---------------------------------------------------------------------------
# Google Deployer — stub
# ---------------------------------------------------------------------------

class GoogleDeployer:
    """
    Deploys ads to Google Ads via Google Ads API.

    Requires:
    - GOOGLE_ADS_DEVELOPER_TOKEN
    - GOOGLE_ADS_CLIENT_ID
    - GOOGLE_ADS_CLIENT_SECRET
    - GOOGLE_ADS_REFRESH_TOKEN
    - GOOGLE_ADS_CUSTOMER_ID
    """

    def __init__(self, customer_id: str, credentials_path: str):
        self.customer_id = customer_id
        self.credentials_path = credentials_path

    def upload_asset(self, variant: AdVariant) -> str:
        raise NotImplementedError("Google asset upload not yet implemented")

    def create_ad(self, variant: AdVariant, campaign_id: str, ad_group_id: str) -> str:
        raise NotImplementedError("Google ad creation not yet implemented")

    def pause_ad(self, google_ad_id: str) -> bool:
        raise NotImplementedError("Google ad pause not yet implemented")

    def resume_ad(self, google_ad_id: str) -> bool:
        raise NotImplementedError("Google ad resume not yet implemented")


# ---------------------------------------------------------------------------
# Unified deployer
# ---------------------------------------------------------------------------

class AdDeployer:
    """
    Unified deployer — routes to Meta or Google based on variant platform.
    """

    def __init__(
        self,
        store: Store,
        meta: Optional[MetaDeployer] = None,
        google: Optional[GoogleDeployer] = None,
    ):
        self.store = store
        self.meta = meta
        self.google = google

    @classmethod
    def from_env(cls, store: Store) -> "AdDeployer":
        """
        Construct from environment variables.
        Returns a deployer with Meta configured if all keys are present
        and the facebook-business SDK is installed.
        Logs a warning and continues without Meta if either is missing.
        """
        meta: Optional[MetaDeployer] = None
        access_token = os.getenv("META_ACCESS_TOKEN", "")
        if access_token:
            try:
                meta = MetaDeployer(
                    access_token=access_token,
                    ad_account_id=os.environ["META_AD_ACCOUNT_ID"],
                    app_id=os.environ["META_APP_ID"],
                    app_secret=os.environ["META_APP_SECRET"],
                    page_id=os.environ.get("META_PAGE_ID", ""),
                    destination_url=os.getenv("META_DESTINATION_URL", "https://jotpsych.com"),
                )
            except ImportError:
                print(
                    "[WARN] facebook-business SDK not installed — Meta deployer disabled. "
                    "Run: pip install facebook-business"
                )
            except Exception as e:
                print(f"[WARN] Meta deployer init failed: {e} — Meta disabled.")
        return cls(store=store, meta=meta)

    def deploy_variant(
        self,
        variant: AdVariant,
        adset_id: str,
        destination_url: Optional[str] = None,
    ) -> AdVariant:
        """Deploy a single approved variant to its target platform."""
        if variant.status != AdStatus.APPROVED:
            raise ValueError(f"Variant {variant.id} is {variant.status}, not APPROVED")

        platform = variant.taxonomy.platform

        if platform == Platform.META:
            if not self.meta:
                raise RuntimeError(
                    "Meta deployer not configured — set META_ACCESS_TOKEN in .env"
                )
            ad_id = self.meta.create_ad(variant, adset_id, destination_url)
            variant.meta_ad_id = ad_id
            variant.meta_review_status = "pending_review"

        elif platform == Platform.GOOGLE:
            if not self.google:
                raise RuntimeError("Google deployer not configured")
            ad_id = self.google.create_ad(variant, adset_id, adset_id)
            variant.google_ad_id = ad_id

        variant.status = AdStatus.LIVE
        self.store.save_variant(variant)
        return variant

    def kill_variant(self, variant: AdVariant) -> AdVariant:
        """Kill a live ad — remove from platform and mark as killed."""
        if variant.taxonomy.platform == Platform.META and variant.meta_ad_id:
            if self.meta:
                self.meta.delete_ad(variant.meta_ad_id)
        elif variant.taxonomy.platform == Platform.GOOGLE and variant.google_ad_id:
            if self.google:
                self.google.pause_ad(variant.google_ad_id)

        variant.status = AdStatus.KILLED
        self.store.save_variant(variant)
        return variant

    def pause_variant(self, variant: AdVariant) -> AdVariant:
        """Pause a live ad temporarily."""
        if variant.taxonomy.platform == Platform.META and variant.meta_ad_id:
            if self.meta:
                self.meta.pause_ad(variant.meta_ad_id)
        elif variant.taxonomy.platform == Platform.GOOGLE and variant.google_ad_id:
            if self.google:
                self.google.pause_ad(variant.google_ad_id)

        variant.status = AdStatus.PAUSED
        self.store.save_variant(variant)
        return variant

    def poll_meta_ad_statuses(self, notifier=None) -> list[dict]:
        """
        Check Meta's review status for all LIVE variants with a meta_ad_id.
        Updates variant records and fires Slack notifications on state changes.

        Returns a list of status update dicts for logging.
        """
        if not self.meta:
            return []

        live_variants = [
            v for v in self.store.get_variants_by_status(AdStatus.LIVE)
            if v.meta_ad_id and v.meta_review_status != "active"
        ]

        updates = []
        for variant in live_variants:
            try:
                result = self.meta.get_ad_status(variant.meta_ad_id)
                new_status = result["status"]
                reasons = result["reasons"]

                if new_status == variant.meta_review_status:
                    continue  # No change

                variant.meta_review_status = new_status

                if new_status == "active":
                    if notifier:
                        notifier.notify_meta_approved(variant)

                elif new_status == "disapproved":
                    variant.meta_rejection_reasons = reasons or []
                    variant.status = AdStatus.REJECTED
                    if notifier:
                        notifier.notify_meta_rejected(variant, reasons)

                self.store.save_variant(variant)
                updates.append({
                    "variant_id": variant.id,
                    "meta_ad_id": variant.meta_ad_id,
                    "new_status": new_status,
                    "reasons": reasons,
                })

            except Exception as e:
                print(f"  [WARN] Could not poll status for {variant.meta_ad_id}: {e}")

        return updates
