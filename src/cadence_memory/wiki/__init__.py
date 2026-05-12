"""Wiki repo locator and scaffolder re-exports."""

from cadence_memory.wiki.branch import BranchError, create_or_switch_branch
from cadence_memory.wiki.init import (
    HookInstallError,
    InstallOutcome,
    ScaffoldResult,
    install_post_commit_hook,
    scaffold_wiki,
)
from cadence_memory.wiki.locator import WikiNotFoundError, resolve_wiki_dir

__all__ = [
    "BranchError",
    "HookInstallError",
    "InstallOutcome",
    "ScaffoldResult",
    "WikiNotFoundError",
    "create_or_switch_branch",
    "install_post_commit_hook",
    "resolve_wiki_dir",
    "scaffold_wiki",
]
