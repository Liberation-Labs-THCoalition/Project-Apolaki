# The hosted demo

A small web page where a visitor types a brief and Apolaki drafts the copy, through the real engine (brief screen,
ethics check, style lint, ethics check on the draft). It uses a made-up organization, "Example Collective"
(`brand.example.json`). It exists so people can see what Apolaki does without installing a local model.

```bash
pip install -e .
ANTHROPIC_API_KEY=... python -m apolaki.demo        # http://127.0.0.1:8080
```

It needs only `requests` (already a dependency) and the key. It does not need Ollama, spaCy, or the embedding model.

## What it does not do

- **It sends and posts nothing, and queues nothing for a person.** The page says an AI wrote the draft and that a person
  would review it in real use. It also says to check every detail: a draft can add things the brief did not say.
- **It keeps nothing a visitor writes.** Each request runs alone, in a fresh temporary `APOLAKI_HOME` that is deleted
  afterwards, with an empty read-only memory (`apolaki/demo/memory.py`; `store` raises). There is no access log.
- **It does not use the key for anything but the Anthropic request.** The key is read from `ANTHROPIC_API_KEY` and goes
  into one header.

## Limits (all checked before a model call)

| Setting | Default | What it bounds |
|---|---|---|
| `APOLAKI_DEMO_PER_IP_PER_HOUR` | 5 | drafts per visitor per hour |
| `APOLAKI_DEMO_MAX_DRAFTS_PER_DAY` | 150 | drafts for the whole demo, per UTC day |
| `APOLAKI_DEMO_MAX_USD_PER_DAY` | 3.00 | estimated spend per UTC day, checked against the worst case of the next draft |
| `APOLAKI_DEMO_USD_PER_MTOK_IN` / `_OUT` | 3 / 15 | the price estimate. **Assumed, and deliberately high. Set the real ones.** |
| `APOLAKI_DEMO_OFF=1`, or a file at `$APOLAKI_DEMO_STOP_FILE` | off | switch the demo off without restarting |

Briefs are 10 to 600 characters; a draft is at most 500 tokens (social), 700 (email) or 1,000 (blog post). Requests run
one at a time. A draft that fails is still charged at its worst case, since it may have been billed.

**The estimate is a guard, not the bill.** The real hard stop is the spending limit set in the Anthropic console. Set one.
The in-memory counters reset if the process restarts, so a restart loop can reset the daily cap; the console limit
is what holds then.

`HOST` (default `127.0.0.1`) and `PORT` (default 8080) set where it listens. Bind `0.0.0.0` only behind something that
terminates TLS, and then set `APOLAKI_DEMO_TRUST_PROXY=1` so the visitor's address is the last `X-Forwarded-For` entry,
not the proxy's. Do not set it when nothing sits in front: the header is then whatever a visitor sends.

## The Anthropic backend

`APOLAKI_BACKEND=anthropic` makes `chat()` call the Anthropic Messages API (default model `claude-haiku-5-5`, or
`APOLAKI_MODEL`). It covers the drafting path only: `style_pass.py` and `fact_checker.py` still call Ollama. No
temperature is sent, since some current models refuse it. `apolaki.agent.agent.USAGE` counts every billed call.
