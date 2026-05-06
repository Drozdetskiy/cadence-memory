"""Tests for the `cadence-memory reindex` and `status` commands."""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path

import pytest
from typer.testing import CliRunner

from cadence_memory import cli as cli_module
from cadence_memory.cli import app
from cadence_memory.enrichment.interface import Enricher, EnrichmentResult

runner = CliRunner()


@dataclass
class _StubEnricher:
    model: str
    calls: list[tuple[str, tuple[str, ...], str, str | None]] = field(default_factory=list)

    def enrich_chunk(
        self,
        *,
        title: str,
        heading_path: tuple[str, ...],
        body: str,
        summary: str | None,
    ) -> EnrichmentResult:
        self.calls.append((title, heading_path, body, summary))
        return EnrichmentResult(
            keywords=("kw",),
            questions=(),
            alt_phrasings=(),
            model=self.model,
            generated_at="2026-01-01T00:00:00+00:00",
        )


@dataclass
class _FactoryRecorder:
    models: list[str] = field(default_factory=list)
    idle_timeouts: list[float] = field(default_factory=list)
    enrichers: list[_StubEnricher] = field(default_factory=list)

    def __call__(self, model: str, idle_timeout: float) -> Enricher:
        self.models.append(model)
        self.idle_timeouts.append(idle_timeout)
        enricher = _StubEnricher(model=model)
        self.enrichers.append(enricher)
        return enricher


@pytest.fixture
def factory_recorder(monkeypatch: pytest.MonkeyPatch) -> _FactoryRecorder:
    recorder = _FactoryRecorder()
    monkeypatch.setattr(cli_module, "_enricher_factory", recorder)
    return recorder


def _write(path: Path, content: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content, encoding="utf-8")


def _make_store(
    tmp_path: Path,
    *,
    config_yaml: str | None = None,
    annotations_yaml: str | None = None,
) -> tuple[Path, Path]:
    """Create a store dir + a project dir with a sample README.md.

    Returns (store_dir, project_dir).
    """
    store_dir = tmp_path / "store"
    project_dir = tmp_path / "proj"
    store_dir.mkdir()
    project_dir.mkdir()

    _write(project_dir / "README.md", "# Title\n\nbody\n")

    if config_yaml is None:
        config_yaml = (
            f"projects:\n  - name: proj\n    path: {project_dir}\n"
            "defaults:\n  kind: doc\n"
            "enrichment:\n  enabled: false\n"
        )
    (store_dir / "config.yaml").write_text(config_yaml, encoding="utf-8")

    if annotations_yaml is None:
        annotations_yaml = (
            "documents:\n  - id: proj:README.md\n    project: proj\n    path: README.md\n"
        )
    (store_dir / "annotations-config.yaml").write_text(annotations_yaml, encoding="utf-8")

    return store_dir, project_dir


def test_reindex_inserts_new_document(tmp_path: Path) -> None:
    store_dir, _project = _make_store(tmp_path)

    result = runner.invoke(app, ["--store", str(store_dir), "reindex"])

    assert result.exit_code == 0, result.output
    assert "inserted: 1" in result.output
    assert "content-updated: 0" in result.output
    assert "metadata-updated: 0" in result.output
    assert "deleted: 0" in result.output
    assert "skipped: 0" in result.output


def _seed_reindex(store_dir: Path) -> None:
    seed = runner.invoke(app, ["--store", str(store_dir), "reindex"])
    assert seed.exit_code == 0, seed.output


def test_reindex_noop_on_second_run(tmp_path: Path) -> None:
    store_dir, _project = _make_store(tmp_path)
    _seed_reindex(store_dir)

    result = runner.invoke(app, ["--store", str(store_dir), "reindex"])

    assert result.exit_code == 0, result.output
    assert "inserted: 0" in result.output
    assert "content-updated: 0" in result.output
    assert "metadata-updated: 0" in result.output
    assert "deleted: 0" in result.output


def test_reindex_detects_body_change(tmp_path: Path) -> None:
    store_dir, project_dir = _make_store(tmp_path)
    _seed_reindex(store_dir)

    _write(project_dir / "README.md", "# Title\n\nfresh body\n")
    result = runner.invoke(app, ["--store", str(store_dir), "reindex"])

    assert result.exit_code == 0, result.output
    assert "content-updated: 1" in result.output
    assert "inserted: 0" in result.output


def test_reindex_detects_metadata_only_change(tmp_path: Path) -> None:
    store_dir, _project = _make_store(tmp_path)
    _seed_reindex(store_dir)

    (store_dir / "annotations-config.yaml").write_text(
        "documents:\n"
        "  - id: proj:README.md\n"
        "    project: proj\n"
        "    path: README.md\n"
        "    tags: [updated]\n",
        encoding="utf-8",
    )
    result = runner.invoke(app, ["--store", str(store_dir), "reindex"])

    assert result.exit_code == 0, result.output
    assert "metadata-updated: 1" in result.output
    assert "content-updated: 0" in result.output
    assert "inserted: 0" in result.output


