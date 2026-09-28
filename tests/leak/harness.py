"""Leak-test helpers. Canaries and users come only from the synthetic test set."""

import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import httpx

TESTSET = Path(__file__).parents[1] / "fixtures/synthetic_litho_testset"


@dataclass(frozen=True)
class Canary:
    text: str
    source_space: str
    must_never_reach: frozenset[str]
    context: str | None = None


def load_canaries() -> list[Canary]:
    raw: list[dict[str, Any]] = json.loads((TESTSET / "gold/leak_probes.json").read_text())
    return [
        Canary(
            text=c["canary"],
            source_space=c["source_space"],
            must_never_reach=frozenset(c["must_never_reach"]),
            context=c.get("context"),
        )
        for c in raw
    ]


def load_manifest() -> dict[str, Any]:
    return json.loads((TESTSET / "manifest.json").read_text())


def load_users() -> dict[str, frozenset[str]]:
    return {u: frozenset(s) for u, s in load_manifest()["users"].items()}


def load_titles() -> list[str]:
    return [p["title"] for p in load_manifest()["pages"]]


def assert_no_canary(output: str, user: str, canaries: list[Canary] | None = None) -> None:
    """Fail if any canary that must never reach `user` appears in `output`.

    Context-scoped canaries (e.g. bare numbers like "3.4") are checked too; tests that emit
    unrelated numbers must keep outputs narrow rather than weaken this check.
    """
    for c in canaries if canaries is not None else load_canaries():
        if user in c.must_never_reach:
            assert c.text not in output, f"canary from {c.source_space} reached {user}"


def assert_no_content(text: str) -> None:
    """For logs/metrics/traces: no canary and no page title may appear at all (invariant 7)."""
    for c in load_canaries():
        if c.context is None:  # bare-number canaries are only meaningful in answers
            assert c.text not in text, f"canary from {c.source_space} found in observability output"
    for t in load_titles():
        assert t not in text, "page title found in observability output"


# Headers that legitimately differ between any two responses.
_VOLATILE_HEADERS = {"date", "content-length"}


def assert_indistinguishable(a: httpx.Response, b: httpx.Response) -> None:
    """'No permission' and 'not found' must look identical (invariant 5).

    Timing is compared statistically once there are expensive code paths (M4).
    """
    assert a.status_code == b.status_code
    assert a.content == b.content
    ha = {k.lower(): v for k, v in a.headers.items() if k.lower() not in _VOLATILE_HEADERS}
    hb = {k.lower(): v for k, v in b.headers.items() if k.lower() not in _VOLATILE_HEADERS}
    assert ha == hb
