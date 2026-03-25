"""
Intake Parser — converts free-form idea dumps into structured creative briefs.

Input: raw text (slack message, voice transcript, notes, competitor URL)
Output: CreativeBrief with all fields populated

Uses LLM to extract structure. The prompt is the secret sauce here —
it needs to preserve the original creative intent while enforcing
the taxonomy vocabulary so downstream tagging stays clean.
"""

from __future__ import annotations

import json
from typing import Optional

from anthropic import Anthropic

from engine.models import CreativeBrief, AdFormat, Platform


SYSTEM_PROMPT = """You are a creative strategist for JotPsych, a clinical documentation AI tool for behavioral health clinicians and small/medium clinic owners.

Your job: take a raw, messy creative idea and structure it into a precise creative brief.

JotPsych's core value props:
- Saves 1-2 hours of documentation time per day
- AI-powered note generation from session recordings
- HIPAA-compliant, built for behavioral health specifically
- Reduces burnout, lets clinicians focus on patients
- Works with existing EHR workflows

Target audiences:
1. Individual BH clinicians (therapists, psychologists, counselors, social workers)
2. SMB clinic owners / administrators (practice managers, clinical directors)

Output a JSON object with these fields:
{
    "target_audience": "bh_clinicians" or "smb_clinic_owners",
    "value_proposition": "the core promise in one sentence",
    "pain_point": "the specific problem being addressed",
    "desired_action": "what the viewer should do after seeing this",
    "tone_direction": "descriptive tone guidance",
    "visual_direction": "what the ad should look like",
    "key_phrases": ["specific language to use"],
    "num_variants": 6,
    "formats_requested": ["single_image", "video"],
    "platforms": ["meta"]
}

Be specific. Don't be generic. If the input is vague, make sharp creative choices and note them.
"""


class IntakeParser:
    def __init__(self, client: Optional[Anthropic] = None):
        self.client = client or Anthropic()

    def parse(self, raw_input: str, source: str = "manual") -> CreativeBrief:
        """Parse a free-form idea dump into a structured creative brief."""

        response = self.client.messages.create(
            model="claude-sonnet-4-20250514",
            max_tokens=2000,
            system=SYSTEM_PROMPT,
            messages=[
                {
                    "role": "user",
                    "content": f"Turn this into a creative brief:\n\n{raw_input}",
                }
            ],
        )

        # Extract JSON from response
        text = response.content[0].text
        # Handle potential markdown code blocks
        if "```json" in text:
            text = text.split("```json")[1].split("```")[0]
        elif "```" in text:
            text = text.split("```")[1].split("```")[0]

        data = json.loads(text.strip())

        return CreativeBrief(
            raw_input=raw_input,
            source=source,
            target_audience=data["target_audience"],
            value_proposition=data["value_proposition"],
            pain_point=data["pain_point"],
            desired_action=data["desired_action"],
            tone_direction=data["tone_direction"],
            visual_direction=data["visual_direction"],
            key_phrases=data.get("key_phrases", []),
            num_variants=data.get("num_variants", 6),
            formats_requested=[AdFormat(f) for f in data.get("formats_requested", ["single_image"])],
            platforms=[Platform(p) for p in data.get("platforms", ["meta", "google"])],
        )

    def parse_batch(self, ideas: list[str], source: str = "manual") -> list[CreativeBrief]:
        """Parse multiple idea dumps into briefs."""
        return [self.parse(idea, source) for idea in ideas]
