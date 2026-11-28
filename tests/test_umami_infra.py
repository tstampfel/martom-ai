"""Umami stores the country only (#163 review r2, Tomislav): guards on infra/umami.tf.

The behaviour itself was checked by hand against the real Umami 3.5.0 image
(docs/analytics.md, "Country only"); these tests keep the settings that give it from being
dropped by a later edit.
"""

import re
from pathlib import Path

INFRA = Path(__file__).resolve().parents[1] / "infra"
UMAMI_TF = (INFRA / "umami.tf").read_text()
VARIABLES_TF = (INFRA / "variables.tf").read_text()


def env_value(name: str) -> str:
    match = re.search(r'\{ name = "' + name + r'", value = ([^}]+?) \}', UMAMI_TF)
    assert match, f"{name} is not set on a container in umami.tf"
    return match.group(1).strip()


def start_script() -> str:
    match = re.search(r"umami_start_script\s*=\s*<<-EOT\n(.*?)\n\s*EOT", UMAMI_TF, re.S)
    assert match, "umami_start_script heredoc not found"
    return match.group(1)


def test_location_comes_from_a_country_database_never_from_headers() -> None:
    assert env_value("SKIP_LOCATION_HEADERS") == '"1"'
    assert env_value("GEOLITE_DB_PATH") == "local.umami_country_db_path"
    assert 'umami_country_db_path = "/tmp/geo/GeoLite2-Country.mmdb"' in UMAMI_TF
    assert "GeoLite2-City" not in UMAMI_TF.replace("image's GeoLite2-City", "")


def test_the_umami_container_runs_the_start_script() -> None:
    assert 'command = ["sh", "-c", local.umami_start_script]' in UMAMI_TF


def test_start_script_stops_on_a_failed_download_or_checksum_before_umami_starts() -> None:
    lines = [line.strip() for line in start_script().splitlines()]
    assert lines[0] == "set -eu"
    checksum = next(i for i, line in enumerate(lines) if "sha256sum -c -" in line)
    start = next(i for i, line in enumerate(lines) if "scripts/start-docker.sh" in line)
    assert any("curl -fsSL" in line for line in lines[:checksum])
    assert checksum < start
    assert lines[-1] == "exec sh scripts/start-docker.sh"


def test_the_country_database_is_pinned_to_a_commit_and_a_sha256() -> None:
    url = re.search(
        r'variable "umami_geo_country_db_url".*?default\s*=\s*"([^"]+)"', VARIABLES_TF, re.S
    )
    sha = re.search(
        r'variable "umami_geo_country_db_sha256".*?default\s*=\s*"([^"]+)"', VARIABLES_TF, re.S
    )
    assert url and sha
    assert re.search(r"/[0-9a-f]{40}/redist/GeoLite2-Country\.tar\.gz$", url.group(1))
    assert re.fullmatch(r"[0-9a-f]{64}", sha.group(1))


def test_settings_that_weaken_the_privacy_promise_stay_unset() -> None:
    for name in (
        "SALT_ROTATION",
        "ENABLE_CLIENT_SALT",
        "CLIENT_IP_HEADER",
        "TRUSTED_PROXIES",
        "LOG_QUERY",
    ):
        assert f'name = "{name}"' not in UMAMI_TF
