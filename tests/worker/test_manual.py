"""Tests for the manual-ingest orchestrator (design2 §11)."""

from __future__ import annotations

import subprocess
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import UTC, datetime
from importlib.resources import files
from pathlib import Path
from string import Template

from cadence_memory.config.schema import Config, WorkerConfig
from cadence_memory.executor.runner import ClaudeResult
from cadence_memory.executor.tool_sets import WIKI_READWRITE
from cadence_memory.progress.events import (
    ErrorEvent,
    PhaseEndEvent,
    PhaseStartEvent,
    ProgressEvent,
)
from cadence_memory.worker.manual import (
    _TRUNCATION_LIMIT,
    _TRUNCATION_MARKER,
    _load_default_template,
    _read_source,
    ingest_file,
    sniff_source_kind,
)

_VALID_FRONTMATTER = """---
title: Sample
type: overview
project: x
created: 2026-05-12
updated: 2026-05-12
tags: [a]
confidence: medium
---

body text here
"""

_VALID_PAGE = (
    "---\n"
    'title: "Sample"\n'
    "type: overview\n"
    "project: project-a\n"
    "created: 2026-05-12\n"
    "updated: 2026-05-12\n"
    "tags: []\n"
    "confidence: high\n"
    "---\n"
    "\n"
    "**TLDR**: hello.\n"
)


@dataclass
class _RunCall:
    prompt: str
    model: str
    allowed_tools: tuple[str, ...]
    idle_timeout_s: int
    cwd: Path | None


@dataclass
class _FakeClaudeRunner:
    """Mock ClaudeRunner that records call kwargs and lets each call mutate the wiki dir."""

    side_effects: list[Callable[[Path], ClaudeResult]] = field(default_factory=list)
    default_result: ClaudeResult = field(
        default_factory=lambda: ClaudeResult(
            success=True,
            final_text="ok",
            cost_usd=0.01,
            duration_ms=123,
            tool_call_count=1,
            error=None,
        )
    )
    calls: list[_RunCall] = field(default_factory=list)
    extra_kwargs: list[dict[str, object]] = field(default_factory=list)

    def run(
        self,
        *,
        prompt: str,
        model: str,
        allowed_tools: tuple[str, ...],
        idle_timeout_s: int,
        cwd: Path | None = None,
        **extra: object,
    ) -> ClaudeResult:
        self.calls.append(
            _RunCall(
                prompt=prompt,
                model=model,
                allowed_tools=allowed_tools,
                idle_timeout_s=idle_timeout_s,
                cwd=cwd,
            )
        )
        self.extra_kwargs.append(dict(extra))
        if self.side_effects:
            side_effect = self.side_effects.pop(0)
            assert cwd is not None
            return side_effect(cwd)
        return self.default_result


def _git(*args: str, cwd: Path) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        ["git", *args],
        cwd=cwd,
        check=True,
        text=True,
        capture_output=True,
    )


def _init_wiki(tmp_path: Path) -> Path:
    wiki = tmp_path / "wiki"
    wiki.mkdir()
    _git("init", "--initial-branch=main", cwd=wiki)
    _git("config", "user.email", "test@example.com", cwd=wiki)
    _git("config", "user.name", "Test User", cwd=wiki)
    (wiki / "index.md").write_text(
        "---\n"
        'title: "Index"\n'
        "type: overview\n"
        "project: _master\n"
        "created: 2026-05-12\n"
        "updated: 2026-05-12\n"
        "tags: []\n"
        "confidence: high\n"
        "---\n"
        "\n"
        "**TLDR**: catalog.\n"
        "## Cross-project root pages\n"
        "- [[index]]\n"
        "- [[log]]\n",
        encoding="utf-8",
    )
    (wiki / "log.md").write_text(
        "---\n"
        'title: "Activity Log"\n'
        "type: log\n"
        "project: _master\n"
        "created: 2026-05-12\n"
        "updated: 2026-05-12\n"
        "tags: []\n"
        "confidence: high\n"
        "---\n"
        "\n"
        "## [2026-05-12] init | wiki scaffolded\n"
        "\n"
        "seed entry.\n",
        encoding="utf-8",
    )
    _git("add", "-A", cwd=wiki)
    _git("commit", "-m", "seed", cwd=wiki)
    return wiki


