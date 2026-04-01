"""
Slack Notifications — posts updates to a Slack channel.

Events that trigger notifications:
- New variants generated (with preview thumbnails)
- Variants approved/rejected
- Ads deployed to platforms
- Daily decision digest (scale/kill/wait summary)
- Regression insights updated
- Budget alerts
"""

from __future__ import annotations

from datetime import date
from typing import Optional

from engine.models import AdVariant, DecisionRecord, DecisionVerdict, RegressionResult


# Channel configuration
DEFAULT_CHANNEL = "#ads-engine"
ALERTS_CHANNEL = "#ads-alerts"


class SlackNotifier:
    """
    Posts structured messages to Slack.

    Uses the MCP Slack integration when running inside Claude Code.
    Falls back to webhook for standalone/scheduled execution.

    Intern: wire up the webhook fallback for scheduled jobs.
    """

    def __init__(self, webhook_url: Optional[str] = None, channel: str = DEFAULT_CHANNEL):
        self.webhook_url = webhook_url
        self.channel = channel

    def notify_variants_generated(self, brief_id: str, variants: list[AdVariant]) -> None:
        """Post when new variants are ready for review."""
        n = len(variants)
        formats = set(v.taxonomy.format.value for v in variants)
        platforms = set(v.taxonomy.platform.value for v in variants)

        message = (
            f"*New Creative Batch Ready for Review*\n"
            f"Brief: `{brief_id[:8]}`\n"
            f"Variants: {n}\n"
            f"Formats: {', '.join(formats)}\n"
            f"Platforms: {', '.join(platforms)}\n"
            f"→ Review in dashboard"
        )
        self._send(message)

    def notify_daily_decisions(self, decisions: list[DecisionRecord]) -> None:
        """Post the daily scale/kill/wait digest."""
        scale = [d for d in decisions if d.verdict == DecisionVerdict.SCALE]
        kill = [d for d in decisions if d.verdict == DecisionVerdict.KILL]
        wait = [d for d in decisions if d.verdict == DecisionVerdict.WAIT]

        lines = [f"*Daily Ad Decisions — {date.today().isoformat()}*\n"]

        if scale:
            lines.append(f"🟢 *SCALE ({len(scale)})*")
            for d in scale:
                lines.append(f"  `{d.ad_variant_id[:8]}` — CPA ${d.cost_per_first_note:.2f} — {d.reasoning[:80]}")

        if kill:
            lines.append(f"🔴 *KILL ({len(kill)})*")
            for d in kill:
                lines.append(f"  `{d.ad_variant_id[:8]}` — CPA ${d.cost_per_first_note:.2f} — {d.reasoning[:80]}")

        if wait:
            lines.append(f"🟡 *WAIT ({len(wait)})*")
            for d in wait:
                lines.append(f"  `{d.ad_variant_id[:8]}` — {d.reasoning[:60]}")

        total_spend = sum(d.total_spend for d in decisions)
        total_notes = sum(d.total_first_notes for d in decisions)
        lines.append(f"\n*Portfolio*: ${total_spend:,.0f} spent → {total_notes} first notes")
        if total_notes > 0:
            lines.append(f"*Blended CPA*: ${total_spend / total_notes:.2f}")

        self._send("\n".join(lines))

    def notify_regression_update(self, result: RegressionResult) -> None:
        """Post when the regression model has new insights."""
        lines = [
            f"*Regression Model Updated — {result.run_date.isoformat()}*\n",
            f"R²: {result.r_squared:.3f} | Observations: {result.n_observations}",
        ]

        if result.top_positive_features:
            lines.append(f"\n*What's Working:* {', '.join(result.top_positive_features[:5])}")
        if result.top_negative_features:
            lines.append(f"*What to Avoid:* {', '.join(result.top_negative_features[:5])}")

        high_vif = [f for f, v in result.vif_scores.items() if v > 5]
        if high_vif:
            lines.append(f"\n⚠️ *Multicollinearity warning:* {', '.join(high_vif[:3])}")

        self._send("\n".join(lines))

    def notify_deployment(self, variants: list[AdVariant], platform: str) -> None:
        """Post when ads go live."""
        message = (
            f"*Ads Deployed to {platform.upper()}*\n"
            f"Count: {len(variants)}\n"
            f"Variants: {', '.join(v.id[:8] for v in variants)}"
        )
        self._send(message)

    def notify_budget_alert(self, daily_spend: float, daily_limit: float) -> None:
        """Post when daily spend approaches or exceeds limits."""
        pct = daily_spend / daily_limit * 100
        message = (
            f"⚠️ *Budget Alert*\n"
            f"Daily spend: ${daily_spend:,.2f} ({pct:.0f}% of ${daily_limit:,.2f} limit)"
        )
        self._send(message, channel=ALERTS_CHANNEL)

    def notify_meta_submitted(self, variant: AdVariant, meta_ad_id: str) -> None:
        """Post immediately after an ad is submitted to Meta for review."""
        message = (
            f"📤 *Ad Submitted to Meta for Review*\n"
            f"Variant: `{variant.id[:8]}` — {variant.headline[:60]}\n"
            f"Meta Ad ID: `{meta_ad_id}`\n"
            f"Review typically takes 24–48 hours. You'll get a notification when it's approved or rejected."
        )
        self._send(message)

    def notify_meta_approved(self, variant: AdVariant) -> None:
        """Post when Meta approves an ad and it goes live."""
        message = (
            f"✅ *Ad Approved and Live on Meta*\n"
            f"Variant: `{variant.id[:8]}` — {variant.headline[:60]}\n"
            f"Meta Ad ID: `{variant.meta_ad_id}`"
        )
        self._send(message)

    def notify_meta_rejected(self, variant: AdVariant, reasons: list[str]) -> None:
        """Post when Meta rejects an ad. Variant is automatically returned to draft."""
        reasons_text = ", ".join(reasons) if reasons else "unspecified"
        message = (
            f"🚫 *Ad Rejected by Meta*\n"
            f"Variant: `{variant.id[:8]}` — {variant.headline[:60]}\n"
            f"Rejection reasons: {reasons_text}\n"
            f"The variant has been returned to Draft — edit the copy or image and redeploy."
        )
        self._send(message, channel=ALERTS_CHANNEL)

    def _send(self, message: str, channel: Optional[str] = None) -> None:
        """Send a message to Slack via webhook, falling back to stdout."""
        target = channel or self.channel
        if self.webhook_url and "hooks.slack.com/services/" in self.webhook_url:
            try:
                import requests
                requests.post(
                    self.webhook_url,
                    json={"text": message},
                    timeout=5,
                )
            except Exception as e:
                print(f"[SLACK SEND FAILED] {e}")
                print(f"[SLACK → {target}] {message}")
        else:
            print(f"[SLACK → {target}] {message}")
