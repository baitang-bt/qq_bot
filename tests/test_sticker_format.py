"""Keep original sticker bytes on disk; vision still uses a first frame."""

from __future__ import annotations

from io import BytesIO

from PIL import Image

from app.stickers.format import encode_for_library, frame_for_model
from app.stickers.library import encode_sticker_image


def _png() -> bytes:
    """Tiny RGB PNG."""
    buf = BytesIO()
    Image.new("RGB", (8, 8), (10, 20, 30)).save(buf, format="PNG")
    return buf.getvalue()


def _jpeg() -> bytes:
    """Tiny JPEG."""
    buf = BytesIO()
    Image.new("RGB", (8, 8), (40, 50, 60)).save(buf, format="JPEG", quality=90)
    return buf.getvalue()


def _gif(*, frames: int) -> bytes:
    """GIF with one still frame or two animation frames."""
    buf = BytesIO()
    first = Image.new("RGB", (8, 8), (255, 0, 0))
    if frames <= 1:
        first.save(buf, format="GIF")
        return buf.getvalue()
    second = Image.new("RGB", (8, 8), (0, 255, 0))
    first.save(
        buf,
        format="GIF",
        save_all=True,
        append_images=[second],
        duration=40,
        loop=0,
    )
    return buf.getvalue()


def test_passthrough_keeps_original_bytes() -> None:
    """Library save does not transcode: GIF stays GIF, JPEG stays JPEG, PNG unchanged."""
    raw_gif = _gif(frames=2)
    encoded, suffix, mime = encode_sticker_image(raw_gif, "image/jpeg")
    assert suffix == ".gif"
    assert mime == "image/gif"
    assert encoded == raw_gif
    assert encoded.startswith(b"GIF8")

    jpeg = _jpeg()
    encoded, suffix, mime = encode_for_library(jpeg, "image/gif")
    assert suffix == ".jpg"
    assert mime == "image/jpeg"
    assert encoded == jpeg
    assert encoded[:3] == b"\xff\xd8\xff"

    still = _gif(frames=1)
    encoded, suffix, mime = encode_for_library(still, "image/gif")
    assert suffix == ".gif"
    assert encoded == still


def test_png_passthrough() -> None:
    """Existing PNG bytes are not re-encoded."""
    raw = _png()
    encoded, suffix, mime = encode_for_library(raw, "image/png")
    assert suffix == ".png"
    assert mime == "image/png"
    assert encoded == raw


def test_frame_for_model_flattens_gif() -> None:
    """Vision payload uses a still PNG, not the animation stream."""
    raw = _gif(frames=2)
    frame, mime = frame_for_model(raw)
    assert mime == "image/png"
    assert frame.startswith(b"\x89PNG")
    assert not frame.startswith(b"GIF8")
