"""AES-GCM payload codec: Temporal stores ciphertext, never a premise (ADR-012)."""

import os
from collections.abc import Sequence

from cryptography.exceptions import InvalidTag
from cryptography.hazmat.primitives.ciphers.aead import AESGCM
from temporalio.api.common.v1 import Payload
from temporalio.converter import PayloadCodec

from src.config import PAYLOAD_KEY_BYTES

ENCRYPTED_ENCODING = b"binary/encrypted-aes-gcm"
NONCE_BYTES = 12


class PayloadDecryptionError(ValueError):
    """A payload did not decrypt with this key: wrong key or tampered bytes."""


class AesGcmPayloadCodec(PayloadCodec):
    """Encrypts each whole serialized payload, metadata included.

    The wire form is a random 96-bit nonce followed by the AES-GCM ciphertext
    and tag. Payloads the server wrote itself (not encrypted by us) pass
    through decode unchanged.
    """

    def __init__(self, key: bytes) -> None:
        if len(key) != PAYLOAD_KEY_BYTES:
            raise ValueError(f"the payload key must be {PAYLOAD_KEY_BYTES} bytes")
        self._aead = AESGCM(key)

    async def encode(self, payloads: Sequence[Payload]) -> list[Payload]:
        return [self._encrypt(payload) for payload in payloads]

    async def decode(self, payloads: Sequence[Payload]) -> list[Payload]:
        return [self._decrypt(payload) for payload in payloads]

    def _encrypt(self, payload: Payload) -> Payload:
        nonce = os.urandom(NONCE_BYTES)
        sealed = self._aead.encrypt(nonce, payload.SerializeToString(), None)
        return Payload(metadata={"encoding": ENCRYPTED_ENCODING}, data=nonce + sealed)

    def _decrypt(self, payload: Payload) -> Payload:
        if payload.metadata.get("encoding") != ENCRYPTED_ENCODING:
            return payload
        nonce, sealed = payload.data[:NONCE_BYTES], payload.data[NONCE_BYTES:]
        try:
            plain = self._aead.decrypt(nonce, sealed, None)
        except InvalidTag as exc:
            raise PayloadDecryptionError("payload failed to decrypt") from exc
        decoded = Payload()
        decoded.ParseFromString(plain)
        return decoded
