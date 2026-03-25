"""
Orchestrator — runs the full daily cycle and one-off commands.

This is the main entry point. It wires all pipeline stages together.

Daily cycle (scheduled):
1. Pull performance data from Meta + Google
2. Run decision engine (scale/kill/wait)
3. Execute decisions (pause/scale budget on platforms)
4. Run regression model
5. Post Slack digest

On-demand:
- Submit idea → generate variants
- Review variants → deploy approved
"""

from __future__ import annotations

from dotenv import load_dotenv
load_dotenv()

from datetime import date
from typing import Optional

from engine.store import Store
from engine.intake.parser import IntakeParser
from engine.generation.generator import CreativeGenerator
from engine.review.reviewer import ReviewPipeline
from engine.deployment.deployer import AdDeployer
from engine.tracking.tracker import PerformanceTracker
from engine.decisions.engine import DecisionEngine
from engine.regression.model import CreativeRegressionModel
from engine.notifications import SlackNotifier
from engine.models import AdStatus, DecisionVerdict


class Orchestrator:
    def __init__(
        self,
        store: Optional[Store] = None,
        notifier: Optional[SlackNotifier] = None,
    ):
        self.store = store or Store()
        self.notifier = notifier or SlackNotifier()

        # Pipeline stages
        self.parser = IntakeParser()
        self.generator = CreativeGenerator()
        self.reviewer = ReviewPipeline(self.store)
        self.deployer = AdDeployer(self.store)
        self.tracker = PerformanceTracker(self.store)
        self.decisions = DecisionEngine(self.store)
        self.regression = CreativeRegressionModel(self.store)

    # ------------------------------------------------------------------
    # On-demand: Idea → Variants
    # ------------------------------------------------------------------

    def submit_idea(self, raw_text: str, source: str = "manual") -> dict:
        """Full pipeline: parse idea → generate variants → notify."""
        brief = self.parser.parse(raw_text, source)
        self.store.save_brief(brief)

        variants = self.generator.generate(brief)
        for v in variants:
            self.store.save_variant(v)

        self.notifier.notify_variants_generated(brief.id, variants)

        return {
            "brief_id": brief.id,
            "variants_generated": len(variants),
            "brief": brief.model_dump(),
        }

    # ------------------------------------------------------------------
    # On-demand: Deploy approved variants
    # ------------------------------------------------------------------

    def deploy_approved(self, campaign_id: str, adset_or_adgroup_id: str) -> list:
        """Deploy all approved variants to platforms."""
        approved = self.store.get_variants_by_status(AdStatus.APPROVED)
        if not approved:
            print("No approved variants to deploy.")
            return []

        deployed = []
        for variant in approved:
            try:
                result = self.deployer.deploy_variant(variant, campaign_id, adset_or_adgroup_id)
                deployed.append(result)
            except Exception as e:
                print(f"Failed to deploy {variant.id}: {e}")

        if deployed:
            platform = deployed[0].taxonomy.platform.value
            self.notifier.notify_deployment(deployed, platform)

        return deployed

    # ------------------------------------------------------------------
    # Scheduled: Daily cycle
    # ------------------------------------------------------------------

    def run_daily_cycle(self, report_date: Optional[date] = None) -> dict:
        """
        Full daily cycle. Call this from a scheduler (cron, Airflow, etc.).

        1. Pull performance data
        2. Make scale/kill/wait decisions
        3. Execute kill decisions automatically
        4. Run regression
        5. Send Slack digest
        """
        if report_date is None:
            report_date = date.today()

        results = {"date": report_date.isoformat()}

        # 1. Pull performance
        print(f"[{report_date}] Pulling performance data...")
        try:
            snapshots = self.tracker.pull_daily(report_date)
            results["snapshots_pulled"] = len(snapshots)
        except Exception as e:
            print(f"Performance pull failed: {e}")
            results["snapshots_pulled"] = 0

        # 2. Make decisions
        print(f"[{report_date}] Running decision engine...")
        decisions = self.decisions.run_daily(report_date)
        results["decisions"] = {
            "scale": len([d for d in decisions if d.verdict == DecisionVerdict.SCALE]),
            "kill": len([d for d in decisions if d.verdict == DecisionVerdict.KILL]),
            "wait": len([d for d in decisions if d.verdict == DecisionVerdict.WAIT]),
        }

        # 3. Auto-execute kills (scales require manual confirmation)
        kills = [d for d in decisions if d.verdict == DecisionVerdict.KILL]
        for decision in kills:
            try:
                variant = self.store.get_variant(decision.ad_variant_id)
                self.deployer.kill_variant(variant)
                decision.executed = True
                self.store.save_decision(decision)
            except Exception as e:
                print(f"Failed to kill {decision.ad_variant_id}: {e}")

        results["auto_killed"] = len([d for d in kills if d.executed])

        # 4. Run regression (only if enough data)
        print(f"[{report_date}] Running regression model...")
        try:
            reg_result = self.regression.run()
            if reg_result:
                self.store.save_regression(reg_result)
                self.notifier.notify_regression_update(reg_result)
                results["regression"] = {
                    "r_squared": reg_result.r_squared,
                    "observations": reg_result.n_observations,
                }
            else:
                results["regression"] = {"status": "insufficient_data"}
        except Exception as e:
            print(f"Regression failed: {e}")
            results["regression"] = {"status": "error", "message": str(e)}

        # 5. Send Slack digest
        if decisions:
            self.notifier.notify_daily_decisions(decisions)

        print(f"[{report_date}] Daily cycle complete.")
        return results


# CLI entry point
if __name__ == "__main__":
    import sys

    orchestrator = Orchestrator()

    if len(sys.argv) > 1:
        command = sys.argv[1]

        if command == "daily":
            result = orchestrator.run_daily_cycle()
            print(result)

        elif command == "idea":
            if len(sys.argv) < 3:
                print("Usage: python -m engine.orchestrator idea 'your idea here'")
                sys.exit(1)
            idea = " ".join(sys.argv[2:])
            result = orchestrator.submit_idea(idea)
            print(f"Brief: {result['brief_id']}")
            print(f"Variants generated: {result['variants_generated']}")

        elif command == "review":
            pending = orchestrator.reviewer.get_pending_review()
            print(f"{len(pending)} variants pending review")
            for v in pending:
                print(f"  {v.id[:8]} | {v.headline[:60]}")

        elif command == "regression":
            playbook = orchestrator.regression.get_creative_playbook()
            print(playbook)

        else:
            print(f"Unknown command: {command}")
            print("Commands: daily, idea, review, regression")
    else:
        print("JotPsych Ads Engine Orchestrator")
        print("Commands: daily, idea '<text>', review, regression")
