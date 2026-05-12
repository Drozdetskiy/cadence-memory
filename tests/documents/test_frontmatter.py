"""Tests for the wiki page frontmatter parser."""

from __future__ import annotations

from collections.abc import Callable
from datetime import date
from pathlib import Path

import pytest
import yaml

from cadence_memory import documents
from cadence_memory.documents import (
    Confidence,
    FrontmatterError,
    PageFrontmatter,
    PageType,
    ParsedPage,
    parse_page,
    parse_text,
)
from cadence_memory.documents.frontmatter import _normalize_text, _read_normalized

FIXTURES = Path(__file__).parent / "fixtures"


def test_dataclasses_construct() -> None:
    fm = PageFrontmatter(
        title="User",
        type="model",
        source="app/models/user.rb",
        project="app",
        created=date(2026, 5, 8),
        updated=date(2026, 5, 9),
        tags=("auth",),
        confidence="high",
    )
    page = ParsedPage(path=Path("/wiki/user.md"), frontmatter=fm, body="body")
    assert page.frontmatter.title == "User"
    assert page.frontmatter.tags == ("auth",)
    assert page.body == "body"


def test_frontmatter_error_carries_path_and_message() -> None:
    err = FrontmatterError(Path("/wiki/bad.md"), "missing frontmatter block")
    assert err.path == Path("/wiki/bad.md")
    assert err.message == "missing frontmatter block"
    assert str(err) == "/wiki/bad.md: missing frontmatter block"


def test_public_reexports() -> None:
    assert documents.PageType is PageType
    assert documents.Confidence is Confidence
    assert documents.PageFrontmatter is PageFrontmatter
    assert documents.ParsedPage is ParsedPage
    assert documents.FrontmatterError is FrontmatterError
    assert documents.parse_page is parse_page
    assert documents.parse_text is parse_text


def test_bom_stripped(tmp_path: Path) -> None:
    path = tmp_path / "bom.md"
    path.write_bytes(b"\xef\xbb\xbf---\ntitle: X\n---\nhello\n")
    text = _read_normalized(path)
    assert not text.startswith("\ufeff")
    assert text == "---\ntitle: X\n---\nhello\n"


def test_crlf_normalized(tmp_path: Path) -> None:
    path = tmp_path / "crlf.md"
    path.write_bytes(b"---\r\ntitle: X\r\n---\r\nbody\r\nline2\r\n")
    text = _read_normalized(path)
    assert "\r" not in text
    assert text == "---\ntitle: X\n---\nbody\nline2\n"


def test_lone_cr_normalized() -> None:
    assert _normalize_text("a\rb\rc") == "a\nb\nc"


def test_non_utf8_replaced(tmp_path: Path) -> None:
    path = tmp_path / "non_utf8.md"
    path.write_bytes(b"---\ntitle: X\n---\nbefore\x80\xffafter\n")
    text = _read_normalized(path)
    assert "�" in text
    assert "before" in text
    assert "after" in text


def test_minimal_valid() -> None:
    page = parse_page(FIXTURES / "valid_minimal.md")
    assert page.frontmatter.title == "User"
    assert page.frontmatter.type == "model"
    assert page.frontmatter.source is None
    assert page.frontmatter.project == "app"
    assert page.frontmatter.created == date(2026, 5, 8)
    assert page.frontmatter.updated == date(2026, 5, 8)
    assert page.frontmatter.tags == ()
    assert page.frontmatter.confidence == "high"
    assert page.body.startswith("Minimal valid page.")


def test_full_valid() -> None:
    page = parse_page(FIXTURES / "valid_full.md")
    assert page.frontmatter.title == "User"
    assert page.frontmatter.type == "model"
    assert page.frontmatter.source == "app/models/user.rb"
    assert page.frontmatter.project == "app"
    assert page.frontmatter.created == date(2026, 5, 8)
    assert page.frontmatter.updated == date(2026, 5, 9)
    assert page.frontmatter.tags == ("auth", "billing")
    assert page.frontmatter.confidence == "high"
    assert "# User" in page.body


def test_no_frontmatter_errors() -> None:
    path = FIXTURES / "no_frontmatter.md"
    with pytest.raises(FrontmatterError) as exc_info:
        parse_page(path)
    assert "missing frontmatter block" in exc_info.value.message
    assert exc_info.value.path == path


def test_missing_required_field_errors() -> None:
    path = FIXTURES / "missing_type.md"
    with pytest.raises(FrontmatterError) as exc_info:
        parse_page(path)
    assert "type" in exc_info.value.message
    assert exc_info.value.path == path


def test_unknown_type_errors() -> None:
    with pytest.raises(FrontmatterError) as exc_info:
        parse_page(FIXTURES / "bad_type.md")
    msg = exc_info.value.message
    for allowed in (
        "model",
        "service",
        "controller",
        "architecture",
        "decision",
        "pattern",
        "overview",
        "log",
        "gaps",
    ):
        assert allowed in msg


def test_bad_yaml_errors() -> None:
    with pytest.raises(FrontmatterError) as exc_info:
        parse_page(FIXTURES / "bad_yaml.md")
    assert "invalid YAML" in exc_info.value.message
    assert isinstance(exc_info.value.__cause__, yaml.YAMLError)


