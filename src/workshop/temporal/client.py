"""The Temporal client the app and the worker share (ADR-012).

Every payload goes through the Pydantic converter, then the AES-GCM codec, and
failure messages and stack traces are moved into encoded attributes, so the
server holds ciphertext, run ids, activity names and timings only.
"""

import dataclasses

from temporalio.client import Client
from temporalio.contrib.pydantic import pydantic_data_converter
from temporalio.converter import DataConverter, DefaultFailureConverterWithEncodedAttributes

from src.config import Settings, decode_payload_key
from src.workshop.temporal.codec import AesGcmPayloadCodec


def data_converter(payload_key: bytes) -> DataConverter:
    return dataclasses.replace(
        pydantic_data_converter,
        payload_codec=AesGcmPayloadCodec(payload_key),
        failure_converter_class=DefaultFailureConverterWithEncodedAttributes,
    )


async def connect(settings: Settings, *, tls: bool | None = None) -> Client:
    """Connect with the Temporal settings; TLS follows the API key unless set.

    With an API key (Temporal Cloud) TLS is on by default; ``tls=False`` is only
    for the local dev and test servers.
    """
    return await Client.connect(
        settings.temporal_address,
        namespace=settings.temporal_namespace,
        api_key=settings.temporal_api_key.get_secret_value() or None,
        data_converter=data_converter(
            decode_payload_key(settings.temporal_payload_key.get_secret_value())
        ),
        tls=tls,
    )