def _config(**overrides: object) -> Config:
    base: dict[str, object] = {
        "model": "claude-sonnet-4-6",
        "idle_timeout_s": 300,
        "worker": WorkerConfig(),
        "repos": (),
    }
    base.update(overrides)
    return Config(**base)  # type: ignore[arg-type]


def _fixed_clock(year: int = 2026, month: int = 5, day: int = 12) -> Callable[[], datetime]:
    return lambda: datetime(year, month, day, 12, 0, 0, tzinfo=UTC)


def _write_source(tmp_path: Path, name: str = "article.md") -> Path:
    src = tmp_path / name
    src.write_text("plain content for ingest\n", encoding="utf-8")
    return src


def _load_manual_template() -> str:
    resource = files("cadence_memory.defaults").joinpath("prompts/manual-ingest.txt")
    return resource.read_text(encoding="utf-8")


def test_manual_prompt_template_contains_all_placeholders() -> None:
    text = _load_manual_template()
    for token in (
        "$wiki_root",
        "$source_path",
        "$source_kind",
        "$source_content",
        "$index_head",
        "$log_tail",
    ):
        assert token in text


def test_source_kind_sniffed_meeting() -> None:
    assert sniff_source_kind(Path("/tmp/2026-05-12-meeting.md")) == "meeting"
    assert sniff_source_kind(Path("/tmp/team-standup-notes.txt")) == "meeting"


def test_source_kind_sniffed_spec() -> None:
    assert sniff_source_kind(Path("/tmp/auth-spec.md")) == "spec"
    assert sniff_source_kind(Path("/tmp/rfc-7-storage.md")) == "spec"


def test_source_kind_sniffed_article_md() -> None:
    assert sniff_source_kind(Path("/tmp/some-article.md")) == "article"


def test_source_kind_sniffed_article_txt() -> None:
    assert sniff_source_kind(Path("/tmp/notes.txt")) == "article"


def test_source_kind_sniffed_other() -> None:
    assert sniff_source_kind(Path("/tmp/data.json")) == "other"
    assert sniff_source_kind(Path("/tmp/binary.bin")) == "other"


def test_read_source_md_strips_frontmatter(tmp_path: Path) -> None:
    src = tmp_path / "article.md"
    src.write_text(_VALID_FRONTMATTER, encoding="utf-8")
    out = _read_source(src)
    assert "body text here" in out
    assert "---" not in out
    assert "title: Sample" not in out


def test_read_source_md_without_frontmatter_falls_back(tmp_path: Path) -> None:
    src = tmp_path / "article.md"
    src.write_text("just markdown, no frontmatter\n", encoding="utf-8")
    out = _read_source(src)
    assert "just markdown, no frontmatter" in out


def test_read_source_txt_raw(tmp_path: Path) -> None:
    src = tmp_path / "note.txt"
    body = "plain text content\nwith multiple lines\n"
    src.write_text(body, encoding="utf-8")
    assert _read_source(src) == body


def test_read_source_huge_truncated(tmp_path: Path) -> None:
    src = tmp_path / "big.txt"
    payload = "a" * (300 * 1024)
    src.write_text(payload, encoding="utf-8")
    out = _read_source(src)
    assert out.endswith(_TRUNCATION_MARKER)
    assert len(out) == _TRUNCATION_LIMIT + len(_TRUNCATION_MARKER)


