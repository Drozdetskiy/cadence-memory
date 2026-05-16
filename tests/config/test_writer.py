"""Round-trip YAML writer tests."""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass, field
from pathlib import Path

import pytest

from cadence_memory.config.errors import ConfigError
from cadence_memory.config.writer import (
    add_repo,
    dump_yaml_roundtrip,
    list_repos,
    load_yaml_roundtrip,
    remove_repo,
)
from cadence_memory.progress.events import ProgressEvent


@dataclass
class _RecordingLogger:
    warnings: list[str] = field(default_factory=list)

    @property
    def path(self) -> str | None:
        return None

    def print(self, fmt: str, *args: object) -> None:
        pass

    def info(self, fmt: str, *args: object) -> None:
        pass

    def warn(self, fmt: str, *args: object) -> None:
        self.warnings.append(fmt % args if args else fmt)

    def error(self, fmt: str, *args: object) -> None:
        pass

    def section(self, label: str) -> None:
        pass

    def log_event(self, event: ProgressEvent) -> None:
        pass


def test_writer_preserves_top_and_inline_comments(tmp_path: Path) -> None:
    src = tmp_path / "config.yaml"
    src.write_text(
        """\
# top-level comment
model: claude-sonnet-4-6
# inside repos
repos:
  - name: project-a  # inline comment
    url: git@github.com:org/a.git
  # between-entry comment
  - name: project-b
    url: git@github.com:org/b.git
""",
        encoding="utf-8",
    )

    doc = load_yaml_roundtrip(src)
    out = tmp_path / "out.yaml"
    dump_yaml_roundtrip(out, doc)
    rendered = out.read_text(encoding="utf-8")

    assert "# top-level comment" in rendered
    assert "# inside repos" in rendered
    assert "# inline comment" in rendered
    assert "# between-entry comment" in rendered


def test_writer_preserves_double_and_single_quotes(tmp_path: Path) -> None:
    src = tmp_path / "config.yaml"
    src.write_text(
        """\
model: "claude-sonnet-4-6"
worker:
  noise_subject_patterns:
    - 'release [0-9]+'
    - "wip"
""",
        encoding="utf-8",
    )

    doc = load_yaml_roundtrip(src)
    out = tmp_path / "out.yaml"
    dump_yaml_roundtrip(out, doc)
    rendered = out.read_text(encoding="utf-8")

    assert '"claude-sonnet-4-6"' in rendered
    assert "'release [0-9]+'" in rendered
    assert '"wip"' in rendered


def test_writer_atomic_write_no_temp_left_behind(tmp_path: Path) -> None:
    src = tmp_path / "config.yaml"
    src.write_text("model: claude-sonnet-4-6\n", encoding="utf-8")

    doc = load_yaml_roundtrip(src)
    target = tmp_path / "out.yaml"
    dump_yaml_roundtrip(target, doc)

    assert target.exists()
    siblings = list(tmp_path.iterdir())
    assert not any(p.name.endswith(".tmp") for p in siblings), siblings


def _write(path: Path, body: str) -> None:
    path.write_text(body, encoding="utf-8")


def test_add_appends_to_empty_repos(tmp_path: Path) -> None:
    cfg = tmp_path / "config.yaml"
    _write(cfg, "model: claude-sonnet-4-6\n")

    add_repo(cfg, name="alpha", url="git@github.com:org/alpha.git")

    rendered = cfg.read_text(encoding="utf-8")
    assert "name: alpha" in rendered
    assert "url: git@github.com:org/alpha.git" in rendered
    assert "branch: main" in rendered


def test_add_appends_to_existing_repos(tmp_path: Path) -> None:
    cfg = tmp_path / "config.yaml"
    _write(
        cfg,
        """\
model: claude-sonnet-4-6
repos:
  - name: alpha
    url: git@github.com:org/alpha.git
""",
    )

    add_repo(cfg, name="beta", url="git@github.com:org/beta.git")

    doc = load_yaml_roundtrip(cfg)
    names = [entry["name"] for entry in doc["repos"]]
    assert names == ["alpha", "beta"]


