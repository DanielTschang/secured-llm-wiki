import json

import pytest

from kc_events import PageEvent, subject_for


def ev() -> PageEvent:
    return PageEvent.of(space_id="sp_opc", page_id="opc_o1", revision=3)


def test_roundtrip_contains_only_ids() -> None:
    raw = ev().to_bytes()
    assert json.loads(raw) == {"space_id": "sp_opc", "page_id": "opc_o1", "revision": 3}
    assert PageEvent.from_bytes(raw) == ev()


def test_subject_per_space() -> None:
    assert ev().subject == "kc.page.sp_opc"
    assert subject_for("sp_cd") == "kc.page.sp_cd"


@pytest.mark.parametrize(
    "payload",
    [
        {"space_id": "sp_opc", "page_id": "opc_o1", "revision": 3, "title": "KESTREL"},
        {"space_id": "sp_opc", "page_id": "opc_o1"},
        {"space_id": "OPC", "page_id": "opc_o1", "revision": 3},
        {"space_id": "sp_opc", "page_id": "a b", "revision": 3},
        {"space_id": "sp_opc", "page_id": "opc_o1", "revision": 0},
        {"space_id": "sp_opc", "page_id": "opc_o1", "revision": "3"},
    ],
)
def test_rejects_anything_but_three_ids(payload: dict[str, object]) -> None:
    with pytest.raises(ValueError):
        PageEvent.from_bytes(json.dumps(payload).encode())


def test_rejects_event_on_wrong_subject() -> None:
    with pytest.raises(ValueError):
        PageEvent.from_message(subject="kc.page.sp_cd", data=ev().to_bytes())
    assert PageEvent.from_message(subject="kc.page.sp_opc", data=ev().to_bytes()) == ev()
