"""Bluesky (AT Protocol) minimal client - requests only, no SDK."""

from __future__ import annotations

import datetime as dt
from typing import Any

import requests

DEFAULT_PDS = "https://bsky.social"
TIMEOUT = 30


class BlueskyError(RuntimeError):
    pass


def _now_iso() -> str:
    return dt.datetime.now(dt.timezone.utc).isoformat(timespec="milliseconds").replace("+00:00", "Z")


class BlueskyClient:
    def __init__(self, handle: str, app_password: str, pds: str = DEFAULT_PDS) -> None:
        self.handle = handle
        self.app_password = app_password
        self.pds = pds.rstrip("/")
        self.session = requests.Session()
        self.did: str | None = None
        self._jwt: str | None = None

    # ---------- auth ----------

    def login(self) -> None:
        r = self.session.post(
            f"{self.pds}/xrpc/com.atproto.server.createSession",
            json={"identifier": self.handle, "password": self.app_password},
            timeout=TIMEOUT,
        )
        if r.status_code != 200:
            raise BlueskyError(f"login failed [{r.status_code}]: {r.text[:300]}")
        data = r.json()
        self.did = data["did"]
        self._jwt = data["accessJwt"]

    @property
    def _headers(self) -> dict[str, str]:
        if not self._jwt:
            raise BlueskyError("not logged in")
        return {"Authorization": f"Bearer {self._jwt}"}

    # ---------- low level ----------

    def _get(self, nsid: str, params: dict[str, Any]) -> dict[str, Any]:
        r = self.session.get(
            f"{self.pds}/xrpc/{nsid}", params=params, headers=self._headers, timeout=TIMEOUT
        )
        if r.status_code != 200:
            raise BlueskyError(f"{nsid} [{r.status_code}]: {r.text[:300]}")
        return r.json()

    def _post(self, nsid: str, body: dict[str, Any]) -> dict[str, Any]:
        r = self.session.post(
            f"{self.pds}/xrpc/{nsid}", json=body, headers=self._headers, timeout=TIMEOUT
        )
        if r.status_code != 200:
            raise BlueskyError(f"{nsid} [{r.status_code}]: {r.text[:300]}")
        return r.json()

    # ---------- api ----------

    def list_notifications(self, limit: int = 50) -> list[dict[str, Any]]:
        return self._get(
            "app.bsky.notification.listNotifications", {"limit": limit}
        ).get("notifications", [])

    def update_seen(self) -> None:
        self._post("app.bsky.notification.updateSeen", {"seenAt": _now_iso()})

    def get_post_thread(self, uri: str, depth: int = 1, parent_height: int = 80) -> dict[str, Any]:
        return self._get(
            "app.bsky.feed.getPostThread",
            {"uri": uri, "depth": depth, "parentHeight": parent_height},
        )

    def create_post(
        self,
        text: str,
        root: dict[str, str] | None = None,
        parent: dict[str, str] | None = None,
    ) -> dict[str, str]:
        record: dict[str, Any] = {
            "$type": "app.bsky.feed.post",
            "text": text,
            "createdAt": _now_iso(),
            "langs": ["ja"],
        }
        if root and parent:
            record["reply"] = {
                "root": {"uri": root["uri"], "cid": root["cid"]},
                "parent": {"uri": parent["uri"], "cid": parent["cid"]},
            }
        res = self._post(
            "com.atproto.repo.createRecord",
            {"repo": self.did, "collection": "app.bsky.feed.post", "record": record},
        )
        return {"uri": res["uri"], "cid": res["cid"]}

    def post_chain(
        self, texts: list[str], root: dict[str, str], parent: dict[str, str]
    ) -> list[dict[str, str]]:
        """Post texts as a chained self-thread under `parent`."""
        out = []
        cur_parent = parent
        for t in texts:
            ref = self.create_post(t, root=root, parent=cur_parent)
            out.append(ref)
            cur_parent = ref
        return out


# ---------- thread helpers ----------


def has_bot_replied(thread_node: dict[str, Any], bot_handle: str) -> bool:
    """Return True if the bot already has a direct reply under this post."""
    for reply in thread_node.get("replies", []):
        post = reply.get("post", {})
        if post.get("author", {}).get("handle") == bot_handle:
            return True
    return False


def flatten_ancestors(thread_node: dict[str, Any], max_posts: int = 60) -> list[dict[str, Any]]:
    """Walk from the summoning post up to the root, return chronological chain."""
    chain: list[dict[str, Any]] = []
    node: dict[str, Any] | None = thread_node
    while node is not None:
        if node.get("$type") != "app.bsky.feed.defs#threadViewPost":
            break
        post = node.get("post")
        if not post:
            break
        chain.append(
            {
                "uri": post["uri"],
                "cid": post["cid"],
                "handle": post["author"]["handle"],
                "display_name": post["author"].get("displayName") or "",
                "text": (post.get("record") or {}).get("text", ""),
                "created_at": (post.get("record") or {}).get("createdAt", ""),
            }
        )
        node = node.get("parent")
    chain.reverse()
    return chain[-max_posts:]
