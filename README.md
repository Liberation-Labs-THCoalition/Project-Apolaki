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
  with no source as suspect (`fact_checker`). The checker reads the whole piece, in parts when it is long, and says
  plainly which characters went unchecked when a part fails.
- **Two ethics checks, and the stricter one wins.**
  - *Blocked:* false urgency, guilt manipulation, confirmshaming (a decline option worded to shame), astroturfing
    (pretending to be a customer or supporter, or writing or inventing reviews and testimonials in their name), spam
    (the law, CAN-SPAM, and the spam folder can be named), deceptive or inflated metrics, review manipulation
    (five-star asks, rewards for good reviews, asking only the happy customers, prewritten reviews), consent bypass
    (pre-checked boxes, adding or emailing people without asking), undisclosed ads, pricing tricks (a former price
    never charged, decoy tiers, door-in-the-face), and inferring people's health, faith, politics, sexuality,
    ethnicity or finances.
  - *Sent for human review:* amounts of $1,000 or more however they are written ("$5,000", "$1.2M", "50,000 USD"),
    other financial asks, instructions to publish or post on the organization's behalf, sensitive political topics,
    sharing data with outside parties, claimed scarcity and lapse deadlines, rewards for reviews, targeting by a
    sensitive trait or condition ("people with depression"), and tracking people for ads (custom or lookalike
    audiences made from a list, retargeting, tracking pixels, fingerprinting).
  - A count of what is left ("Only three spots left!") passes only when the brief states the same count, and then goes
    to a person; a brief, or a person's chat message, is a fact for its own check, so the count it states is the
    person's to claim, and only a count it contradicts ("Say only 10 left; we have hundreds") is blocked; time left
    ("Only 2 days left to apply") goes to a person and is never blocked; a "last chance" or "limited time" in a sentence
    that names a real date or time ("through March 15", "Friday at 5pm") goes to a person instead of being blocked; an
    instruction to say something it calls untrue ("even though it doesn't") is blocked; moderating spam ("Delete the
    spam comments") is not sending it.
  - They are patterns, and patterns have limits: they see only the negations they were written for ("no fake
    testimonials" and "We never spam you" pass), and they can't catch a paraphrase they weren't written for. The known
    misses are kept as expected failures in `tests/test_red_team_probes.py`, and listed in plain language, with what to
    watch for, in [docs/KNOWN_LIMITS.md](docs/KNOWN_LIMITS.md). The person who approves the draft is the check that
    holds.
  - Both read normalised text: curly quotes are straightened, invisible characters removed and look-alike letters
    (fullwidth, Cyrillic, accented) folded first, so "Don’t miss out" typed on a phone is read as "Don't miss out".
  - They run on every brief before the model is called, on every draft, and in the interactive chat on each message
    and on each reply. In chat a message is also read together with the person's last six messages and the model's
    replies between them, since the model reads them together and a request split across turns passes a check of
    each half, sometimes joined by the model's own question ("Should I include the #ad disclosure?" "No, leave it
    out."). A blocked message never reaches the model. A blocked reply gets one revision, checked again; if it is
    still blocked, the chat answers with a refusal and the reason, never the blocked text. The scaffold's own refusals
    are marked where they are made and left out of that reading; a blocked draft is kept for a person in the queue,
    not in what the model reads next.
- **Outside text is screened before anything uses it.** Layer 1 of
  [Garuda](https://github.com/Liberation-Labs-THCoalition/Project-Garuda), our injection screen, is vendored in
  `apolaki/security/garuda` (its notice kept beside it). Every brief is screened before its ethics check and before
  the model is called (the grant hunt puts listing and funder-page text in its briefs), and so is every memory the MCP
  server is asked to store; `apolaki.grants.intake` has the screens a grant job runs on listings, funder pages and the
  calls a model extracts from them, and `apolaki.intake.screen_discord` is there for Discord input. A hit is
  quarantined in `$APOLAKI_HOME/quarantine/` for a person and logged, and goes nowhere else;
  `python -m apolaki.intake list|show|release` is how a person reads it and, if it is harmless, lets that exact text
  through. HTML (Grants.gov synopses come as HTML) is screened as a reader sees it, tags out and entities decoded, as
  well as as it came. Layer 1 is a list of known phrasings: a paraphrase gets past it, and research that only discusses
  injection is held like an attack until a person releases it. Garuda's second layer, a model judge, isn't vendored.
- **An approval queue.** Nothing is published by Apolaki. Every draft lands in the queue with its ethics verdict,
  its lint results and the sha256 of its text, and a person approves it. An approval is of that exact text: if the
  text has changed since it was queued, the approval is refused until the item is re-queued as a new item with its
  own check, and an item the ethics check blocked is approved only with an override note saying why and who, which
  is recorded on the item. The hash catches edits by anything that doesn't also rewrite it; it is not a signature.
  An optional Discord hook posts each item to a review channel.
- **It can learn from its reviewers, when that is switched on.** A reviewer's decision (approved, rejected, changes
  requested, with a note) is recorded on the queue item. Learning is off by default; with it on (`--learn`), the
  Kintsugi engine keeps each decision as a belief, and the recent notes for a content type go to the model with the
  next brief of that type, as reference material beside memory. They never go into the brief itself, which is what
  the ethics check reads.
  - The engine holds learning's safeguards for every caller (`apolaki/learning.py`): a switch file whose
    `{"on": true}` a draft needs when the file is there, a pause file only a person removes, a daily cap and a total
    ceiling (5 and 25 until a person raises them) that hold a whole run back and write the pause, a log of every
    belief added, held back or removed, and a watch on each draft that fails it if Apolaki's memory or beliefs
    change while it drafts. A pause tells no one by itself: it is a file and a log line, and someone has to read
    them.
- **Runs on the Kintsugi engine.** Each request is a BDI intention carried out by a skill chip from a Kintsugi skill
  registry (Rivet's pattern). Kintsugi's BDI and skill modules are vendored, so a bare install works, and a Kintsugi
  checkout (`KINTSUGI_PATH`) takes precedence when there is one.
- **A person can be erased from its memory.** `python -m apolaki.engine erase-person "NAME" [--alias ...]` lists
  everything that names them (memories with their embeddings, entities, graph rows, wiki pages, engine beliefs) and
  every other file the name is in; with the dry run's code it removes exactly that, rebuilds what depended on it,
  compacts the database and checks that the name is gone: every text column of every table, escapes decoded and markup
  taken out, and the database files byte by byte. A trace left, or a step that failed, is reported as an incomplete
  erase. The name inside a longer word ("Quinterova") is a near-match: the dry run lists each one, and the erase refuses
  until a person rules it someone else (`--not-them`) or gives it as an alias. It certifies only Apolaki's own tables: a
  table, column, index, view or trigger its code didn't make, or one of its own whose definition says more than its
  code writes, is listed, and the erase refuses until a person says they checked each (`--checked`). So is a folder
  under its home that it can't read, since that folder could hold files that name the person. It finds people
  by the names given, and it leaves records that aren't memory (the approval queue, grant files, the quarantine) for a
  person to handle, listing each place in them that names the person.
  [docs/DATA_LIFECYCLE.md](docs/DATA_LIFECYCLE.md) says what Apolaki keeps, about whom, for how long (until a person
  removes it) and who can erase it.
- **A grant outcome log.** `python -m apolaki.grants outcome KEY applied|awarded|declined|no_reply --date D --by NAME`
  appends what happened to an application to `grant-hunt/outcomes.jsonl`, beside the grant hunt's seen-list, with
  one primary reason and its source when there is one (no reason without a source). `python -m apolaki.grants
  outcomes` summarises it in counts with n, never percentages, and says when fewer than five decided outcomes make
  it a signal to watch rather than a finding. A person fills it in; nothing reads it into triage or learning.
- **A STOP file.** `python -m apolaki.stop now "WHY" --by NAME` writes `$APOLAKI_HOME/STOP`: while it exists the
  engine starts no draft and learns nothing, and a scheduled job that checks it stops before its next step. It can't
  stop a step already running, or a job that never checks it; [docs/STOPPING.md](docs/STOPPING.md) has the whole
  procedure for the nightly grant hunt, including the scheduler route that works on any version, and
  [docs/GRANT_JOB_HOOKS.md](docs/GRANT_JOB_HOOKS.md) lists what a grant job must call to get the rest.
- **An MCP server** exposes the memory store to other tools. It can't write a house rule (a brand or guideline
  memory goes into every system prompt), and it checks the rest of what it stores against its schema. A person
  adds house rules at the interactive agent's `/rule` command.

## What it is designed to grow into

Ten skill domains are laid out as skill chips (web design, social media, content creation, lead generation,
partnerships, media relations, fundraising, brand strategy, analytics, community), with a fast classifier and an
expected-free-energy router that picks the chip for a request. Content creation runs through the engine today; the
other chips are scaffolds, and they are being filled from what the working agent already does (grant drafting next).

## Set it up

You need Python 3.11+, [Ollama](https://ollama.com) and a capable local model. We run `qwen3.8:27b-mxfp8`.

```bash
pip install -e ".[agent,dev]"
python -m spacy download en_core_web_md       # the memory store's entity graph won't open without it
python -m pytest tests/                       # none needs a model

mkdir -p ~/.apolaki
cp brand.example.json ~/.apolaki/brand.json   # then make it yours: name, voice, reviewers
python -m apolaki.engine draft "BRIEF" --type social     # one draft, through the engine
python -m apolaki.engine review QUEUE_ID rejected "NOTE"  # record a reviewer's decision
python -m apolaki.engine requeue QUEUE_ID                 # an edited item becomes a new item to review
python -m apolaki.stop now "WHY" --by NAME                # stop scheduled work at its next step (docs/STOPPING.md)
python -m apolaki.grants outcome KEY declined --date D --by NAME --reason WHY --source WHERE
python -m apolaki.grants outcomes                         # grant outcomes in counts with n
python -m apolaki.intake list                             # outside text the screen quarantined, for a person
python -m apolaki.engine erase-person "NAME"              # a dry run; then --confirm CODE --by WHO --reason WHY
                                                          # (and --not-them WORD for each near-match it lists,
                                                          # --checked THING for each table, column or index
                                                          # it didn't make, or folder it couldn't read)
python -m apolaki.agent.agent                 # the interactive agent (/rule TEXT adds a house rule)
python -m apolaki.agent.style_pass draft.md   # the judgement pass on a finished piece
python -m apolaki.agent.fact_checker draft.md # the fact checker
```

A hosted, capped demo that needs only an Anthropic key (no local model) is described in [docs/DEMO.md](docs/DEMO.md).

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
brief -> BDI intention -> content-creation chip
      -> intake screen (Garuda layer 1; a hit is quarantined for a person and never reaches the model)
      -> ethics check (both checks; a blocked brief never reaches the model)
      -> model, with the brand, the house rules, retrieved memory and recent reviewer notes
      -> style lint -> one revision if it fails
      -> ethics check on the draft
      -> approval queue -> a person decides -> the decision becomes a belief for next time
```

## License

[Hippocratic License 3.0](LICENSE.md) with Liberation Labs' modules, plus the
[commercial use addendum](LICENSE-COMMERCIAL-ADDENDUM.md).

The erase's table of Cherokee look-alike letters, and a test's copy of it, come from Unicode's `confusables.txt`.
They are © Unicode, Inc., under the Unicode License v3. [THIRD_PARTY_NOTICES.md](THIRD_PARTY_NOTICES.md) holds that
licence's full text. It also names exactly what in Apolaki is derived from Unicode data, and says where each of the
other look-alike tables came from.

## Team

Built by Liberation Labs. The working agent's design owes a great deal to Vera's audit of it in September 2026.
