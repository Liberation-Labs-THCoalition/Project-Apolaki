"""Apolaki → Discord approval pipeline.

Posts drafts to the admin review channel with react-to-approve workflow.
Meant to be called by the Apolaki agent after drafting content.

Usage:
    from apolaki_discord_hook import post_for_review
    post_for_review(draft_text, content_type="linkedin", queue_id="20260512_143622")
"""
import json
import os
import requests
from pathlib import Path
from datetime import datetime, timezone

DISCORD_TOKEN = os.environ.get("DISCORD_TOKEN", "")
from . import config

ADMIN_CHANNEL_ID = config.discord_channel()      # $APOLAKI_DISCORD_CHANNEL; the hook is off when unset
APPROVAL_DIR = config.home() / "approval-queue"


def post_for_review(
    draft: str,
    content_type: str = "social",
    queue_id: str = "",
    ethics_verdict: str = "pending",
    notes: str = "",
) -> dict:
    """Post a draft to the admin Discord channel for review.

    Returns dict with message_id and status.
    """
    if not DISCORD_TOKEN:
        return {"error": "DISCORD_TOKEN not set"}
    if not ADMIN_CHANNEL_ID:
        return {"error": "APOLAKI_DISCORD_CHANNEL not set: the hook is off"}

    if not queue_id:
        queue_id = datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%S")

    header = (
        f"**[Apolaki Draft — Awaiting Review]**\n"
        f"**Type:** {content_type.title()} | **Queue:** {queue_id} | "
        f"**Ethics:** {ethics_verdict}\n"
        f"React ✅ to approve · ❌ to reject · 💬 for changes"
    )

    # If draft fits in 2000 chars with header, send inline
    if len(header) + len(draft) + 10 < 2000:
        payload = {"content": f"{header}\n\n{draft}"}
        resp = requests.post(
            f"https://discord.com/api/v10/channels/{ADMIN_CHANNEL_ID}/messages",
            headers={"Authorization": f"Bot {DISCORD_TOKEN}"},
            json=payload,
        )
    else:
        # Send as file attachment
        draft_file = APPROVAL_DIR / f"{queue_id}_{content_type}.md"
        draft_file.parent.mkdir(parents=True, exist_ok=True)
        draft_file.write_text(draft)

        resp = requests.post(
            f"https://discord.com/api/v10/channels/{ADMIN_CHANNEL_ID}/messages",
            headers={"Authorization": f"Bot {DISCORD_TOKEN}"},
            data={"content": header},
            files={"files[0]": (draft_file.name, open(draft_file, "rb"), "text/markdown")},
        )

    if resp.status_code == 200:
        msg = resp.json()
        # Add approval reactions
        msg_id = msg["id"]
        for emoji in ["✅", "❌", "💬"]:
            requests.put(
                f"https://discord.com/api/v10/channels/{ADMIN_CHANNEL_ID}/messages/{msg_id}/reactions/{emoji}/@me",
                headers={"Authorization": f"Bot {DISCORD_TOKEN}"},
            )
        return {"message_id": msg_id, "status": "posted", "channel": ADMIN_CHANNEL_ID}
    else:
        return {"error": resp.text, "status_code": resp.status_code}


if __name__ == "__main__":
    # Test
    result = post_for_review(
        "Test draft from Apolaki pipeline. If you see this, the Discord hook is working.",
        content_type="test",
        ethics_verdict="approved",
    )
    print(json.dumps(result, indent=2))
