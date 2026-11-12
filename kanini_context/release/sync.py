"""Check the client copy against the latest build and record the check as a sync (spec section 6.6).

    python -m kanini_context.release.sync

Each check is a sync: when the client copy holds the same rules as the latest build, the check is recorded in
kc_client.sync_log, which keeps answers from going "sync aging" (insurer lists: 48 hours) or "out of sync"
(7 days). When the build holds different rules, nothing is recorded: install the new release with release.build.
"""
from __future__ import annotations

import sys
from datetime import datetime, timezone

import psycopg

from kanini_context import env
from kanini_context.release.build import dsn, rule_digest


def main() -> int:
    try:
        with psycopg.connect(dsn(env.BUILD_DB)) as b:
            latest = rule_digest(b.cursor())
    except psycopg.Error as ex:
        print(f"cannot read the build database: {ex}")
        return 1
    with psycopg.connect(dsn(env.CLIENT_DB)) as c:
        rel, digest = c.execute("""SELECT release_id, digest FROM kc_gov.release
                                    WHERE release_id = current_setting('kc.release', true)""").fetchone()
        if digest != latest:
            print(f"release {rel} differs from the latest build: install it with python -m kanini_context.release.build")
            return 1
        now = datetime.now(timezone.utc)
        c.execute("INSERT INTO kc_client.sync_log VALUES (%s, %s)", (now, rel))
        print(f"synced: release {rel} is current ({now:%Y-%m-%d %H:%M} UTC)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
