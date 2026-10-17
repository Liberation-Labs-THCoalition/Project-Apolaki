# Project Apolaki — Agent Context

## What Is Apolaki

Marketing, sales, and prosocial fundraising engine built on the Kintsugi framework. Gives a marketing professional AI-powered superpowers for the Cause — web design, social media management, lead generation, media relations, fundraising campaigns, and brand strategy.

Named after the Filipino sun god. Designed to light things up.

**Not a replacement for the marketer. A force multiplier.** The human sets strategy and voice. Apolaki handles the volume, research, scheduling, and grunt work.

## Architecture

Built on Kintsugi engine principles:
- **EFE (Expected Free Energy)**: Scores candidate actions with prosocial-marketing weights
- **FastClassifier**: Two-stage routing — fast-path obvious tasks, escalate sensitive ones
- **Ethics layer**: Prosocial marketing only. No dark patterns. No manipulation. Cameras UP.

```
User request → FastClassifier (deny/escalate/allow)
  → Skill router (10 domains)
    → Skill chip executes with domain-specific EFE weights
      → Output with ethics guardrails
```

## 10 Skill Domains

| Domain | What it does | EFE Profile |
|--------|-------------|-------------|
| **web_design** | Sites, landing pages, CMS, SEO | risk=0.3, ambiguity=0.3, epistemic=0.4 |
| **social_media** | Content calendar, scheduling, platform strategy | risk=0.3, ambiguity=0.3, epistemic=0.4 |
| **content_creation** | Copywriting, blogs, newsletters, press releases | risk=0.4, ambiguity=0.2, epistemic=0.4 |
| **lead_generation** | Prospect research, outreach, CRM, pipeline | risk=0.4, ambiguity=0.3, epistemic=0.3 |
| **partnerships** | Strategic partner ID, outreach, relationship mgmt | risk=0.3, ambiguity=0.4, epistemic=0.3 |
| **media_relations** | Press contacts, media kits, pitches, story placement | risk=0.5, ambiguity=0.2, epistemic=0.3 |
| **fundraising** | Grants, donor cultivation, campaigns, events | risk=0.4, ambiguity=0.3, epistemic=0.3 |
| **brand_strategy** | Positioning, messaging, visual identity, research | risk=0.3, ambiguity=0.3, epistemic=0.4 |
| **analytics** | Campaign metrics, ROI, A/B testing, reporting | risk=0.2, ambiguity=0.3, epistemic=0.5 |
| **community_engagement** | Community management, advocacy mobilization | risk=0.3, ambiguity=0.3, epistemic=0.4 |

## The working agent (start here)

`apolaki/agent/` is the system that runs (ported 2026-10-16 from the Starship deployment): the agent loop with house
rules from the brand file and memory, the style gate, both ethics checks (stricter wins), the approval queue, the
fact checker, the style pass, the MCP server, the Discord hook. Configuration is in `apolaki/agent/config.py`; a
deployment's identity and voice live in its brand file (`brand.example.json`), never in code. A deployment's private
brand, guide and data stay out of this repo.

`apolaki/engine.py` is the Kintsugi engine around it (Rivet's pattern): each request a BDI intention run by a skill
chip from a Kintsugi SkillRegistry; reviewers' decisions (agent.review_item) become persistent beliefs that reach the
model as a <reviewer_feedback> block beside memory, never in the brief. Kintsugi comes through `apolaki/kintsugi_core.py`
(KINTSUGI_PATH checkout, then installed, then `apolaki/kintsugi_vendor/`). Only content_creation runs through the
engine so far; the other chips are scaffolds.

## Key Directories

```
apolaki/
├── agent/              — THE WORKING AGENT (see above)
├── engine.py           — the Kintsugi engine around it (BDI, skill registry, reviewer feedback)
├── kintsugi_core.py    — Kintsugi loader; kintsugi_vendor/ holds the vendored modules
├── cognition/          — EFE calculator, fast classifier, routing
├── skills/             — 10 domain skill chips
│   ├── web_design/
│   ├── social_media/
│   ├── content_creation/
│   ├── lead_generation/
│   ├── partnerships/
│   ├── media_relations/
│   ├── fundraising/
│   ├── brand_strategy/
│   ├── analytics/
│   └── community_engagement/
├── security/           — ethics guardrails, deny patterns
├── config/             — settings, values
└── adapters/           — Discord, Slack, web integrations
tests/                  — test suite
docs/                   — playbooks, guides
```

## Ethics (Non-Negotiable)

Prosocial marketing only. The power serves the Cause, not the other way around.

**ALLOWED:**
- Amplifying genuine impact stories
- Connecting supporters with causes they care about
- Making fundraising accessible and transparent
- Building authentic community engagement
- Honest promotion of prosocial products/services

**DENIED:**
- Dark patterns (urgency manipulation, guilt tripping, fear-based appeals)
- Astroturfing (fake grassroots, fake reviews, sock puppets)
- Data harvesting beyond stated purpose
- Targeting vulnerable populations for extraction
- Misrepresenting organizational impact
- Spam or unsolicited mass outreach

**ESCALATED (human review required):**
- Donor contact with financial ask > $1000
- Press outreach on sensitive topics
- Any communication on behalf of the organization (vs drafting for review)
- Social media posts on political/controversial topics
- Data sharing with external partners

## Team

- **CC** — Architect, built on Kintsugi engine
- **Thomas** — Strategy, Coalition lead
- **Marketing lead** — Primary user, sets voice and strategy
- **Apolaki** — The engine itself

## Running Tests

```bash
cd Project-Apolaki && python -m pytest tests/ -x
```
