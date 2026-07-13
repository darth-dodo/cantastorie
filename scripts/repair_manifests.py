from __future__ import annotations

import argparse
import json
import sys
from typing import TYPE_CHECKING

from src.config import Settings, get_settings
from src.pipeline.models import Story
from src.pipeline.publish import PUBLISHED_PREFIX, THEME_WASH, _build_client, _load_manifest

if TYPE_CHECKING:
    from mypy_boto3_s3 import S3Client


def repair_manifests(
    settings: Settings,
    *,
    client: S3Client | None = None,
    dry_run: bool = False,
) -> list[str]:
    if not settings.r2_public_base:
        raise ValueError("R2_PUBLIC_BASE must be set before repairing manifests")

    client = client or _build_client(settings)
    public_base = settings.r2_public_base.rstrip("/")
    repaired: list[str] = []
    paginator = client.get_paginator("list_objects_v2")

    for page in paginator.paginate(Bucket=settings.r2_bucket, Prefix=f"{PUBLISHED_PREFIX}/"):
        for item in page.get("Contents", []):
            key = item["Key"]
            path = key.removeprefix(f"{PUBLISHED_PREFIX}/")
            parts = path.split("/")
            if len(parts) != 2 or parts[1] != "manifest.json":
                continue

            manifest, _ = _load_manifest(client, settings.r2_bucket, parts[0])
            changed = False
            for entry in manifest.get("stories", []):
                story_id = entry["id"]
                story_bytes = client.get_object(
                    Bucket=settings.r2_bucket,
                    Key=f"{PUBLISHED_PREFIX}/stories/{story_id}/story.json",
                )["Body"].read()
                story = Story.model_validate_json(story_bytes)
                expected_wash = THEME_WASH[story.theme]
                if entry.get("wash") != expected_wash:
                    entry["wash"] = expected_wash
                    changed = True

                for field in ("story", "cover"):
                    url = entry[field]
                    if not url.startswith(("http://", "https://")):
                        entry[field] = f"{public_base}/{url.lstrip('/')}"
                        changed = True

            if changed:
                repaired.append(key)
                if not dry_run:
                    client.put_object(
                        Bucket=settings.r2_bucket,
                        Key=key,
                        Body=json.dumps(manifest, ensure_ascii=False, indent=2).encode("utf-8"),
                        ContentType="application/json",
                    )

    return repaired


def main() -> int:
    parser = argparse.ArgumentParser(description="Repair published R2 manifests")
    parser.add_argument("--dry-run", action="store_true", help="Report changes without writing")
    args = parser.parse_args()

    try:
        repaired = repair_manifests(get_settings(), dry_run=args.dry_run)
    except Exception as error:
        print(f"Manifest repair failed: {error}", file=sys.stderr)
        return 1

    action = "Would repair" if args.dry_run else "Repaired"
    for key in repaired:
        print(f"{action} {key}")
    print(f"{action} {len(repaired)} manifest(s).")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