def test_non_string_yaml_key_errors() -> None:
    text = (
        "---\n"
        "title: User\n"
        "type: model\n"
        "project: app\n"
        "created: 2026-05-08\n"
        "updated: 2026-05-08\n"
        "tags: []\n"
        "confidence: high\n"
        "123: rogue\n"
        "---\n"
        "body\n"
    )
    with pytest.raises(FrontmatterError) as exc_info:
        parse_text(text, path=Path("/virt/page.md"))
    assert "invalid YAML" in exc_info.value.message
    assert isinstance(exc_info.value.__cause__, TypeError)


def test_unknown_extra_keys_accepted() -> None:
    text = (
        "---\n"
        "title: User\n"
        "type: model\n"
        "project: app\n"
        "created: 2026-05-08\n"
        "updated: 2026-05-08\n"
        "tags: []\n"
        "confidence: high\n"
        "foo: bar\n"
        "---\n"
        "body\n"
    )
    page = parse_text(text, path=Path("/virt/page.md"))
    assert page.frontmatter.title == "User"


def test_source_optional() -> None:
    page = parse_page(FIXTURES / "valid_minimal.md")
    assert page.frontmatter.source is None


def test_dates_iso_string() -> None:
    text = (
        "---\n"
        "title: User\n"
        "type: model\n"
        "project: app\n"
        'created: "2026-05-08"\n'
        'updated: "2026-05-09"\n'
        "tags: []\n"
        "confidence: high\n"
        "---\n"
    )
    page = parse_text(text, path=Path("/virt/page.md"))
    assert page.frontmatter.created == date(2026, 5, 8)
    assert page.frontmatter.updated == date(2026, 5, 9)


def test_dates_yaml_native() -> None:
    text = (
        "---\n"
        "title: User\n"
        "type: model\n"
        "project: app\n"
        "created: 2026-05-08\n"
        "updated: 2026-05-09\n"
        "tags: []\n"
        "confidence: high\n"
        "---\n"
    )
    page = parse_text(text, path=Path("/virt/page.md"))
    assert page.frontmatter.created == date(2026, 5, 8)
    assert page.frontmatter.updated == date(2026, 5, 9)


def test_tags_must_be_list() -> None:
    text = (
        "---\n"
        "title: User\n"
        "type: model\n"
        "project: app\n"
        "created: 2026-05-08\n"
        "updated: 2026-05-08\n"
        'tags: "auth"\n'
        "confidence: high\n"
        "---\n"
    )
    with pytest.raises(FrontmatterError) as exc_info:
        parse_text(text, path=Path("/virt/page.md"))
    assert "tags" in exc_info.value.message


def test_body_preserves_content() -> None:
    text = (
        "---\n"
        "title: User\n"
        "type: model\n"
        "project: app\n"
        "created: 2026-05-08\n"
        "updated: 2026-05-08\n"
        "tags: []\n"
        "confidence: high\n"
        "---\n"
        "\n"
        "## Heading\n"
        "\n"
        "See [[other-page]] for details.\n"
        "\n"
        "```python\n"
        "x = 1\n"
        "```\n"
    )
    page = parse_text(text, path=Path("/virt/page.md"))
    assert "## Heading" in page.body
    assert "[[other-page]]" in page.body
    assert "```python" in page.body
    assert "x = 1" in page.body


def test_file_not_found_wraps(tmp_path: Path) -> None:
    missing = tmp_path / "does_not_exist.md"
    with pytest.raises(FrontmatterError) as exc_info:
        parse_page(missing)
    assert "file not found" in exc_info.value.message
    assert exc_info.value.path == missing


def _frontmatter_text(**overrides: str) -> str:
    fields: dict[str, str] = {
        "title": "User",
        "type": "model",
        "project": "app",
        "created": "2026-05-08",
        "updated": "2026-05-08",
        "tags": "[]",
        "confidence": "high",
    }
    fields.update(overrides)
    lines = ["---"]
    for key, value in fields.items():
        lines.append(f"{key}: {value}")
    lines.append("---")
    lines.append("")
    return "\n".join(lines)


def test_title_empty_errors() -> None:
    text = _frontmatter_text(title='""')
    with pytest.raises(FrontmatterError) as exc_info:
        parse_text(text, path=Path("/virt/page.md"))
    assert exc_info.value.message == "title: must be a non-empty string"


def test_title_non_string_errors() -> None:
    text = _frontmatter_text(title="123")
    with pytest.raises(FrontmatterError) as exc_info:
        parse_text(text, path=Path("/virt/page.md"))
    assert exc_info.value.message == "title: must be a non-empty string"


def test_project_empty_errors() -> None:
    text = _frontmatter_text(project='""')
    with pytest.raises(FrontmatterError) as exc_info:
        parse_text(text, path=Path("/virt/page.md"))
    assert exc_info.value.message == "project: must be a non-empty string"


