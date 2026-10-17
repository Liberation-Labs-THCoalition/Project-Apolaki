# Project Apolaki

**A prosocial marketing agent. It drafts in your house voice, checks its own work, and a person approves
everything before it goes out.**

Apolaki writes and revises public-facing copy for organizations that do good work: announcements, posts, grant
narratives, web pages. It is built to describe that work accurately, in the organization's own voice, without the
dark patterns marketing usually leans on. Named after the Filipino sun god.

## What it does today

The working agent (`apolaki.agent`) is the system Liberation Labs runs for its own copy:

- **Drafts in your voice.** Your brand file (identity, voice, reviewers) and every house rule in memory go into the
  model's instructions in full, every time. Relevant memory is retrieved per request and labelled as reference
  material.
- **A style gate on every draft.** A mechanical linter checks for the tells of machine-written copy (em dashes,
  curly quotes, bold, heading case, emoji markers, negative parallelism, banned vocabulary). A failing draft gets one
  revision pass, and both lint results travel with it.
- **Two review tools you run on a finished piece:** a judgement pass on content patterns, claims discipline and
  register (`style_pass`), and a hostile fact checker that pulls out every statistic and citation and marks the ones
  with no source as suspect (`fact_checker`).
- **Two ethics checks, and the stricter one wins.**
  - *Blocked:* false urgency, guilt manipulation, astroturfing (pretending to be a customer or supporter), spam,
    deceptive or inflated metrics.
  - *Sent for human review:* large financial asks, instructions to publish or post on the organization's behalf,
    sensitive political topics, sharing data with outside parties.
- **An approval queue.** Nothing is published by Apolaki. Every draft lands in the queue with its ethics verdict
  and lint results, and a person approves it. An optional Discord hook posts each item to a review channel.
- **An MCP server** exposes the memory store to other tools.

## What it is designed to grow into

Ten skill domains are laid out as skill chips (web design, social media, content creation, lead generation,
partnerships, media relations, fundraising, brand strategy, analytics, community), with a fast classifier and an
expected-free-energy router that picks the chip for a request. The router and classifier work; the chips are
scaffolds today, and they are being filled from what the working agent already does (grant drafting first). Wiring
the full Kintsugi engine in is the next step.

## Set it up

You need Python 3.11+, [Ollama](https://ollama.com) and a capable local model. We run `qwen3.8:27b-mxfp8`.

```bash
pip install -e ".[agent,dev]"
python -m pytest tests/                       # 68 tests; none needs a model

mkdir -p ~/.apolaki
cp brand.example.json ~/.apolaki/brand.json   # then make it yours: name, voice, reviewers
python -m apolaki.agent.agent                 # the interactive agent
python -m apolaki.agent.style_pass draft.md   # the judgement pass on a finished piece
python -m apolaki.agent.fact_checker draft.md # the fact checker
```

Configuration comes from the environment:

| Variable | What it sets | Default |
|---|---|---|
| `APOLAKI_HOME` | where the approval queue, memory database, wiki and drafts live | `~/.apolaki` |
| `APOLAKI_BRAND_FILE` | your brand file | `$APOLAKI_HOME/brand.json` |
| `OLLAMA_BASE` | the model server | `http://localhost:11434` |
| `APOLAKI_MODEL` | the chat model | `qwen3.8:27b-mxfp8` |
| `APOLAKI_DISCORD_CHANNEL` | the review channel for the Discord hook (`DISCORD_TOKEN` too) | off |

Without a brand file, Apolaki uses a plain, generic voice and names no one.

## Architecture

```
brief -> ethics check (both checks; a blocked brief never reaches the model)
      -> model, with the brand, the house rules and retrieved memory
      -> style lint -> one revision if it fails
      -> ethics check on the draft
      -> approval queue -> a person approves -> published by a person
```

## License

[Hippocratic License 3.0](LICENSE.md) with Liberation Labs' modules, plus the
[commercial use addendum](LICENSE-COMMERCIAL-ADDENDUM.md).

## Team

Built by Liberation Labs. The working agent's design owes a great deal to Vera's audit of it in September 2026.
