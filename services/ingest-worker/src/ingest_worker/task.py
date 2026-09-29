"""Subprocess entry for one ingest task. Holds only its space's child token.

Order matters: the process makes itself non-dumpable first, then tells the runner it is
ready, and only then receives the token on stdin. Heavy imports come after, so there is
no window in which a sibling process could read the token through /proc.
"""

import os
import sys
from pathlib import Path

from ingest_worker.hardening import READY, harden_process

__all__ = ["harden_process", "main"]


def main() -> int:
    harden_process()
    print(READY, flush=True)
    token = sys.stdin.readline().strip()  # only now, and never from the environment

    import contextlib

    import httpx

    from ingest_worker.broker import EXIT_DONE, EXIT_STALE
    from ingest_worker.pipeline import Quarantined, Stale, ingest_page
    from ingest_worker.steps.read import Resources
    from kc_ids import PageId, Revision
    from kc_labels import SpaceId
    from kc_models import from_env as model_from_env
    from kc_obs import configure_logging, get_logger
    from kc_store.context import open_space
    from kc_store.vault import VaultClient

    configure_logging()
    log = get_logger("ingest_task")
    space = SpaceId(os.environ["KC_SPACE_ID"])
    page_id = PageId(os.environ["KC_PAGE_ID"])
    revision = Revision(int(os.environ["KC_REVISION"]))
    vault = VaultClient(httpx.Client(base_url=os.environ["KC_VAULT_ADDR"], timeout=30), token)
    try:
        with open_space(space, vault) as ctx:
            resources = Resources.load(Path(os.environ["KC_SCHEMA_DIR"]))
            model = model_from_env()
            ingest_page(
                ctx,
                model,
                model_from_env("KC_EMBED_MODEL_NAME"),
                resources,
                page_id,
                revision,
                model_id=os.environ["KC_MODEL_NAME"],
            )
    except Stale:
        log.info("task_stale", space_id=space, page_id=page_id, revision=int(revision))
        return EXIT_STALE
    except Quarantined:
        log.info("task_quarantined", space_id=space, page_id=page_id, revision=int(revision))
        return EXIT_STALE  # nothing to do; ack and drop
    except Exception as e:
        log.error("task_error", space_id=space, page_id=page_id, error=e)
        return 1
    finally:
        # The token is useless after this task; do not leave it valid until its TTL.
        try:
            vault.revoke_self()
        except Exception as e:
            log.error("token_revoke_failed", space_id=space, page_id=page_id, error=e)
        with contextlib.suppress(Exception):
            vault.close()
    log.info("task_done", space_id=space, page_id=page_id, revision=int(revision))
    return EXIT_DONE


if __name__ == "__main__":
    sys.exit(main())
