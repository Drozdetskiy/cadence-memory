"""Tests for the v2 config dataclass shape and defaults."""

from __future__ import annotations

import dataclasses
from pathlib import Path

import pytest

from cadence_memory.config import Config, ConfigError, ProgressConfig, RepoConfig, WorkerConfig


def test_config_is_frozen_with_slots() -> None:
    assert Config.__dataclass_params__.frozen is True
    assert hasattr(Config, "__slots__")


def test_repoconfig_is_frozen_with_slots() -> None:
    assert RepoConfig.__dataclass_params__.frozen is True
    assert hasattr(RepoConfig, "__slots__")


def test_workerconfig_is_frozen_with_slots() -> None:
    assert WorkerConfig.__dataclass_params__.frozen is True
    assert hasattr(WorkerConfig, "__slots__")


def test_config_fields_present() -> None:
    names = {f.name for f in dataclasses.fields(Config)}
    assert names == {
        "model",
        "budget_usd",
        "idle_timeout_s",
        "worker",
        "repos",
        "progress",
    }


def test_repoconfig_fields_present() -> None:
    names = {f.name for f in dataclasses.fields(RepoConfig)}
    assert names == {
        "name",
        "url",
        "branch",
        "start_commit",
        "model",
        "budget_usd",
    }


def test_workerconfig_fields_present() -> None:
    names = {f.name for f in dataclasses.fields(WorkerConfig)}
    assert names == {
        "poll_interval_s",
        "noise_subject_patterns",
        "skip_subject_patterns",
        "max_commits_per_run",
        "stop_on_failure",
    }


def test_config_defaults() -> None:
    cfg = Config()
    assert cfg.model == "claude-sonnet-4-6"
    assert cfg.budget_usd == 0.50
    assert cfg.idle_timeout_s == 300
    assert cfg.worker == WorkerConfig()
    assert cfg.repos == ()
    assert cfg.progress == ProgressConfig()


def test_workerconfig_defaults() -> None:
    w = WorkerConfig()
    assert w.poll_interval_s == 3600
    assert w.noise_subject_patterns == ()
    assert w.skip_subject_patterns == ()
    assert w.max_commits_per_run == 50
    assert w.stop_on_failure is True


def test_repoconfig_defaults() -> None:
    r = RepoConfig(name="a", url="git@example.com:org/a.git")
    assert r.branch == "main"
    assert r.start_commit is None
    assert r.model is None
    assert r.budget_usd is None


def test_config_is_immutable() -> None:
    cfg = Config()
    with pytest.raises(dataclasses.FrozenInstanceError):
        cfg.model = "x"  # type: ignore[misc]


def test_repoconfig_is_immutable() -> None:
    r = RepoConfig(name="a", url="u")
    with pytest.raises(dataclasses.FrozenInstanceError):
        r.name = "b"  # type: ignore[misc]


def test_workerconfig_is_immutable() -> None:
    w = WorkerConfig()
    with pytest.raises(dataclasses.FrozenInstanceError):
        w.poll_interval_s = 7  # type: ignore[misc]


def test_configerror_carries_path_and_message() -> None:
    err = ConfigError(Path("/tmp/x.yaml"), "broken")
    assert err.path == Path("/tmp/x.yaml")
    assert err.message == "broken"
    assert str(err) == "/tmp/x.yaml: broken"