def test_renders_prompt_with_all_placeholders(tmp_path: Path) -> None:
    wiki = _init_wiki(tmp_path)
    src = _write_source(tmp_path)
    runner = _FakeClaudeRunner(
        side_effects=[
            lambda cwd: ClaudeResult(
                success=True,
                final_text="ok",
                cost_usd=0.02,
                duration_ms=200,
                tool_call_count=0,
                error=None,
            )
        ]
    )

    outcome = ingest_file(
        source_path=src,
        config=_config(),
        wiki_dir=wiki,
        runner=runner,
        clock=_fixed_clock(),
    )

    assert outcome.success
    template_text = _load_default_template()
    identifiers = set(Template(template_text).get_identifiers())
    rendered = runner.calls[0].prompt
    for ident in identifiers:
        assert f"${ident}" not in rendered, f"placeholder {ident!r} not substituted"


def test_runner_called_with_wiki_readwrite(tmp_path: Path) -> None:
    wiki = _init_wiki(tmp_path)
    src = _write_source(tmp_path)
    runner = _FakeClaudeRunner()

    ingest_file(
        source_path=src,
        config=_config(),
        wiki_dir=wiki,
        runner=runner,
        clock=_fixed_clock(),
    )

    assert runner.calls[0].allowed_tools == WIKI_READWRITE


def test_runner_called_with_config_defaults(tmp_path: Path) -> None:
    wiki = _init_wiki(tmp_path)
    src = _write_source(tmp_path)
    runner = _FakeClaudeRunner()

    ingest_file(
        source_path=src,
        config=_config(model="claude-opus-4-7", idle_timeout_s=600),
        wiki_dir=wiki,
        runner=runner,
        clock=_fixed_clock(),
    )

    assert runner.calls[0].model == "claude-opus-4-7"
    assert runner.calls[0].idle_timeout_s == 600
    assert runner.calls[0].cwd == wiki


def test_happy_path_commits_wiki(tmp_path: Path) -> None:
    wiki = _init_wiki(tmp_path)
    src = _write_source(tmp_path, name="article.md")

    def write_page(cwd: Path) -> ClaudeResult:
        (cwd / "learnings.md").write_text(_VALID_PAGE, encoding="utf-8")
        return ClaudeResult(
            success=True,
            final_text="wrote",
            cost_usd=0.03,
            duration_ms=400,
            tool_call_count=2,
            error=None,
        )

    runner = _FakeClaudeRunner(side_effects=[write_page])

    head_before = _git("rev-parse", "HEAD", cwd=wiki).stdout.strip()
    outcome = ingest_file(
        source_path=src,
        config=_config(),
        wiki_dir=wiki,
        runner=runner,
        clock=_fixed_clock(),
    )

    assert outcome.success is True
    assert outcome.wiki_commit_sha is not None
    head_after = _git("rev-parse", "HEAD", cwd=wiki).stdout.strip()
    assert head_after != head_before
    subject_line = _git("log", "-1", "--format=%s", cwd=wiki).stdout.strip()
    assert subject_line == "cadence-memory: ingest-manual article.md"
    assert outcome.cost_usd == 0.03


def test_pages_validated_after_run(tmp_path: Path) -> None:
    wiki = _init_wiki(tmp_path)
    src = _write_source(tmp_path)

    def write_bad(cwd: Path) -> ClaudeResult:
        (cwd / "stub.md").write_text("no frontmatter here\n", encoding="utf-8")
        return ClaudeResult(
            success=True,
            final_text="wrote",
            cost_usd=0.02,
            duration_ms=120,
            tool_call_count=1,
            error=None,
        )

    runner = _FakeClaudeRunner(side_effects=[write_bad])

    outcome = ingest_file(
        source_path=src,
        config=_config(),
        wiki_dir=wiki,
        runner=runner,
        clock=_fixed_clock(),
    )

    assert outcome.success is False
    assert outcome.wiki_commit_sha is None
    assert outcome.error is not None
    assert not (wiki / "stub.md").exists()
    log_text = (wiki / "log.md").read_text(encoding="utf-8")
    assert "FAILED ingest | manual" in log_text
    porcelain = subprocess.run(
        ["git", "status", "--porcelain"],
        cwd=wiki,
        check=True,
        text=True,
        capture_output=True,
    ).stdout
    assert porcelain == ""


