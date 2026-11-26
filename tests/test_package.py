from importlib.metadata import version

import martom


def test_package_exposes_installed_version() -> None:
    assert martom.__version__ == version("martom")
