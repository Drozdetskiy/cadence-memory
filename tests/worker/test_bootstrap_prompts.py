"""Tests for the embedded bootstrap prompt templates (design2 §8)."""

from __future__ import annotations

import re
from importlib.resources import files
from string import Template
from typing import get_args

import pytest

from cadence_memory.documents.frontmatter import PageType

_CORE_PLACEHOLDERS = {"repo_name", "repo_path", "wiki_root", "head_sha", "today"}
_VALID_PAGE_TYPES = set(get_args(PageType))

_STAGE_FILES = {
    1: "bootstrap-1-data-model.txt",
    2: "bootstrap-2-routes.txt",
    3: "bootstrap-3-architecture.txt",
    4: "bootstrap-4-gaps.txt",
    5: "bootstrap-5-plans.txt",
}


def _load(name: str) -> str:
    resource = files("cadence_memory.defaults").joinpath(f"prompts/{name}")
    return resource.read_text(encoding="utf-8")


def test_stack_detect_template_loadable_and_has_no_placeholders() -> None:
    text = _load("bootstrap-stack-detect.txt")
    assert text.strip()
    assert Template(text).get_identifiers() == []


@pytest.mark.parametrize("stage", [1, 2, 3, 4])
def test_stage_1_to_4_have_exactly_core_placeholders(stage: int) -> None:
    text = _load(_STAGE_FILES[stage])
    assert set(Template(text).get_identifiers()) == _CORE_PLACEHOLDERS


def test_stage_5_has_core_placeholders_plus_plans_directive() -> None:
    text = _load(_STAGE_FILES[5])
    assert set(Template(text).get_identifiers()) == _CORE_PLACEHOLDERS | {"plans_directive"}


@pytest.mark.parametrize("stage", [1, 2, 3, 4, 5])
def test_stack_detect_text_appears_at_start_of_every_stage(stage: int) -> None:
    stack_detect = _load("bootstrap-stack-detect.txt").rstrip()
    text = _load(_STAGE_FILES[stage])
    head = text[:400]
    assert stack_detect in head, (
        f"stage {stage} prompt does not embed the stack-detect block in its first 400 chars"
    )


@pytest.mark.parametrize("stage", [1, 2, 3, 4, 5])
def test_every_stage_prompt_is_loadable_via_importlib_resources(stage: int) -> None:
    text = _load(_STAGE_FILES[stage])
    assert text.strip()


@pytest.mark.parametrize("stage", [1, 2, 3, 4])
def test_stages_1_to_4_carry_log_directive(stage: int) -> None:
    """Stages 1-4 must instruct Claude to append a `bootstrap-N` log entry."""
    text = _load(_STAGE_FILES[stage])
    assert f"bootstrap-{stage}" in text
    assert "log.md" in text


def test_stage_5_log_directive_is_driven_by_plans_directive() -> None:
    """Stage 5's bootstrap-5 log entry is appended via $plans_directive, not the static body."""
    text = _load(_STAGE_FILES[5])
    assert "$plans_directive" in text


def test_every_stage_prompt_embeds_frontmatter_template() -> None:
    """Each stage prompt must declare the v2 frontmatter fields used by parse_page."""
    required_fields = (
        "title",
        "type",
        "source",
        "project",
        "created",
        "updated",
        "tags",
        "confidence",
    )
    for stage, filename in _STAGE_FILES.items():
        text = _load(filename)
        for field in required_fields:
            assert field in text, f"stage {stage} prompt missing frontmatter field `{field}`"


def test_every_stage_prompt_mentions_wikilinks() -> None:
    """The [[wikilink]] convention must be communicated in every stage prompt."""
    for stage, filename in _STAGE_FILES.items():
        text = _load(filename)
        assert "[[" in text, f"stage {stage} prompt does not mention wikilinks"


@pytest.mark.parametrize("stage", [1, 2, 3, 4, 5])
def test_stage_prompt_type_options_are_valid_page_types(stage: int) -> None:
    """Every `type:` value listed in the prompt must be accepted by parse_page,
    otherwise pages Claude writes following the prompt will be reverted as
    FrontmatterError-failing stages."""
    text = _load(_STAGE_FILES[stage])
    match = re.search(r"^\s*type:\s*(.+)$", text, re.MULTILINE)
    assert match is not None, f"stage {stage} prompt has no `type:` line"
    options = [opt.strip() for opt in match.group(1).split("|")]
    assert options, f"stage {stage} prompt `type:` line is empty"
    unknown = [opt for opt in options if opt not in _VALID_PAGE_TYPES]
    assert not unknown, (
        f"stage {stage} prompt lists type values that PageType does not accept: "
        f"{unknown}. Valid types are: {sorted(_VALID_PAGE_TYPES)}"
    )
