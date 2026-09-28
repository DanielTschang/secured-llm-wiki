import pytest
from hypothesis import given
from hypothesis import strategies as st

from kc_labels import Labels, ReadableSpaces, SpaceId, can_read, derive

space_ids = st.from_regex(r"sp_[a-z0-9_]{1,12}", fullmatch=True)
# A small alphabet makes overlapping label sets (the interesting cases) common.
small_space_ids = st.sampled_from(["sp_a", "sp_b", "sp_c", "sp_d", "sp_e"])
labels = st.frozensets(small_space_ids, min_size=1).map(Labels.of)
readables = st.frozensets(small_space_ids).map(ReadableSpaces.of)


# --- SpaceId / Labels construction ----------------------------------------


@given(space_ids)
def test_valid_space_ids_accepted(raw: str) -> None:
    assert SpaceId(raw) == raw


@pytest.mark.parametrize(
    "raw",
    ["", "sp_", "opc", "SP_OPC", "sp_OPC", "sp opc", "sp_opc ", "sp_光罩", "sp-opc", "sp_opc\n"],
)
def test_invalid_space_ids_rejected(raw: str) -> None:
    with pytest.raises(ValueError, match="space id"):
        SpaceId(raw)


def test_empty_labels_rejected() -> None:
    with pytest.raises(ValueError, match="empty"):
        Labels.of([])


def test_labels_reject_invalid_member() -> None:
    with pytest.raises(ValueError, match="space id"):
        Labels.of(["sp_ok", "not a space"])


def test_derive_without_inputs_rejected() -> None:
    with pytest.raises(ValueError, match="at least one"):
        derive()


def test_labels_are_immutable() -> None:
    lab = Labels.of(["sp_a"])
    with pytest.raises(AttributeError):
        lab.spaces = frozenset()  # type: ignore[misc]


def test_error_messages_do_not_echo_input() -> None:
    # Invariant 7: exceptions may end up in logs, so they must not carry content.
    with pytest.raises(ValueError) as exc:
        SpaceId("KESTREL-7 secret")
    assert "KESTREL" not in str(exc.value)


# --- derive ----------------------------------------------------------------


@given(st.lists(labels, min_size=1, max_size=5))
def test_derive_is_union(inputs: list[Labels]) -> None:
    expected = frozenset[str]().union(*(i.spaces for i in inputs))
    assert derive(*inputs).spaces == expected


@given(labels, labels)
def test_derive_commutative(a: Labels, b: Labels) -> None:
    assert derive(a, b) == derive(b, a)


@given(labels, labels, labels)
def test_derive_associative(a: Labels, b: Labels, c: Labels) -> None:
    assert derive(derive(a, b), c) == derive(a, derive(b, c))


@given(labels)
def test_derive_idempotent(a: Labels) -> None:
    assert derive(a, a) == a
    assert derive(a) == a


@given(st.lists(labels, min_size=1, max_size=5))
def test_derived_is_superset_of_every_input(inputs: list[Labels]) -> None:
    out = derive(*inputs)
    for i in inputs:
        assert i.spaces <= out.spaces


# --- can_read --------------------------------------------------------------


@given(readables, labels)
def test_can_read_is_subset(r: ReadableSpaces, lab: Labels) -> None:
    assert can_read(r, lab) == (lab.spaces <= r.spaces)


@given(readables, labels, labels)
def test_can_read_derived_iff_can_read_all_inputs(r: ReadableSpaces, a: Labels, b: Labels) -> None:
    assert can_read(r, derive(a, b)) == (can_read(r, a) and can_read(r, b))


@given(readables, labels, labels)
def test_widening_labels_never_grants_access(r: ReadableSpaces, a: Labels, extra: Labels) -> None:
    if not can_read(r, a):
        assert not can_read(r, derive(a, extra))


@given(st.frozensets(small_space_ids), st.frozensets(small_space_ids), labels)
def test_shrinking_readable_never_grants_access(
    big: frozenset[str], removed: frozenset[str], lab: Labels
) -> None:
    r_big = ReadableSpaces.of(big)
    r_small = ReadableSpaces.of(big - removed)
    if not can_read(r_big, lab):
        assert not can_read(r_small, lab)


@given(labels)
def test_no_readable_spaces_reads_nothing(lab: Labels) -> None:
    assert not can_read(ReadableSpaces.of([]), lab)


def test_can_read_rejects_raw_sets() -> None:
    # Guard against callers passing a plain set, which would bypass validation.
    with pytest.raises(TypeError):
        can_read(frozenset({"sp_a"}), Labels.of(["sp_a"]))  # type: ignore[arg-type]
    with pytest.raises(TypeError):
        can_read(ReadableSpaces.of(["sp_a"]), frozenset({"sp_a"}))  # type: ignore[arg-type]


def test_labels_reject_mutable_set() -> None:
    with pytest.raises(TypeError):
        Labels({SpaceId("sp_cd")})  # type: ignore[arg-type]


def test_labels_reject_unvalidated_members() -> None:
    with pytest.raises(TypeError):
        Labels(frozenset({"sp_cd"}))  # type: ignore[arg-type]


def test_readable_spaces_reject_mutable_set() -> None:
    with pytest.raises(TypeError):
        ReadableSpaces({SpaceId("sp_cd")})  # type: ignore[arg-type]


def test_readable_spaces_reject_unvalidated_members() -> None:
    with pytest.raises(TypeError):
        ReadableSpaces(frozenset({"SP OPC!!"}))  # type: ignore[arg-type]