def test_add_preserves_unrelated_keys_byte_for_byte(tmp_path: Path) -> None:
    cfg = tmp_path / "config.yaml"
    body = """\
# top comment
model: "claude-sonnet-4-6"
worker:
  poll_interval_s: 3600
  noise_subject_patterns:
    - 'release [0-9]+'
repos:
  - name: alpha
    url: git@github.com:org/alpha.git
"""
    _write(cfg, body)

    def _non_repos_hash(text: str) -> str:
        lines: list[str] = []
        in_repos = False
        for line in text.splitlines():
            if line.startswith("repos:"):
                in_repos = True
                continue
            if in_repos:
                if line and not line.startswith((" ", "\t", "#")):
                    in_repos = False
                else:
                    continue
            lines.append(line)
        return hashlib.sha256("\n".join(lines).encode("utf-8")).hexdigest()

    before = _non_repos_hash(cfg.read_text(encoding="utf-8"))
    add_repo(cfg, name="beta", url="git@github.com:org/beta.git")
    after = _non_repos_hash(cfg.read_text(encoding="utf-8"))
    assert before == after

    rendered = cfg.read_text(encoding="utf-8")
    assert "# top comment" in rendered
    assert '"claude-sonnet-4-6"' in rendered
    assert "'release [0-9]+'" in rendered


def test_add_duplicate_name_raises_config_error(tmp_path: Path) -> None:
    cfg = tmp_path / "config.yaml"
    _write(
        cfg,
        """\
repos:
  - name: alpha
    url: git@github.com:org/alpha.git
""",
    )

    with pytest.raises(ConfigError, match="alpha"):
        add_repo(cfg, name="alpha", url="git@github.com:org/other.git")


@pytest.mark.parametrize("bad_name", ["Foo", "with_underscore", "-leading-dash", "", "a/b"])
def test_add_invalid_slug_raises_config_error(tmp_path: Path, bad_name: str) -> None:
    cfg = tmp_path / "config.yaml"
    _write(cfg, "model: claude-sonnet-4-6\n")

    with pytest.raises(ConfigError, match="slug"):
        add_repo(cfg, name=bad_name, url="git@github.com:org/x.git")


def test_add_empty_url_does_not_corrupt_config(tmp_path: Path) -> None:
    cfg = tmp_path / "config.yaml"
    original = "model: claude-sonnet-4-6\n"
    _write(cfg, original)

    with pytest.raises(ConfigError, match="url"):
        add_repo(cfg, name="alpha", url="")

    assert cfg.read_text(encoding="utf-8") == original


def test_add_empty_model_does_not_corrupt_config(tmp_path: Path) -> None:
    cfg = tmp_path / "config.yaml"
    original = "model: claude-sonnet-4-6\n"
    _write(cfg, original)

    with pytest.raises(ConfigError, match="model"):
        add_repo(
            cfg,
            name="alpha",
            url="git@github.com:org/alpha.git",
            model="",
        )

    assert cfg.read_text(encoding="utf-8") == original


def test_add_passes_strict_loader_after_write(tmp_path: Path) -> None:
    cfg = tmp_path / "config.yaml"
    _write(cfg, "model: claude-sonnet-4-6\n")

    add_repo(
        cfg,
        name="alpha",
        url="git@github.com:org/alpha.git",
        branch="dev",
        start_commit="abc123",
        model="claude-opus-4-7",
    )

    from cadence_memory.config.loader import load_config

    loaded = load_config(cfg)
    assert len(loaded.repos) == 1
    repo = loaded.repos[0]
    assert repo.name == "alpha"
    assert repo.branch == "dev"
    assert repo.start_commit == "abc123"
    assert repo.model == "claude-opus-4-7"


def test_remove_existing_repo(tmp_path: Path) -> None:
    cfg = tmp_path / "config.yaml"
    _write(
        cfg,
        """\
repos:
  - name: alpha
    url: git@github.com:org/alpha.git
  - name: beta
    url: git@github.com:org/beta.git
""",
    )

    remove_repo(cfg, name="alpha")
    doc = load_yaml_roundtrip(cfg)
    names = [entry["name"] for entry in doc["repos"]]
    assert names == ["beta"]


def test_remove_unknown_name_raises_config_error(tmp_path: Path) -> None:
    cfg = tmp_path / "config.yaml"
    _write(
        cfg,
        """\
repos:
  - name: alpha
    url: git@github.com:org/alpha.git
""",
    )

    with pytest.raises(ConfigError, match="no repo named"):
        remove_repo(cfg, name="missing")


