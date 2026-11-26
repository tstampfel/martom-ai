import martom


def test_package_exposes_installed_version() -> None:
    assert martom.__version__ == "0.1.0"
