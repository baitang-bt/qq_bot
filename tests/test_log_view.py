"""Colored log HTML for the admin desktop."""

from app.admin.log_view import format_logs_html, _is_error_line


def test_format_logs_html_colors_timestamp_and_error() -> None:
    """Timestamps are green; ERROR bodies are red."""
    lines = [
        "2026-08-28 22:33:21,713 INFO app.bot vision done notes=1",
        "2026-08-28 22:33:21,710 ERROR app.vision.identify vision call failed",
        "  File \"/tmp/identify.py\", line 125, in _vision",
    ]
    html = format_logs_html(lines)
    assert "#6bbf6b" in html
    assert "22:33:21,713" in html
    assert "#f87171" in html
    assert "vision call failed" in html
    assert "File &quot;/tmp/identify.py&quot;" in html


def test_is_error_line_detects_traceback_continuation() -> None:
    """Indented traceback lines are treated as errors."""
    assert _is_error_line('  File "/tmp/x.py", line 1, in f')
    assert not _is_error_line("2026 INFO app.bot replied preview='ok'")