def test_reindex_detects_deletion(tmp_path: Path) -> None:
    store_dir, _project = _make_store(tmp_path)
    _seed_reindex(store_dir)

    (store_dir / "annotations-config.yaml").write_text("documents: []\n", encoding="utf-8")
    result = runner.invoke(app, ["--store", str(store_dir), "reindex"])

    assert result.exit_code == 0, result.output
    assert "deleted: 1" in result.output
    assert "inserted: 0" in result.output


def test_status_after_noop_reindex_prints_dry_run_zeros(tmp_path: Path) -> None:
    store_dir, _project = _make_store(tmp_path)
    _seed_reindex(store_dir)

    result = runner.invoke(app, ["--store", str(store_dir), "status"])

    assert result.exit_code == 0, result.output
    assert "inserted: 0" in result.output
    assert "content-updated: 0" in result.output
    assert "(dry-run)" in result.output


def test_status_does_not_mutate_database(tmp_path: Path) -> None:
    store_dir, project_dir = _make_store(tmp_path)
    _seed_reindex(store_dir)

    db_path = store_dir / "index.sqlite"
    _write(project_dir / "README.md", "# Title\n\ndifferent body\n")
    before = db_path.stat().st_mtime_ns

    result = runner.invoke(app, ["--store", str(store_dir), "status"])

    assert result.exit_code == 0, result.output
    assert "content-updated: 1" in result.output
    assert "(dry-run)" in result.output
    assert db_path.stat().st_mtime_ns == before


def test_reindex_verbose_lists_affected_ids(tmp_path: Path) -> None:
    store_dir, _project = _make_store(tmp_path)

    result = runner.invoke(app, ["--store", str(store_dir), "reindex", "--verbose"])

    assert result.exit_code == 0, result.output
    assert "inserted:" in result.output
    assert "  proj:README.md" in result.output