def test_runner_failure_reverts_and_logs(tmp_path: Path) -> None:
    wiki = _init_wiki(tmp_path)
    src = _write_source(tmp_path)

    def fail(cwd: Path) -> ClaudeResult:
        return ClaudeResult(
            success=False,
            final_text="",
            cost_usd=0.01,
            duration_ms=50,
            tool_call_count=0,
            error="boom",
        )

    runner = _FakeClaudeRunner(side_effects=[fail])

    outcome = ingest_file(
        source_path=src,
        config=_config(),
        wiki_dir=wiki,
        runner=runner,
        clock=_fixed_clock(),
    )

    assert outcome.success is False
    assert outcome.error == "boom"
    assert outcome.wiki_commit_sha is None
    log_text = (wiki / "log.md").read_text(encoding="utf-8")
    assert "FAILED ingest | manual" in log_text
    assert "boom" in log_text
    last_subject = _git("log", "-1", "--format=%s", cwd=wiki).stdout.strip()
    assert last_subject.startswith("cadence-memory: ingest failure manual")


def test_ingest_file_preserves_user_dirty_file_on_failure(tmp_path: Path) -> None:
    wiki = _init_wiki(tmp_path)
    (wiki / "config.yaml").write_text("model: old\n", encoding="utf-8")
    _git("add", "config.yaml", cwd=wiki)
    _git("commit", "-m", "add config", cwd=wiki)
    (wiki / "config.yaml").write_text("model: user-edit\n", encoding="utf-8")
    src = _write_source(tmp_path)

    def fail(cwd: Path) -> ClaudeResult:
        return ClaudeResult(
            success=False,
            final_text="",
            cost_usd=None,
            duration_ms=None,
            tool_call_count=0,
            error="boom",
        )

    runner = _FakeClaudeRunner(side_effects=[fail])

    outcome = ingest_file(
        source_path=src,
        config=_config(),
        wiki_dir=wiki,
        runner=runner,
        clock=_fixed_clock(),
    )

    assert outcome.success is False
    assert (wiki / "config.yaml").read_text(encoding="utf-8") == "model: user-edit\n"


def test_ingest_file_preserves_user_dirty_file_on_frontmatter_error(tmp_path: Path) -> None:
    wiki = _init_wiki(tmp_path)
    (wiki / "config.yaml").write_text("model: old\n", encoding="utf-8")
    _git("add", "config.yaml", cwd=wiki)
    _git("commit", "-m", "add config", cwd=wiki)
    (wiki / "config.yaml").write_text("model: user-edit\n", encoding="utf-8")
    src = _write_source(tmp_path)

    def write_bad_frontmatter(cwd: Path) -> ClaudeResult:
        (cwd / "stub.md").write_text("no frontmatter here\n", encoding="utf-8")
        return ClaudeResult(
            success=True,
            final_text="wrote",
            cost_usd=0.02,
            duration_ms=120,
            tool_call_count=1,
            error=None,
        )

    runner = _FakeClaudeRunner(side_effects=[write_bad_frontmatter])

    outcome = ingest_file(
        source_path=src,
        config=_config(),
        wiki_dir=wiki,
        runner=runner,
        clock=_fixed_clock(),
    )

    assert outcome.success is False
    assert not (wiki / "stub.md").exists()
    assert (wiki / "config.yaml").read_text(encoding="utf-8") == "model: user-edit\n"


def test_validation_skips_pre_dirty_markdown(tmp_path: Path) -> None:
    wiki = _init_wiki(tmp_path)
    # User leaves a non-wiki markdown file dirty before the manual ingest.
    (wiki / "CLAUDE.md").write_text("user notes, no frontmatter\n", encoding="utf-8")
    src = _write_source(tmp_path)

    def write_good(cwd: Path) -> ClaudeResult:
        (cwd / "learnings.md").write_text(_VALID_PAGE, encoding="utf-8")
        return ClaudeResult(
            success=True,
            final_text="wrote",
            cost_usd=0.03,
            duration_ms=400,
            tool_call_count=2,
            error=None,
        )

    runner = _FakeClaudeRunner(side_effects=[write_good])

    outcome = ingest_file(
        source_path=src,
        config=_config(),
        wiki_dir=wiki,
        runner=runner,
        clock=_fixed_clock(),
    )

    assert outcome.success is True
    assert outcome.wiki_commit_sha is not None
    log_text = (wiki / "log.md").read_text(encoding="utf-8")
    assert "FAILED ingest" not in log_text


