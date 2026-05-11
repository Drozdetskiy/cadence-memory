from typer.testing import CliRunner

from cadence_memory import __version__
from cadence_memory.cli import app


def test_version_command_prints_version() -> None:
    runner = CliRunner()
    result = runner.invoke(app, ["--version"])
    assert result.exit_code == 0
    assert __version__ in result.stdout


def test_package_imports() -> None:
    assert isinstance(__version__, str)
    assert __version__
