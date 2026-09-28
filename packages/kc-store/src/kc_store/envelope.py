"""Envelope encryption bound to where the object lives.

Layout: MAGIC | u16 len(wrapped) | wrapped data key | 12-byte nonce | AES-256-GCM ciphertext.
The AAD binds (space, kind, object id, revision), so ciphertext copied to another space,
object or revision fails to decrypt; the data key is wrapped by the space's own key.
"""

import os
import struct
from dataclasses import dataclass

from cryptography.exceptions import InvalidTag
from cryptography.hazmat.primitives.ciphers.aead import AESGCM

from kc_labels import SpaceId
from kc_store.keys import KeyService, KeyServiceError

__all__ = ["DecryptError", "ObjectRef", "open_sealed", "seal"]

_MAGIC = b"KC1\x00"


class DecryptError(Exception):
    def __init__(self) -> None:
        super().__init__("cannot decrypt object")


@dataclass(frozen=True, slots=True)
class ObjectRef:
    space_id: SpaceId
    kind: str
    object_id: str
    revision: int

    def aad(self) -> bytes:
        return f"kc1|{self.space_id}|{self.kind}|{self.object_id}|{self.revision}".encode()


def seal(keys: KeyService, ref: ObjectRef, plaintext: bytes) -> bytes:
    dek, wrapped = keys.new_data_key(ref.space_id)
    nonce = os.urandom(12)
    ct = AESGCM(dek).encrypt(nonce, plaintext, ref.aad())
    w = wrapped.encode("ascii")
    return _MAGIC + struct.pack(">H", len(w)) + w + nonce + ct


def open_sealed(keys: KeyService, ref: ObjectRef, blob: bytes) -> bytes:
    try:
        if not blob.startswith(_MAGIC):
            raise DecryptError
        (n,) = struct.unpack(">H", blob[4:6])
        wrapped = blob[6 : 6 + n].decode("ascii")
        nonce = blob[6 + n : 18 + n]
        ct = blob[18 + n :]
        dek = keys.unwrap(ref.space_id, wrapped)
        return AESGCM(dek).decrypt(nonce, ct, ref.aad())
    except (InvalidTag, KeyServiceError, ValueError, struct.error, UnicodeDecodeError) as e:
        raise DecryptError from e
