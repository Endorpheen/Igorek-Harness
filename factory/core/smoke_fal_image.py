#!/usr/bin/env python3
from __future__ import annotations

import os
import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from factory.mcp_servers.fal_image_server import _build_fal_payload


def main() -> int:
    for key in (
        "FAL_IMAGE_MODEL",
        "FAL_IMAGE_SIZE",
        "FAL_IMAGE_QUALITY",
        "FAL_IMAGE_NUM_IMAGES",
        "FAL_IMAGE_OUTPUT_FORMAT",
        "FAL_IMAGE_SYNC_MODE",
    ):
        os.environ.pop(key, None)

    model, payload = _build_fal_payload({"prompt": "smoke"})
    assert model == "openai/gpt-image-2", model
    assert payload == {
        "prompt": "smoke",
        "image_size": "landscape_4_3",
        "quality": "low",
        "num_images": 1,
        "output_format": "png",
        "sync_mode": False,
    }, payload

    _, explicit_size_payload = _build_fal_payload(
        {"prompt": "smoke", "image_size": "1024x768"}
    )
    assert explicit_size_payload["image_size"] == {"width": 1024, "height": 768}

    print("smoke_fal_image: PASS")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
