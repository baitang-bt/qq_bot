"""Sticker catalog loading and marker parsing."""

from pathlib import Path

from PIL import Image

from app.stickers.catalog import StickerCatalog
from app.stickers.library import content_md5
from app.stickers.markers import StickerSeg, TextSeg, parse_reply_segments


def _png(path: Path) -> None:
    """Write a tiny valid PNG for catalog tests."""
    path.parent.mkdir(parents=True, exist_ok=True)
    Image.new("RGB", (8, 8), (1, 2, 3)).save(path)


def _gif(path: Path) -> None:
    """Write a tiny GIF so folder-scan tests cover animated stickers."""
    path.parent.mkdir(parents=True, exist_ok=True)
    Image.new("RGB", (8, 8), (9, 8, 7)).save(path, format="GIF")


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
    digest = content_md5((stickers / "facepalm.png").read_bytes())
    assert catalog.id_for_md5(digest) == "facepalm"
    block = catalog.prompt_block()
    assert "[[sticker:facepalm]]" in block
    assert "一只猫捂脸" in block
    assert "不要把对方刚发的那张原样打回去" in block


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


def test_catalog_scans_dropped_jpg_and_gif(tmp_path: Path) -> None:
    """Files dropped into the folder are usable without a toml row."""
    stickers = tmp_path / "stickers"
    stickers.mkdir()
    Image.new("RGB", (8, 8), (4, 5, 6)).save(stickers / "loading.jpg")
    _gif(stickers / "spin.gif")
    index = tmp_path / "stickers.toml"
    index.write_text("# empty index\n", encoding="utf-8")
    catalog = StickerCatalog(index, stickers)
    assert catalog.path_for("loading") == (stickers / "loading.jpg").resolve()
    assert catalog.path_for("spin") == (stickers / "spin.gif").resolve()
    ids = catalog.known_ids()
    assert "loading" in ids
    assert "spin" in ids
    block = catalog.prompt_block()
    assert "[[sticker:loading]]" in block
    assert "[[sticker:spin]]" in block


def test_catalog_picks_up_new_file_without_toml_change(tmp_path: Path) -> None:
    """Adding a GIF after first load is visible on the next lookup."""
    stickers = tmp_path / "stickers"
    stickers.mkdir()
    index = tmp_path / "stickers.toml"
    index.write_text("# empty\n", encoding="utf-8")
    catalog = StickerCatalog(index, stickers)
    assert catalog.known_ids() == []
    _gif(stickers / "wave.gif")
    assert catalog.path_for("wave") is not None
    assert "wave" in catalog.known_ids()


def test_parse_reply_segments_sticker_then_text() -> None:
    """A marker on its own line becomes a sticker segment, not visible text."""
    segs = parse_reply_segments(
        "[[sticker:facepalm]]\n\n行吧，给你一个。",
        known_ids={"facepalm", "loading"},
    )
    assert segs == [
        StickerSeg(sticker_id="facepalm"),
        TextSeg(text="行吧，给你一个。"),
    ]


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


def test_parse_reply_segments_accepts_memory_form() -> None:
    """Models copying [发送表情:id] from history still become sticker segments."""
    from app.stickers.markers import memory_text_for_segments

    segs = parse_reply_segments(
        "[发送表情:pixel_attack_helicopter]\n这个也收了",
        known_ids={"pixel_attack_helicopter"},
    )
    assert segs == [
        StickerSeg(sticker_id="pixel_attack_helicopter"),
        TextSeg(text="这个也收了"),
    ]
    assert memory_text_for_segments(segs) == (
        "[[sticker:pixel_attack_helicopter]]\n这个也收了"
    )


def test_drop_echoed_stickers_removes_inbound_copy() -> None:
    """Outbound copy of this turn's inbound sticker is stripped."""
    from app.stickers.markers import drop_echoed_stickers

    segs = parse_reply_segments(
        "嗯 [[sticker:confused]]",
        known_ids={"confused", "facepalm"},
    )
    dropped = drop_echoed_stickers(segs, {"confused"})
    assert dropped == [TextSeg(text="嗯")]
