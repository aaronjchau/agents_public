"""One-shot backfill of email_received_at on existing audit rows.

Thin CLI wrapper around shared.backfill_email_received_at.run; the logic
lives in shared/ so the triager's Modal app can import it too.

    make run-script f=scripts/backfill_email_received_at.py

Or via Modal against production:

    uv run modal run services/triager/modal_app.py::backfill_email_received_at

Idempotent: only touches rows where the column is NULL.
"""

from __future__ import annotations

import asyncio
import logging
import os

# Bridge the local-.env naming mismatch BEFORE importing Settings.
if "NOTION_TOKEN" not in os.environ and "NOTION_API_TOKEN" in os.environ:
    os.environ["NOTION_TOKEN"] = os.environ["NOTION_API_TOKEN"]

from shared.backfill_email_received_at import run


def main() -> None:
    logging.basicConfig(level=logging.INFO)
    summary = asyncio.run(run())
    print(summary)


if __name__ == "__main__":
    main()
