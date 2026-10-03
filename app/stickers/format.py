"""Detect image kind from magic bytes and frame count (no LLM)."""

from __future__ import annotations

import io
import logging
from dataclasses import dataclass

from PIL import Image

_log = logging.getLogger(__name__)

_PNG = b"\x89PNG\r\n\x1a\n"
_JPEG = b"\xff\xd8\xff"
_GIF87 = b"GIF87a"
_GIF89 = b"GIF89a"
_RIFF = b"RIFF"
_WEBP = b"WEBP"

KIND_PNG = "png"
KIND_JPEG = "jpeg"
KIND_GIF = "gif"
KIND_WEBP = "webp"
KIND_UNKNOWN = "unknown"


@dataclass(frozen=True)
class ImageKind:
    """Container type plus whether the file has more than one frame."""

    container: str
    animated: bool


def detect_image_kind(data: bytes) -> ImageKind:
    """Classify bytes by magic number and PIL frame count."""
    container = _sniff_container(data)
    animated = _is_animated(data, container)
    return ImageKind(container=container, animated=animated)


def encode_for_library(data: bytes, mime: str = "") -> tuple[bytes, str, str]:
    """Keep original bytes; pick suffix from magic. Never transcode (that kills animation)."""
    del mime
    if not data:
        return b"", ".png", "image/png"
    kind = detect_image_kind(data)
    if kind.container == KIND_GIF:
        return data, ".gif", "image/gif"
    if kind.container == KIND_PNG:
        return data, ".png", "image/png"
    if kind.container == KIND_WEBP:
        return data, ".webp", "image/webp"
    if kind.container == KIND_JPEG:
        return data, ".jpg", "image/jpeg"
    return data, ".bin", "application/octet-stream"


def frame_for_model(data: bytes) -> tuple[bytes, str]:
    """Return a still PNG/JPEG frame for multimodal APIs (first frame if animated)."""
    if not data:
        return b"", "image/png"
    kind = detect_image_kind(data)
    if kind.container == KIND_PNG and not kind.animated:
        return data, "image/png"
    if kind.container == KIND_JPEG and not kind.animated:
        return data, "image/jpeg"
    return _first_frame_png(data)


def sniffed_mime(data: bytes) -> str:
    """MIME from magic bytes; unknown defaults to image/jpeg for callers."""
    kind = detect_image_kind(data)
    if kind.container == KIND_PNG:
        return "image/png"
    if kind.container == KIND_GIF:
        return "image/gif"
    if kind.container == KIND_WEBP:
        return "image/webp"
    return "image/jpeg"


def _sniff_container(data: bytes) -> str:
    """Return png/jpeg/gif/webp/unknown from the first bytes."""
    if data.startswith(_PNG):
        return KIND_PNG
    if data.startswith(_JPEG):
        return KIND_JPEG
    if data.startswith(_GIF87) or data.startswith(_GIF89):
        return KIND_GIF
    if len(data) >= 12 and data.startswith(_RIFF) and data[8:12] == _WEBP:
        return KIND_WEBP
    return KIND_UNKNOWN


def _is_animated(data: bytes, container: str) -> bool:
    """True when PIL reports more than one frame (GIF/WebP)."""
    if container not in {KIND_GIF, KIND_WEBP}:
        return False
    try:
        image = Image.open(io.BytesIO(data))
        n_frames = int(getattr(image, "n_frames", 1) or 1)
        animated = bool(getattr(image, "is_animated", False))
        return animated or n_frames > 1
    except Exception:
        _log.exception("frame-count detect failed")
        return container == KIND_GIF and data.startswith(_GIF89)


def _first_frame_png(data: bytes) -> tuple[bytes, str]:
    """Flatten to a single PNG frame for the vision/chat payload."""
    try:
        image = Image.open(io.BytesIO(data))
        image.seek(0)
        image = _still_mode(image)
        buf = io.BytesIO()
        image.save(buf, format="PNG")
        return buf.getvalue(), "image/png"
    except Exception:
        _log.exception("first-frame png failed")
        return data, "image/jpeg"


def _still_mode(image: Image.Image) -> Image.Image:
    """RGB or RGBA so PNG encode keeps transparency when present."""
    if image.mode in {"RGB", "RGBA"}:
        return image
    if image.mode in {"P", "LA"} or "A" in image.mode:
        return image.convert("RGBA")
    return image.convert("RGB")
