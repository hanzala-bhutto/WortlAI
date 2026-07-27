"""Seed the Langfuse project from the bundled fallback prompts.

Prompt management (#7) fetches prompts from Langfuse by name + label, falling back
to the bundled copies in app/llmops/fallbacks/ when Langfuse has nothing. Until a
prompt actually exists in Langfuse, the fallback is all that runs - so editing a
prompt still means a code change. This script pushes each bundled fallback into
Langfuse once, so the two start in sync and you can then edit in the Langfuse UI
with no redeploy.

Each run creates a new *version* in Langfuse (that is how the SDK works) and moves
the given label to it. Safe to re-run; it just adds versions.

Usage (from backend/, with Langfuse keys in .env):
    python -m scripts.push_prompts                 # push every fallback, label 'staging'
    python -m scripts.push_prompts --label production
    python -m scripts.push_prompts --dry-run       # show what would be pushed
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from app.llmops.client import get_langfuse
from app.llmops.prompts import FALLBACK_DIR


def _discover(fallback_dir: Path) -> list[tuple[str, str, object]]:
    """Every bundled fallback as (name, type, prompt-payload). Chat prompts are the
    JSON message array; text prompts are the raw string."""
    found: list[tuple[str, str, object]] = []
    for path in sorted(fallback_dir.glob("*.chat.json")):
        name = path.name[: -len(".chat.json")]
        found.append((name, "chat", json.loads(path.read_text(encoding="utf-8"))))
    for path in sorted(fallback_dir.glob("*.txt")):
        found.append((path.stem, "text", path.read_text(encoding="utf-8")))
    return found


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Push bundled prompts to Langfuse.")
    parser.add_argument(
        "--label", default="staging",
        help="Label to attach to the pushed version (default: staging).",
    )
    parser.add_argument(
        "--dry-run", action="store_true",
        help="List what would be pushed without contacting Langfuse.",
    )
    args = parser.parse_args(argv)

    prompts = _discover(FALLBACK_DIR)
    if not prompts:
        print(f"No fallback prompts found in {FALLBACK_DIR}.")
        return 1

    if args.dry_run:
        print(f"Would push {len(prompts)} prompt(s) with label {args.label!r}:")
        for name, kind, _ in prompts:
            print(f"  - {name} ({kind})")
        return 0

    client = get_langfuse()
    if client is None:
        print(
            "Langfuse is not configured (set LANGFUSE_PUBLIC_KEY / LANGFUSE_SECRET_KEY "
            "in .env). Nothing pushed.",
            file=sys.stderr,
        )
        return 1

    for name, kind, payload in prompts:
        client.create_prompt(name=name, type=kind, prompt=payload, labels=[args.label])
        print(f"pushed {name} ({kind}) -> label {args.label!r}")

    client.flush()
    print(f"done: {len(prompts)} prompt(s) pushed.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
