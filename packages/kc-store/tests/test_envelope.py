import pytest

from kc_labels import SpaceId
from kc_store.envelope import DecryptError, ObjectRef, open_sealed, seal
from kc_store.testing import FakeKeyService

OPC = SpaceId("sp_opc")
CD = SpaceId("sp_cd")


def ref(space: SpaceId = OPC, object_id: str = "opc_o1", revision: int = 1) -> ObjectRef:
    return ObjectRef(space_id=space, kind="page", object_id=object_id, revision=revision)


def test_roundtrip() -> None:
    keys = FakeKeyService()
    blob = seal(keys, ref(), "KESTREL-7 設定".encode())
    assert b"KESTREL" not in blob
    assert open_sealed(keys, ref(), blob) == "KESTREL-7 設定".encode()


def test_each_object_gets_its_own_data_key() -> None:
    keys = FakeKeyService()
    seal(keys, ref(), b"a")
    seal(keys, ref(), b"a")
    assert keys.data_keys_issued == 2


@pytest.mark.parametrize(
    "moved",
    [ref(space=CD), ref(object_id="opc_o2"), ref(revision=2)],
    ids=["other-space", "other-object", "other-revision"],
)
def test_ciphertext_bound_to_its_place(moved: ObjectRef) -> None:
    keys = FakeKeyService()
    blob = seal(keys, ref(), b"secret")
    with pytest.raises(DecryptError) as exc:
        open_sealed(keys, moved, blob)
    assert "secret" not in str(exc.value)


def test_tampering_detected() -> None:
    keys = FakeKeyService()
    blob = bytearray(seal(keys, ref(), b"secret"))
    blob[-1] ^= 1
    with pytest.raises(DecryptError):
        open_sealed(keys, ref(), bytes(blob))


def test_other_space_key_cannot_unwrap() -> None:
    # A key service holding only sp_cd's key cannot open sp_opc data.
    keys = FakeKeyService()
    blob = seal(keys, ref(), b"secret")
    with pytest.raises(DecryptError):
        open_sealed(keys.restricted_to(CD), ref(), blob)
