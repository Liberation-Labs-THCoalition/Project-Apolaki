"""Where a deployment keeps its state, and who it is: one place, read from the environment.

    APOLAKI_HOME            the deployment's folder: approval-queue/, db/, wiki/, grant-drafts/ (default ~/.apolaki)
    APOLAKI_BRAND_FILE      the brand file (default $APOLAKI_HOME/brand.json; see brand.example.json)
    OLLAMA_BASE             the model server (default http://localhost:11434)
    APOLAKI_MODEL           the chat model (default qwen3.8:27b-mxfp8)
    APOLAKI_DISCORD_CHANNEL the admin channel the Discord hook posts approval items to (no default: off when unset)

Nothing is created at import. A folder is made the first time something is written into it.
"""
from __future__ import annotations

import json
import os
from pathlib import Path


def home() -> Path:
    return Path(os.environ.get("APOLAKI_HOME", str(Path.home() / ".apolaki"))).expanduser()


def folder(name: str) -> Path:
    d = home() / name
    d.mkdir(parents=True, exist_ok=True)
    return d


def ollama_base() -> str:
    return os.environ.get("OLLAMA_BASE", "http://localhost:11434")


def model() -> str:
    return os.environ.get("APOLAKI_MODEL", "qwen3.8:27b-mxfp8")


def discord_channel() -> str:
    return os.environ.get("APOLAKI_DISCORD_CHANNEL", "")


DEFAULT_BRAND = {"name": "the organization", "reviewers": "a person on the team"}
# A deployment fills these from its brand file; the defaults keep the prompt honest and generic.
BRAND_TEXT_DEFAULTS = {
    "description": ("You draft and revise public-facing copy for this organization. Your job is to describe its work "
                    "accurately, in its own voice, so the people it is for can find it."),
    "voice": ("## Voice, in short\n\n- Specific over vague: name the thing, the measurement, the person it helps.\n"
              "- Any claim about capability, price or performance carries the caveat that makes it true.\n"
              "- When we do not know something, say so."),
}


def brand_path() -> Path:
    """Where the brand file is looked for. brand() falls back to generic defaults when it's missing, so a caller that
    must not run on the defaults (grant_hunt) checks this path exists first."""
    return Path(os.environ.get("APOLAKI_BRAND_FILE", str(home() / "brand.json"))).expanduser()


def brand() -> dict:
    path = brand_path()
    try:
        data = json.loads(path.read_text())
    except (OSError, ValueError):
        return {**DEFAULT_BRAND, **BRAND_TEXT_DEFAULTS}
    return {**DEFAULT_BRAND, **BRAND_TEXT_DEFAULTS,
            **{k: v for k, v in data.items() if isinstance(v, str) and v.strip()}}