def test_remove_from_empty_repos_list_raises_config_error(tmp_path: Path) -> None:
    cfg = tmp_path / "config.yaml"
    _write(cfg, "model: claude-sonnet-4-6\nrepos: []\n")

    with pytest.raises(ConfigError, match="no repo named"):
        remove_repo(cfg, name="missing")


def test_list_empty_repos_table_renders_header_only(tmp_path: Path) -> None:
    cfg = tmp_path / "config.yaml"
    _write(cfg, "model: claude-sonnet-4-6\n")

    out = list_repos(cfg, format="table")

    assert out.splitlines() == ["NAME  URL  BRANCH  MODEL  START"]


def test_list_empty_repos_json_returns_empty_list(tmp_path: Path) -> None:
    cfg = tmp_path / "config.yaml"
    _write(cfg, "model: claude-sonnet-4-6\n")

    out = list_repos(cfg, format="json")

    assert json.loads(out) == []


def test_list_table_columns_and_rows(tmp_path: Path) -> None:
    cfg = tmp_path / "config.yaml"
    _write(
        cfg,
        """\
repos:
  - name: alpha
    url: git@github.com:org/alpha.git
  - name: beta
    url: git@github.com:org/beta.git
    branch: dev
    model: claude-opus-4-7
    start_commit: abc123
  - name: gamma
    url: git@github.com:org/gamma.git
""",
    )

    out = list_repos(cfg, format="table")
    for header in ("NAME", "URL", "BRANCH", "MODEL", "START"):
        assert header in out
    for name in ("alpha", "beta", "gamma"):
        assert name in out
    assert "<default>" in out
    assert "abc123" in out


def test_list_json_parses_back_to_dict_list(tmp_path: Path) -> None:
    cfg = tmp_path / "config.yaml"
    _write(
        cfg,
        """\
repos:
  - name: alpha
    url: git@github.com:org/alpha.git
  - name: beta
    url: git@github.com:org/beta.git
    branch: dev
""",
    )

    out = list_repos(cfg, format="json")
    data = json.loads(out)
    assert isinstance(data, list)
    assert [entry["name"] for entry in data] == ["alpha", "beta"]
    assert data[1]["branch"] == "dev"
    assert data[0]["model"] is None


def test_list_invalid_format_raises_value_error(tmp_path: Path) -> None:
    cfg = tmp_path / "config.yaml"
    _write(cfg, "model: claude-sonnet-4-6\n")

    with pytest.raises(ValueError, match="format"):
        list_repos(cfg, format="csv")  # type: ignore[arg-type]


def test_add_repo_surfaces_unknown_key_warning_exactly_once(tmp_path: Path) -> None:
    cfg = tmp_path / "config.yaml"
    _write(
        cfg,
        """\
model: claude-sonnet-4-6
raw_auto_ingest: true
""",
    )
    recording = _RecordingLogger()

    add_repo(cfg, name="alpha", url="git@github.com:org/alpha.git", logger=recording)

    matching = [w for w in recording.warnings if "raw_auto_ingest" in w]
    assert len(matching) == 1, recording.warnings


def test_remove_repo_surfaces_unknown_key_warning_exactly_once(tmp_path: Path) -> None:
    cfg = tmp_path / "config.yaml"
    _write(
        cfg,
        """\
raw_auto_ingest: true
repos:
  - name: alpha
    url: git@github.com:org/alpha.git
""",
    )
    recording = _RecordingLogger()

    remove_repo(cfg, name="alpha", logger=recording)

    matching = [w for w in recording.warnings if "raw_auto_ingest" in w]
    assert len(matching) == 1, recording.warnings


def test_list_repos_surfaces_unknown_key_warning(tmp_path: Path) -> None:
    cfg = tmp_path / "config.yaml"
    _write(
        cfg,
        """\
raw_auto_ingest: true
repos:
  - name: alpha
    url: git@github.com:org/alpha.git
""",
    )
    recording = _RecordingLogger()

    list_repos(cfg, format="json", logger=recording)

    matching = [w for w in recording.warnings if "raw_auto_ingest" in w]
    assert len(matching) == 1, recording.warnings
