"""Dashboard renders without a palette bar (AI-453: single indigo palette)."""

from pathlib import Path

import boto3
import pytest
from moto import mock_aws
from mypy_boto3_s3 import S3Client

from tests.workshop.test_routes import BUCKET, PENDING_BUCKET, _Harness


@pytest.fixture
def s3() -> S3Client:
    with mock_aws():
        client = boto3.client("s3", region_name="us-east-1")
        client.create_bucket(Bucket=BUCKET)
        client.create_bucket(Bucket=PENDING_BUCKET)
        yield client


def test_palette_bar_absent_by_default(tmp_path: Path, s3: S3Client) -> None:
    harness = _Harness(tmp_path, s3)
    harness.sign_in()

    r = harness.client.get("/workshop")

    assert r.status_code == 200
    assert "ws-palette-bar" not in r.text


def test_palette_bar_absent_with_debug(tmp_path: Path, s3: S3Client) -> None:
    """Palette bar is removed entirely — even ?debug=1 no longer renders it."""
    harness = _Harness(tmp_path, s3)
    harness.sign_in()

    r = harness.client.get("/workshop?debug=1")

    assert r.status_code == 200
    assert "ws-palette-bar" not in r.text
