# Ads Engine — JotPsych Creative Performance Harness

## What This Is
A quant-style ad operations system: idea → creative variants → deploy → measure → decompose → learn → repeat.
Runs ads for JotPsych targeting BH clinicians and SMB clinic decision-makers on Meta and Google.

## Core Loop
1. **Intake** — Free-form ideas (text, voice, swipe files) → AI structures into creative briefs
2. **Generation** — Briefs → static + video ad variants (method TBD by intern)
3. **Review** — Gallery UI: Nate + Jackson approve/reject/annotate variants
4. **Deploy** — Approved ads → Meta Ads API + Google Ads API (full programmatic)
5. **Track** — Pull performance data daily via platform APIs
6. **Decide** — Daily scale / kill / wait recommendations (quant trading style)
7. **Regress** — Auto-decompose creative elements into MECE taxonomy → linear regression → learn what works

## Key Constraints
- Primary conversion event: first note completion
- Budget: $15-20k/mo, scaling
- No AI slop — creative must look and read human-quality
- Meta + Google handle audience selection; we optimize creative only
- All creative elements auto-tagged into MECE taxonomy (no manual tagging)
- Regression coefficients must be orthogonal — watch for covariance

## Architecture
- Python backend (engine/) — all pipeline logic
- Web dashboard (dashboard/) — gallery review, performance views, regression insights
- Slack integration — auto-updates on deploys, kills, scale-ups, daily digest
- Data stored locally initially (data/) — migrate to proper warehouse when volume demands it

## Current State of Ads (as of Mar 22, 2026)

### Team
Adam Harrison — marketing lead, full account history across Meta and Google.
Matt — Meta contractor, knows the account structure.
Jenna — Google Ads rep, recurring call.
Chris Hume — sales, SDR routing for lead form leads.

### Performance History (Feb–Mar 2026, discovery survey attribution)
| Week | Spend | First Notes | CpFN |
|------|-------|-------------|------|
| Jan 25–31 | $4,158 | 22 | $189 |
| Feb 1–7 | $4,243 | 16 | $265 |
| Feb 8–14 | $4,503 | 23 | $196 |
| Feb 15–21 | $4,247 | 12 | $354 |
| Feb 22–Mar 1 | $3,863 | 20 | $193 |
| Mar 1–7 | $3,434 | 18 | $191 |

CpFN swings wildly week-to-week. No consistent trend — feast and famine pattern.

### Channel Status
- **Meta** — Primary channel. ~$2.5–3K/wk. Farm vs. scale campaign structure in place.
  Lead form experiment launched Mar 11 showing early promise: 17–37 leads at ~$63/lead in 3 days at $200/day.
- **Google** — Non-brand paused, reallocated to Meta. Branded-only at ~$400/wk.
  Google tag is broken — showing 2 conversions in-platform vs. 15 in Metabase UTMs. Needs engineering fix.
- **LinkedIn** — On backburner.

### Known Issues
1. **2FA broke conversion tracking.** SMS verification for signups disrupted both Google tag firing and Meta optimization signals. Timing of perf degradation matches exactly.
2. **Attribution is a mess.** Meta and Google double-count conversions (view-through overlap). Discovery survey is treated as canonical but it's self-reported. No deduplication model exists.
3. **Creative assets are scattered.** Across Drive, inbox, Meta, Figma — no single source of truth. This engine is meant to fix that.
4. **Existing analysis tool.** A contractor uses an MCP-based tool (GoMarble) for automated creative analysis. Decide whether to replace with this engine or integrate.

### Creative Learnings (from weekly tests)
- **What works:** UGC-style video, motion/energy, get to product fast, visual proof of product in action, highlighting specific tasks, audit-risk angle showed early promise
- **What doesn't:** Static/boring, slow to show product, generic scripts
- **Best CpFN seen:** $64/note (winner promoted from farm to scale, Feb 1–7 week)
- **Kill threshold in practice:** ~$300+/note after sufficient spend

## For the Intern
See brief.html for the full project brief, open questions, and ownership areas.
The scaffolding is built. Your job is to make it real.
