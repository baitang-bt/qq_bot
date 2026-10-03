"""Sticker catalog loading and marker parsing."""

from pathlib import Path

from PIL import Image

from app.stickers.catalog import StickerCatalog
from app.stickers.markers import StickerSeg, TextSeg, parse_reply_segments


def _png(path: Path) -> None:
    """Write a tiny valid PNG for catalog tests."""
    path.parent.mkdir(parents=True, exist_ok=True)
    Image.new("RGB", (8, 8), (1, 2, 3)).save(path)


def test_catalog_loads_existing_file(tmp_path: Path) -> None:
    """Known id resolves to the on-disk PNG path."""
    stickers = tmp_path / "stickers"
    _png(stickers / "facepalm.png")
    index = tmp_path / "stickers.toml"
    index.write_text(
        '[[sticker]]\nid = "facepalm"\nfile = "facepalm.png"\n'
        'tags = ["捂脸"]\ndescription = "一只猫捂脸"\n',
        encoding="utf-8",
    )
    catalog = StickerCatalog(index, stickers)
    assert catalog.path_for("facepalm") == (stickers / "facepalm.png").resolve()
    assert catalog.known_ids() == ["facepalm"]
    assert catalog.description_for_id("facepalm") == "一只猫捂脸"
    block = catalog.prompt_block()
    assert "[[sticker:facepalm]]" in block
    assert "一只猫捂脸" in block


def test_catalog_skips_missing_file(tmp_path: Path) -> None:
    """Missing files are skipped and path_for returns None."""
    stickers = tmp_path / "stickers"
    stickers.mkdir()
    index = tmp_path / "stickers.toml"
    index.write_text(
        '[[sticker]]\nid = "gone"\nfile = "gone.png"\n',
        encoding="utf-8",
    )
    catalog = StickerCatalog(index, stickers)
    assert catalog.path_for("gone") is None
    assert catalog.known_ids() == []


def test_parse_reply_segments_mixed() -> None:
    """Text and sticker markers become ordered segments."""
    segs = parse_reply_segments(
        "哈哈 [[sticker:facepalm]] 行",
        known_ids={"facepalm"},
    )
    assert segs == [
        TextSeg(text="哈哈"),
        StickerSeg(sticker_id="facepalm"),
        TextSeg(text="行"),
    ]


def test_parse_reply_segments_drops_unknown() -> None:
    """Unknown sticker ids are dropped; surrounding text remains."""
    segs = parse_reply_segments(
        "前 [[sticker:nope]] 后",
        known_ids={"facepalm"},
    )
    assert segs == [TextSeg(text="前\n后")]


def test_parse_reply_segments_with_bubble_split() -> None:
    """--- bubble splits are preserved while markers parse inside bubbles."""
    segs = parse_reply_segments(
        "第一句\n---\n[[sticker:facepalm]]\n---\n第二句",
        known_ids={"facepalm"},
    )
    assert segs == [
        TextSeg(text="第一句"),
        StickerSeg(sticker_id="facepalm"),
        TextSeg(text="第二句"),
    ]
