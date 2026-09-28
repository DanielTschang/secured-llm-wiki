"""Subprocess entry for one ingest task. Holds only its space's child token."""

import os
import sys

import httpx

from ingest_worker.broker import EXIT_DONE, EXIT_STALE
from ingest_worker.pipeline import Stale, stub_ingest
from kc_ids import PageId, Revision
from kc_labels import SpaceId
from kc_obs import configure_logging, get_logger
from kc_store.context import open_space
from kc_store.vault import VaultClient

log = get_logger("ingest_task")


def main() -> int:
    configure_logging()
    space = SpaceId(os.environ["KC_SPACE_ID"])
    page_id = PageId(os.environ["KC_PAGE_ID"])
    revision = Revision(int(os.environ["KC_REVISION"]))
    vault = VaultClient(
        httpx.Client(base_url=os.environ["KC_VAULT_ADDR"], timeout=30), os.environ["KC_VAULT_TOKEN"]
    )
    try:
        with open_space(space, vault) as ctx:
            stub_ingest(ctx, page_id, revision)
    except Stale:
        log.info("task_stale", space_id=space, page_id=page_id, revision=int(revision))
        return EXIT_STALE
    except Exception as e:
        log.error("task_error", space_id=space, page_id=page_id, error=e)
        return 1
    log.info("task_done", space_id=space, page_id=page_id, revision=int(revision))
    return EXIT_DONE


if __name__ == "__main__":
    sys.exit(main())
