from cadence_memory import __version__


def test_package_imports() -> None:
    assert isinstance(__version__, str)
    assert __version__


def test_smoke() -> None:
    assert True