def test_manual_validation_still_catches_claude_authored_bad_md(tmp_path: Path) -> None:
    wiki = _init_wiki(tmp_path)
    src = _write_source(tmp_path)

    def write_bad(cwd: Path) -> ClaudeResult:
        (cwd / "stub.md").write_text("no frontmatter here\n", encoding="utf-8")
        return ClaudeResult(
            success=True,
            final_text="wrote",
            cost_usd=0.02,
            duration_ms=120,
            tool_call_count=1,
            error=None,
        )

    runner = _FakeClaudeRunner(side_effects=[write_bad])

    outcome = ingest_file(
        source_path=src,
        config=_config(),
        wiki_dir=wiki,
        runner=runner,
        clock=_fixed_clock(),
    )

    assert outcome.success is False
    assert outcome.error is not None
    assert "frontmatter" in outcome.error.lower() or "yaml" in outcome.error.lower()


def test_ingest_file_reverts_claude_changes_on_runner_failure(tmp_path: Path) -> None:
    wiki = _init_wiki(tmp_path)
    src = _write_source(tmp_path)

    def write_then_fail(cwd: Path) -> ClaudeResult:
        (cwd / "claude_new.md").write_text("claude output\n", encoding="utf-8")
        return ClaudeResult(
            success=False,
            final_text="",
            cost_usd=None,
            duration_ms=None,
            tool_call_count=1,
            error="boom",
        )

    runner = _FakeClaudeRunner(side_effects=[write_then_fail])

    outcome = ingest_file(
        source_path=src,
        config=_config(),
        wiki_dir=wiki,
        runner=runner,
        clock=_fixed_clock(),
    )

    assert outcome.success is False
    assert not (wiki / "claude_new.md").exists()


def test_commit_message_uses_basename(tmp_path: Path) -> None:
    wiki = _init_wiki(tmp_path)
    raw_dir = tmp_path / "raw" / "notes"
    raw_dir.mkdir(parents=True)
    src = raw_dir / "2026-05-12-meeting.md"
    src.write_text("notes from the meeting\n", encoding="utf-8")

    def write_page(cwd: Path) -> ClaudeResult:
        (cwd / "learnings.md").write_text(_VALID_PAGE, encoding="utf-8")
        return ClaudeResult(
            success=True,
            final_text="ok",
            cost_usd=0.01,
            duration_ms=10,
            tool_call_count=1,
            error=None,
        )

    runner = _FakeClaudeRunner(side_effects=[write_page])

    outcome = ingest_file(
        source_path=src,
        config=_config(),
        wiki_dir=wiki,
        runner=runner,
        clock=_fixed_clock(),
    )

    assert outcome.success is True
    subject_line = _git("log", "-1", "--format=%s", cwd=wiki).stdout.strip()
    assert subject_line == "cadence-memory: ingest-manual 2026-05-12-meeting.md"
    assert str(raw_dir) not in subject_line


def test_manual_emits_phase_start_and_end_events(tmp_path: Path) -> None:
    @dataclass
    class _RecordingLogger:
        events: list[ProgressEvent] = field(default_factory=list)

        @property
        def path(self) -> str | None:
            return None

        def print(self, fmt: str, *args: object) -> None:
            pass

        def info(self, fmt: str, *args: object) -> None:
            pass

        def warn(self, fmt: str, *args: object) -> None:
            pass

        def error(self, fmt: str, *args: object) -> None:
            pass

        def section(self, label: str) -> None:
            pass

        def log_event(self, event: ProgressEvent) -> None:
            self.events.append(event)

    wiki = _init_wiki(tmp_path)
    src = _write_source(tmp_path)
    runner = _FakeClaudeRunner()
    recording = _RecordingLogger()

    ingest_file(
        source_path=src,
        config=_config(),
        wiki_dir=wiki,
        runner=runner,
        clock=_fixed_clock(),
        logger=recording,
    )

    starts = [e for e in recording.events if isinstance(e, PhaseStartEvent)]
    ends = [e for e in recording.events if isinstance(e, PhaseEndEvent)]
    assert len(starts) == 1
    assert starts[0].phase == "manual-ingest"
    assert len(ends) == 1
    assert ends[0].phase == "manual-ingest"
    assert ends[0].result == "ok"


