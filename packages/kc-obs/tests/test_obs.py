import json
import logging

import pytest

from kc_ids import PageId, Revision
from kc_labels import SpaceId
from kc_obs import get_logger


def test_logs_ids_and_numbers_as_json(caplog: pytest.LogCaptureFixture) -> None:
    caplog.set_level(logging.INFO)
    log = get_logger("sync")
    log.info(
        "page_synced",
        space_id=SpaceId("sp_opc"),
        page_id=PageId("opc_o1"),
        revision=Revision(2),
        duration_ms=12.5,
        changed=True,
    )
    rec = json.loads(caplog.records[-1].getMessage())
    assert rec == {
        "event": "page_synced",
        "space_id": "sp_opc",
        "page_id": "opc_o1",
        "revision": 2,
        "duration_ms": 12.5,
        "changed": True,
    }


@pytest.mark.parametrize(
    "value",
    ["OPC 實務入門（2025 版）", b"bytes", ["sp_opc"], {"a": 1}, ValueError("KESTREL-7"), None],
)
def test_rejects_non_id_values(value: object) -> None:
    with pytest.raises(TypeError) as exc:
        get_logger("sync").info("x", page_id=value)  # type: ignore[arg-type]
    assert "KESTREL" not in str(exc.value)
    assert "OPC" not in str(exc.value)


@pytest.mark.parametrize("name", ["Title", "page title", "", "x" * 65])
def test_rejects_bad_event_or_field_names(name: str) -> None:
    with pytest.raises(ValueError):
        get_logger("sync").info(name)
    with pytest.raises(ValueError):
        get_logger("sync").info("ok", **{name: 1})


def test_exceptions_logged_by_type_only(caplog: pytest.LogCaptureFixture) -> None:
    caplog.set_level(logging.INFO)
    try:
        raise RuntimeError("KESTREL-7 secret")
    except RuntimeError as e:
        get_logger("sync").error("task_failed", error=e, page_id=PageId("opc_o1"))
    msg = caplog.records[-1].getMessage()
    assert "KESTREL" not in msg
    assert json.loads(msg)["error"] == "RuntimeError"
    assert caplog.records[-1].exc_info is None


def test_configure_logging_silences_third_party_request_logs(
    caplog: pytest.LogCaptureFixture,
) -> None:
    from kc_obs import configure_logging

    configure_logging()
    caplog.set_level(logging.DEBUG)
    for name in ("httpx", "httpcore", "botocore", "urllib3", "neo4j", "pymongo", "nats"):
        logging.getLogger(name).info("GET http://x/api/pages/p/attachments/secret-name.png")
    assert "secret-name" not in caplog.text