def test_reindex_without_resolvable_store_exits_one(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.delenv("CADENCE_MEMORY_DIR", raising=False)
    monkeypatch.chdir(tmp_path)

    result = runner.invoke(app, ["reindex"])

    assert result.exit_code == 1
    assert "not inside a cadence-memory store" in result.output


def test_reindex_with_malformed_config_exits_one(tmp_path: Path) -> None:
    store_dir, _project = _make_store(
        tmp_path,
        config_yaml="projects: not-a-list\n",
    )

    result = runner.invoke(app, ["--store", str(store_dir), "reindex"])

    assert result.exit_code == 1
    assert "error:" in result.output
    assert "projects" in result.output


def test_reindex_reindex_error_exits_one(tmp_path: Path) -> None:
    store_dir = tmp_path / "store"
    project_dir = tmp_path / "proj"
    store_dir.mkdir()
    project_dir.mkdir()

    (store_dir / "config.yaml").write_text(
        f"projects:\n  - name: proj\n    path: {project_dir}\ndefaults:\n  kind: doc\n",
        encoding="utf-8",
    )
    (store_dir / "annotations-config.yaml").write_text(
        "documents:\n  - id: proj:missing.md\n    project: proj\n    path: missing.md\n",
        encoding="utf-8",
    )

    result = runner.invoke(app, ["--store", str(store_dir), "reindex"])

    assert result.exit_code == 1
    assert "error:" in result.output
    assert "missing.md" in result.output


def test_status_uses_walk_up_when_no_flag(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    store_dir, _project = _make_store(tmp_path)
    monkeypatch.delenv("CADENCE_MEMORY_DIR", raising=False)
    monkeypatch.chdir(store_dir)

    result = runner.invoke(app, ["status"])

    assert result.exit_code == 0, result.output
    assert "(dry-run)" in result.output


def test_reindex_with_malformed_annotations_exits_one(tmp_path: Path) -> None:
    store_dir, _project = _make_store(
        tmp_path,
        annotations_yaml="documents: not-a-list\n",
    )

    result = runner.invoke(app, ["--store", str(store_dir), "reindex"])

    assert result.exit_code == 1
    assert "error:" in result.output


def test_reindex_with_bogus_store_flag_exits_one(tmp_path: Path) -> None:
    bogus = tmp_path / "missing"

    result = runner.invoke(app, ["--store", str(bogus), "reindex"])

    assert result.exit_code == 1
    assert "error:" in result.output
    assert "not a cadence-memory store" in result.output


def test_reindex_help_lists_enrichment_flags() -> None:
    result = runner.invoke(app, ["reindex", "--help"])

    assert result.exit_code == 0, result.output
    assert "--no-enrichment" in result.output
    assert "--enrichment-model" in result.output
    assert "--enrichment-idle-timeout" in result.output


def _make_store_with_enrichment(
    tmp_path: Path,
    *,
    enrichment_section: str | None,
    claude_section: str | None = None,
) -> tuple[Path, Path]:
    store_dir = tmp_path / "store"
    project_dir = tmp_path / "proj"
    store_dir.mkdir()
    project_dir.mkdir()
    _write(project_dir / "README.md", "# Title\n\nbody\n")

    parts = [
        f"projects:\n  - name: proj\n    path: {project_dir}\n",
        "defaults:\n  kind: doc\n",
    ]
    if claude_section is not None:
        parts.append(claude_section)
    if enrichment_section is not None:
        parts.append(enrichment_section)
    (store_dir / "config.yaml").write_text("".join(parts), encoding="utf-8")
    (store_dir / "annotations-config.yaml").write_text(
        "documents:\n  - id: proj:README.md\n    project: proj\n    path: README.md\n",
        encoding="utf-8",
    )
    return store_dir, project_dir


def test_reindex_uses_default_model_when_no_overrides(
    tmp_path: Path, factory_recorder: _FactoryRecorder
) -> None:
    store_dir, _ = _make_store_with_enrichment(tmp_path, enrichment_section=None)

    result = runner.invoke(app, ["--store", str(store_dir), "reindex"])

    assert result.exit_code == 0, result.output
    assert factory_recorder.models == ["claude-haiku-4-5"]
    assert "enriched: 2" in result.output
    assert "enrichment-cache-hits: 0" in result.output


def test_reindex_uses_config_model_when_present(
    tmp_path: Path, factory_recorder: _FactoryRecorder
) -> None:
    store_dir, _ = _make_store_with_enrichment(
        tmp_path,
        enrichment_section="enrichment:\n  model: claude-from-config\n",
    )

    result = runner.invoke(app, ["--store", str(store_dir), "reindex"])

    assert result.exit_code == 0, result.output
    assert factory_recorder.models == ["claude-from-config"]


def test_reindex_uses_claude_default_model_when_enrichment_model_unset(
    tmp_path: Path, factory_recorder: _FactoryRecorder
) -> None:
    store_dir, _ = _make_store_with_enrichment(
        tmp_path,
        enrichment_section=None,
        claude_section="claude:\n  default_model: claude-from-claude\n",
    )

    result = runner.invoke(app, ["--store", str(store_dir), "reindex"])

    assert result.exit_code == 0, result.output
    assert factory_recorder.models == ["claude-from-claude"]


def test_reindex_flag_overrides_config(
    tmp_path: Path, factory_recorder: _FactoryRecorder
) -> None:
    store_dir, _ = _make_store_with_enrichment(
        tmp_path,
        enrichment_section="enrichment:\n  model: claude-from-config\n",
    )

    result = runner.invoke(
        app,
        [
            "--store",
            str(store_dir),
            "reindex",
            "--enrichment-model",
            "claude-from-flag",
        ],
    )

    assert result.exit_code == 0, result.output
    assert factory_recorder.models == ["claude-from-flag"]


def test_reindex_no_enrichment_short_circuits_factory(
    tmp_path: Path, factory_recorder: _FactoryRecorder
) -> None:
    store_dir, _ = _make_store_with_enrichment(tmp_path, enrichment_section=None)

    result = runner.invoke(
        app, ["--store", str(store_dir), "reindex", "--no-enrichment"]
    )

    assert result.exit_code == 0, result.output
    assert factory_recorder.models == []
    assert "enriched:" not in result.output
    assert "enrichment-cache-hits:" not in result.output


def test_reindex_disabled_in_config_short_circuits_factory(
    tmp_path: Path, factory_recorder: _FactoryRecorder
) -> None:
    store_dir, _ = _make_store_with_enrichment(
        tmp_path,
        enrichment_section="enrichment:\n  enabled: false\n",
    )

    result = runner.invoke(
        app,
        [
            "--store",
            str(store_dir),
            "reindex",
            "--enrichment-model",
            "claude-from-flag",
        ],
    )

    assert result.exit_code == 0, result.output
    assert factory_recorder.models == []
    assert "enriched:" not in result.output


def test_reindex_uses_default_enrichment_idle_timeout(
    tmp_path: Path, factory_recorder: _FactoryRecorder
) -> None:
    store_dir, _ = _make_store_with_enrichment(tmp_path, enrichment_section=None)

    result = runner.invoke(app, ["--store", str(store_dir), "reindex"])

    assert result.exit_code == 0, result.output
    assert factory_recorder.idle_timeouts == [300.0]


def test_reindex_enrichment_idle_timeout_flag_overrides_default(
    tmp_path: Path, factory_recorder: _FactoryRecorder
) -> None:
    store_dir, _ = _make_store_with_enrichment(tmp_path, enrichment_section=None)

    result = runner.invoke(
        app,
        [
            "--store",
            str(store_dir),
            "reindex",
            "--enrichment-idle-timeout",
            "42.5",
        ],
    )

    assert result.exit_code == 0, result.output
    assert factory_recorder.idle_timeouts == [42.5]