def test_confidence_invalid_errors() -> None:
    text = _frontmatter_text(confidence="bogus")
    with pytest.raises(FrontmatterError) as exc_info:
        parse_text(text, path=Path("/virt/page.md"))
    msg = exc_info.value.message
    assert "confidence" in msg
    assert "bogus" in msg
    for level in ("high", "medium", "low"):
        assert level in msg


def test_source_empty_errors() -> None:
    text = _frontmatter_text(source='""')
    with pytest.raises(FrontmatterError) as exc_info:
        parse_text(text, path=Path("/virt/page.md"))
    assert exc_info.value.message == "source: must be a non-empty string or absent"


def test_source_non_string_errors() -> None:
    text = _frontmatter_text(source="42")
    with pytest.raises(FrontmatterError) as exc_info:
        parse_text(text, path=Path("/virt/page.md"))
    assert exc_info.value.message == "source: must be a non-empty string or absent"


def test_date_datetime_rejected() -> None:
    text = _frontmatter_text(created="2026-05-08T10:00:00")
    with pytest.raises(FrontmatterError) as exc_info:
        parse_text(text, path=Path("/virt/page.md"))
    assert exc_info.value.message == "created: must be a date (got datetime)"


def test_date_invalid_iso_errors() -> None:
    text = _frontmatter_text(created='"2026-13-40"')
    with pytest.raises(FrontmatterError) as exc_info:
        parse_text(text, path=Path("/virt/page.md"))
    msg = exc_info.value.message
    assert "created" in msg
    assert "invalid ISO-8601" in msg


def test_date_wrong_type_errors() -> None:
    text = _frontmatter_text(created="12345")
    with pytest.raises(FrontmatterError) as exc_info:
        parse_text(text, path=Path("/virt/page.md"))
    assert exc_info.value.message == "created: must be a date or ISO-8601 string"


def test_tags_non_string_item_errors() -> None:
    text = _frontmatter_text(tags="[123, hello]")
    with pytest.raises(FrontmatterError) as exc_info:
        parse_text(text, path=Path("/virt/page.md"))
    assert exc_info.value.message == "tags[0]: must be a non-empty string"


def test_tags_empty_string_item_errors() -> None:
    text = _frontmatter_text(tags='["", hello]')
    with pytest.raises(FrontmatterError) as exc_info:
        parse_text(text, path=Path("/virt/page.md"))
    assert exc_info.value.message == "tags[0]: must be a non-empty string"


@pytest.mark.parametrize(
    "missing",
    ["title", "type", "project", "created", "updated", "tags", "confidence"],
)
def test_missing_each_required_field_errors(missing: str) -> None:
    fields: dict[str, str] = {
        "title": "User",
        "type": "model",
        "project": "app",
        "created": "2026-05-08",
        "updated": "2026-05-08",
        "tags": "[]",
        "confidence": "high",
    }
    del fields[missing]
    lines = ["---"]
    for key, value in fields.items():
        lines.append(f"{key}: {value}")
    lines.append("---")
    lines.append("")
    text = "\n".join(lines)
    with pytest.raises(FrontmatterError) as exc_info:
        parse_text(text, path=Path("/virt/page.md"))
    assert exc_info.value.message == f"missing required field: {missing}"


_VALID_FRONTMATTER_BODY = (
    b"title: User\n"
    b"type: model\n"
    b"project: app\n"
    b"created: 2026-05-08\n"
    b"updated: 2026-05-08\n"
    b"tags: []\n"
    b"confidence: high\n"
)


@pytest.fixture
def byte_fixture(tmp_path: Path) -> Callable[[str, bytes], Path]:
    def _write(name: str, data: bytes) -> Path:
        path = tmp_path / name
        path.write_bytes(data)
        return path

    return _write


def test_parse_page_crlf_normalized(byte_fixture: Callable[[str, bytes], Path]) -> None:
    data = (
        b"---\r\n"
        b"title: User\r\n"
        b"type: model\r\n"
        b"project: app\r\n"
        b"created: 2026-05-08\r\n"
        b"updated: 2026-05-08\r\n"
        b"tags: []\r\n"
        b"confidence: high\r\n"
        b"---\r\n"
        b"body line\r\nline 2\r\n"
    )
    page = parse_page(byte_fixture("crlf.md", data))
    assert "\r" not in page.body
    assert page.frontmatter.title == "User"
    assert "body line\nline 2" in page.body


def test_parse_page_bom_stripped(byte_fixture: Callable[[str, bytes], Path]) -> None:
    data = b"\xef\xbb\xbf---\n" + _VALID_FRONTMATTER_BODY + b"---\nhello\n"
    page = parse_page(byte_fixture("bom.md", data))
    assert page.frontmatter.title == "User"
    assert page.body.rstrip("\n") == "hello"


def test_parse_page_non_utf8_replaced(byte_fixture: Callable[[str, bytes], Path]) -> None:
    data = b"---\n" + _VALID_FRONTMATTER_BODY + b"---\nbefore\x80\xffafter\n"
    page = parse_page(byte_fixture("non_utf8.md", data))
    assert page.frontmatter.title == "User"
    assert page.frontmatter.type == "model"
    assert "before" in page.body
    assert "after" in page.body
    assert "�" in page.body