def test_manual_emits_error_event_on_runner_failure(tmp_path: Path) -> None:
    @dataclass
    class _RecordingLogger:
        events: list[ProgressEvent] = field(default_factory=list)

        @property
        def path(self) -> str | None:
            return None

        def print(self, fmt: str, *args: object) -> None:
            pass

        def info(self, fmt: str, *args: object) -> None:
            pass

        def warn(self, fmt: str, *args: object) -> None:
            pass

        def error(self, fmt: str, *args: object) -> None:
            pass

        def section(self, label: str) -> None:
            pass

        def log_event(self, event: ProgressEvent) -> None:
            self.events.append(event)

    wiki = _init_wiki(tmp_path)
    src = _write_source(tmp_path)

    def fail(cwd: Path) -> ClaudeResult:
        return ClaudeResult(
            success=False,
            final_text="",
            cost_usd=0.01,
            duration_ms=50,
            tool_call_count=0,
            error="boom",
        )

    runner = _FakeClaudeRunner(side_effects=[fail])
    recording = _RecordingLogger()

    outcome = ingest_file(
        source_path=src,
        config=_config(),
        wiki_dir=wiki,
        runner=runner,
        clock=_fixed_clock(),
        logger=recording,
    )

    assert outcome.success is False
    errors = [e for e in recording.events if isinstance(e, ErrorEvent)]
    assert len(errors) == 1
    assert errors[0].phase == "manual-ingest"
    assert "boom" in errors[0].message
    ends = [e for e in recording.events if isinstance(e, PhaseEndEvent)]
    assert len(ends) == 1
    assert ends[0].result == "failed"


def test_manual_emits_error_event_on_frontmatter_error(tmp_path: Path) -> None:
    @dataclass
    class _RecordingLogger:
        events: list[ProgressEvent] = field(default_factory=list)

        @property
        def path(self) -> str | None:
            return None

        def print(self, fmt: str, *args: object) -> None:
            pass

        def info(self, fmt: str, *args: object) -> None:
            pass

        def warn(self, fmt: str, *args: object) -> None:
            pass

        def error(self, fmt: str, *args: object) -> None:
            pass

        def section(self, label: str) -> None:
            pass

        def log_event(self, event: ProgressEvent) -> None:
            self.events.append(event)

    wiki = _init_wiki(tmp_path)
    src = _write_source(tmp_path)

    def write_bad_frontmatter(cwd: Path) -> ClaudeResult:
        (cwd / "stub.md").write_text("no frontmatter here\n", encoding="utf-8")
        return ClaudeResult(
            success=True,
            final_text="wrote",
            cost_usd=0.02,
            duration_ms=120,
            tool_call_count=1,
            error=None,
        )

    runner = _FakeClaudeRunner(side_effects=[write_bad_frontmatter])
    recording = _RecordingLogger()

    outcome = ingest_file(
        source_path=src,
        config=_config(),
        wiki_dir=wiki,
        runner=runner,
        clock=_fixed_clock(),
        logger=recording,
    )

    assert outcome.success is False
    errors = [e for e in recording.events if isinstance(e, ErrorEvent)]
    assert len(errors) == 1
    assert errors[0].phase == "manual-ingest"
    assert errors[0].message == "frontmatter error"
    ends = [e for e in recording.events if isinstance(e, PhaseEndEvent)]
    assert len(ends) == 1
    assert ends[0].result == "failed"
