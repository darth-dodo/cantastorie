"""Behavior specs for the payload codec (ADR-012).

Spec: "No child or family data in clear in the engine". Every payload is
AES-GCM encrypted with TEMPORAL_PAYLOAD_KEY before it leaves the app, so
Temporal Cloud stores ciphertext only.
"""

import asyncio
import os

import pytest
from temporalio.api.common.v1 import Payload
from temporalio.converter import DataConverter

from src.workshop.temporal.codec import (
    ENCRYPTED_ENCODING,
    AesGcmPayloadCodec,
    PayloadDecryptionError,
)

PREMISE = "Una volpe di nome Lucia trova una chiave dorata nel bosco"


def _payload(text: str) -> Payload:
    return DataConverter.default.payload_converter.to_payloads([text])[0]


def _roundtrip(codec: AesGcmPayloadCodec, payload: Payload) -> Payload:
    encoded = asyncio.run(codec.encode([payload]))
    return asyncio.run(codec.decode(encoded))[0]


def test_codec_round_trips_a_payload() -> None:
    codec = AesGcmPayloadCodec(os.urandom(32))
    original = _payload(PREMISE)
    assert _roundtrip(codec, original) == original


def test_codec_round_trips_many_payloads_in_order() -> None:
    codec = AesGcmPayloadCodec(os.urandom(32))
    originals = [_payload(f"page {n}") for n in range(5)]
    encoded = asyncio.run(codec.encode(originals))
    assert asyncio.run(codec.decode(encoded)) == originals


def test_encoded_payload_holds_no_sample_premise_in_clear() -> None:
    codec = AesGcmPayloadCodec(os.urandom(32))
    encoded = asyncio.run(codec.encode([_payload(PREMISE)]))[0]
    wire = encoded.SerializeToString()
    assert PREMISE.encode() not in wire
    assert b"Lucia" not in wire
    assert encoded.metadata["encoding"] == ENCRYPTED_ENCODING


def test_each_encoding_uses_a_fresh_nonce() -> None:
    codec = AesGcmPayloadCodec(os.urandom(32))
    payload = _payload(PREMISE)
    first, second = (asyncio.run(codec.encode([payload]))[0] for _ in range(2))
    assert first.data != second.data


def test_a_payload_encoded_with_one_key_fails_to_decode_with_another() -> None:
    encoded = asyncio.run(AesGcmPayloadCodec(os.urandom(32)).encode([_payload(PREMISE)]))
    with pytest.raises(PayloadDecryptionError):
        asyncio.run(AesGcmPayloadCodec(os.urandom(32)).decode(encoded))


def test_a_tampered_payload_fails_to_decode() -> None:
    codec = AesGcmPayloadCodec(os.urandom(32))
    encoded = asyncio.run(codec.encode([_payload(PREMISE)]))[0]
    tampered = Payload(
        metadata=dict(encoded.metadata), data=encoded.data[:-1] + bytes([encoded.data[-1] ^ 1])
    )
    with pytest.raises(PayloadDecryptionError):
        asyncio.run(codec.decode([tampered]))


@pytest.mark.parametrize(
    "plain",
    [
        _payload("injected premise"),
        DataConverter.default.payload_converter.to_payloads([None])[0],
        Payload(metadata={"encoding": b"binary/plain"}, data=b""),
        Payload(),
    ],
)
def test_a_payload_not_encrypted_by_us_is_refused_on_decode(plain: Payload) -> None:
    codec = AesGcmPayloadCodec(os.urandom(32))
    with pytest.raises(PayloadDecryptionError, match="not encrypted"):
        asyncio.run(codec.decode([plain]))


def test_metadata_only_payloads_the_sdk_makes_round_trip() -> None:
    codec = AesGcmPayloadCodec(os.urandom(32))
    null = DataConverter.default.payload_converter.to_payloads([None])[0]
    assert _roundtrip(codec, null) == null
    assert _roundtrip(codec, Payload()) == Payload()


def test_codec_refuses_a_key_that_is_not_32_bytes() -> None:
    with pytest.raises(ValueError, match="32 bytes"):
        AesGcmPayloadCodec(b"short")
