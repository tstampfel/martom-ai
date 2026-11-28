"""Umami stores the country only (#163 review r4, Tomislav): guards on infra/umami.tf.

The country comes from CloudFront's CloudFront-Viewer-Country header; Umami's bundled
GeoLite2-City lookup is switched off. The behaviour itself was checked by hand against the
real Umami 3.5.0 image (docs/analytics.md, "Country only"); these tests keep the settings
that give it from being dropped by a later edit.
"""

import re
from pathlib import Path

INFRA = Path(__file__).resolve().parents[1] / "infra"
UMAMI_TF = (INFRA / "umami.tf").read_text()

# Location headers Umami 3.5 reads (src/lib/detect.ts, PROVIDER_HEADERS). Only the
# CloudFront country header may reach it; a region or city header would be stored.
LOCATION_HEADERS = {
    "cf-ipcountry",
    "cf-region-code",
    "cf-ipcity",
    "x-vercel-ip-country",
    "x-vercel-ip-country-region",
    "x-vercel-ip-city",
    "cloudfront-viewer-country",
    "cloudfront-viewer-country-region",
    "cloudfront-viewer-city",
    "eo-ipcountry",
    "eo-region-code",
    "eo-ipcity",
    "x-umami-client-country",
    "x-umami-client-region",
    "x-umami-client-city",
}


def env_value(name: str) -> str:
    match = re.search(r'\{ name = "' + name + r'", value = ([^}]+?) \}', UMAMI_TF)
    assert match, f"{name} is not set on a container in umami.tf"
    return match.group(1).strip()


def forwarded_headers() -> list[str]:
    block = re.search(r"umami_forwarded_headers = \[(.*?)\n  \]", UMAMI_TF, re.S)
    assert block, "local.umami_forwarded_headers not found"
    return re.findall(r'^\s*"([^"]+)"', block.group(1), re.M)


def resource_block(kind: str, name: str) -> str:
    match = re.search(rf'resource "{kind}" "{name}" \{{\n(.*?)\n\}}\n', UMAMI_TF, re.S)
    assert match, f"{kind}.{name} not found"
    return match.group(1)


def test_the_only_location_header_forwarded_is_the_cloudfront_country() -> None:
    headers = {h.lower() for h in forwarded_headers()}
    assert headers & LOCATION_HEADERS == {"cloudfront-viewer-country"}
    # CloudFront forwards nothing it isn't told to: an allowlist, never all viewer headers.
    assert not any(
        h.startswith("cloudfront-") and h != "cloudfront-viewer-country" for h in headers
    )


def test_the_umami_distribution_uses_the_allowlist_policy_on_every_dynamic_path() -> None:
    policy = resource_block("aws_cloudfront_origin_request_policy", "umami")
    assert 'header_behavior = "whitelist"' in policy
    assert "items = local.umami_forwarded_headers" in policy
    assert 'cookie_behavior = "none"' in policy

    distribution = resource_block("aws_cloudfront_distribution", "umami")
    assert "all_viewer" not in distribution
    default = re.search(r"default_cache_behavior \{(.*?)\n  \}", distribution, re.S)
    assert default
    assert "origin_request_policy_id   = aws_cloudfront_origin_request_policy.umami.id" in (
        default.group(1)
    )
    # The only other behaviour is the cached tracker file, which forwards no viewer headers.
    ordered = re.findall(r"ordered_cache_behavior \{(.*?)\n  \}", distribution, re.S)
    assert len(ordered) == 1
    assert 'path_pattern           = "/script.js"' in ordered[0]
    assert "origin_request_policy_id" not in ordered[0]


def test_the_bundled_city_lookup_is_switched_off() -> None:
    # A path that doesn't exist: without the country header Umami answers 500 and stores
    # nothing, instead of looking the IP up in GeoLite2-City.
    assert env_value("GEOLITE_DB_PATH") == "local.umami_no_city_db_path"
    assert 'umami_no_city_db_path = "/nonexistent/no-city-lookup.mmdb"' in UMAMI_TF
    assert ".mmdb" not in UMAMI_TF.replace("/nonexistent/no-city-lookup.mmdb", "")


def test_the_official_image_runs_unchanged() -> None:
    umami_task = resource_block("aws_ecs_task_definition", "umami")
    assert "command" not in umami_task
    assert "image     = var.umami_image" in umami_task


def test_settings_that_weaken_the_privacy_promise_stay_unset() -> None:
    for name in (
        "SKIP_LOCATION_HEADERS",  # would ignore the CloudFront country (and use no lookup)
        "SALT_ROTATION",
        "ENABLE_CLIENT_SALT",
        "CLIENT_IP_HEADER",
        "TRUSTED_PROXIES",
        "LOG_QUERY",
        "CLOUD_MODE",
    ):
        assert f'name = "{name}"' not in UMAMI_TF
