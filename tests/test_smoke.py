from shared import __version__


def test_package_importable() -> None:
    assert __version__ == "0.0.0"
