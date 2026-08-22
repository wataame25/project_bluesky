"""Render judgment JSON into Bluesky-safe post text (<=300 chars each)."""

from __future__ import annotations

from typing import Any

from .judge import AXES

LIMIT = 290  # margin below Bluesky's 300-grapheme cap
DISCLAIMER = "※論証の構造のみを機械採点したものです。主張内容の正誤や人格の評価ではありません。"


def _clip(s: str, n: int) -> str:
    s = s.strip()
    return s if len(s) <= n else s[: n - 1] + "…"


def _score_lines(participants: list[dict[str, Any]], detailed: bool) -> list[str]:
    lines = []
    for p in participants:
        lines.append(f"@{p['handle'].lstrip('@')} {p['total']}点")
        if detailed:
            sc = p.get("scores", {})
            parts = [f"{ja}{sc.get(k, 0)}" for k, ja in AXES]
            lines.append("　" + "/".join(parts))
    return lines


def format_verdict(result: dict[str, Any]) -> list[str]:
    """Return 1-2 post texts."""
    parts = sorted(result.get("participants", []), key=lambda p: -p.get("total", 0))[:4]
    topic = _clip(result.get("topic", ""), 30)
    winner = result.get("winner", "引き分け")
    winner_label = winner if winner in ("引き分け", "") else f"@{winner.lstrip('@')}"

    header = f"⚖️ レスバ判定\n論点: {topic}\n"
    footer = f"\n🏆 勝者: {winner_label}"

    for detailed in (True, False):
        body = "\n".join(_score_lines(parts, detailed))
        text = header + body + footer
        if len(text) <= LIMIT:
            break
    else:  # pragma: no cover
        text = _clip(text, LIMIT)

    return [_clip(text, LIMIT)]


def format_skip(reason_text: str) -> list[str]:
    return [_clip(f"⚖️ 判定できませんでした。\n{reason_text}", LIMIT)]
