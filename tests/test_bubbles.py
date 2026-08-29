"""Split model replies into chat bubbles."""

from app.llm.bubbles import split_reply_bubbles


def test_single_bubble_when_no_marker() -> None:
    """Plain replies stay one message."""
    assert split_reply_bubbles("就一句") == ["就一句"]


def test_split_on_marker() -> None:
    """Model-chosen --- splits into multiple bubbles."""
    text = "哈哈\n\n---\n\n确实\n\n---\n\n不过也得看情况"
    assert split_reply_bubbles(text) == ["哈哈", "确实", "不过也得看情况"]


def test_split_caps_bubble_count() -> None:
    """At most four bubbles are sent."""
    text = "a\n\n---\n\nb\n\n---\n\nc\n\n---\n\nd\n\n---\n\ne"
    assert split_reply_bubbles(text, max_bubbles=4) == ["a", "b", "c", "d"]


def test_empty_returns_empty() -> None:
    """Blank model output yields no bubbles."""
    assert split_reply_bubbles("   ") == []
