"""Entry point: poll mentions -> judge thread -> reply."""

from __future__ import annotations

import datetime as dt
import json
import os
import pathlib
import sys
import traceback

from .bluesky import BlueskyClient, flatten_ancestors, has_bot_replied
from .formatter import format_skip, format_verdict
from .judge import build_transcript, judge

STATE_PATH = pathlib.Path("state/seen.json")
STATE_KEEP = 500

MAX_AGE_MIN = int(os.getenv("MAX_AGE_MINUTES", "90"))
MAX_PER_RUN = int(os.getenv("MAX_PER_RUN", "3"))
MIN_POSTS = int(os.getenv("MIN_POSTS", "4"))
DRY_RUN = os.getenv("DRY_RUN", "").lower() in ("1", "true", "yes")


def load_state() -> dict:
    if STATE_PATH.exists():
        try:
            return json.loads(STATE_PATH.read_text(encoding="utf-8"))
        except json.JSONDecodeError:
            pass
    return {"processed": []}


def save_state(state: dict) -> None:
    state["processed"] = state["processed"][-STATE_KEEP:]
    state["updated_at"] = dt.datetime.now(dt.timezone.utc).isoformat()
    STATE_PATH.parent.mkdir(parents=True, exist_ok=True)
    STATE_PATH.write_text(json.dumps(state, ensure_ascii=False, indent=2), encoding="utf-8")


def _is_fresh(indexed_at: str) -> bool:
    try:
        ts = dt.datetime.fromisoformat(indexed_at.replace("Z", "+00:00"))
    except ValueError:
        return False
    age = dt.datetime.now(dt.timezone.utc) - ts
    return age <= dt.timedelta(minutes=MAX_AGE_MIN)


def handle_mention(bs: BlueskyClient, notif: dict, gemini_key: str, model: str) -> None:
    uri = notif["uri"]
    summoner = notif["author"]["handle"]
    print(f"  summoned by @{summoner}")

    thread = bs.get_post_thread(uri).get("thread", {})
    if has_bot_replied(thread, bs.handle):
        print("  already replied, skipping")
        return
    chain = flatten_ancestors(thread)
    if not chain:
        print("  ! could not read thread")
        return

    root = {"uri": chain[0]["uri"], "cid": chain[0]["cid"]}
    parent = {"uri": chain[-1]["uri"], "cid": chain[-1]["cid"]}

    debate = [p for p in chain if p["handle"] != bs.handle]
    speakers = {p["handle"] for p in debate}

    if len(debate) < MIN_POSTS or len(speakers) < 2:
        texts = format_skip("2名以上・4投稿以上のやりとりが必要です。")
    else:
        transcript = build_transcript(chain, bs.handle)
        result = judge(transcript, gemini_key, model)
        print(f"  verdict: {json.dumps(result, ensure_ascii=False)[:200]}")
        if not result.get("valid"):
            texts = format_skip("論争として成立していないと判断しました。")
        else:
            texts = format_verdict(result)

    for t in texts:
        print(f"  --- reply ({len(t)} chars) ---\n{t}")
    if DRY_RUN:
        print("  [dry-run] not posting")
        return
    bs.post_chain(texts, root=root, parent=parent)
    print("  posted")


def main() -> int:
    handle = os.environ["BSKY_HANDLE"]
    password = os.environ["BSKY_APP_PASSWORD"]
    gemini_key = os.environ["GEMINI_API_KEY"]
    model = os.getenv("GEMINI_MODEL", "gemini-2.5-flash")

    bs = BlueskyClient(handle, password, os.getenv("BSKY_PDS", "https://bsky.social"))
    bs.login()
    print(f"logged in as {handle} ({bs.did})")

    state = load_state()
    processed = set(state["processed"])

    notifs = bs.list_notifications(limit=50)
    targets = [
        n
        for n in notifs
        if n.get("reason") == "mention"
        and n["uri"] not in processed
        and n["author"]["handle"] != handle
        and _is_fresh(n.get("indexedAt", ""))
    ]
    targets.sort(key=lambda n: n.get("indexedAt", ""))
    targets = targets[:MAX_PER_RUN]
    print(f"{len(notifs)} notifications, {len(targets)} to process")

    for n in targets:
        print(f"- {n['uri']}")
        try:
            handle_mention(bs, n, gemini_key, model)
        except Exception:
            traceback.print_exc()
        finally:
            # mark processed regardless, so one bad thread never loops forever
            state["processed"].append(n["uri"])

    save_state(state)
    if not DRY_RUN:
        try:
            bs.update_seen()
        except Exception as e:
            print(f"updateSeen failed (non-fatal): {e}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
