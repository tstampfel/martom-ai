"""The private chat export bucket keeps its published promises (#158).

The Privatnost page promises "Očišćeni primjeri razgovora: najduže 90 dana", so the
bucket's lifecycle rule must delete every export within 90 days. Terraform isn't run in
CI, so this reads `infra/chat_export.tf` as text: the expiry must be a literal of at
most 90 days (never a variable a tfvars file could raise), and the bucket must stay
private, encrypted and in the stack's EU region.
"""

import re
from pathlib import Path

INFRA = Path(__file__).resolve().parent.parent / "infra"
PROMISED_MAX_DAYS = 90
# S3 rounds an expiry up to the next midnight UTC and deletes asynchronously, so the rule
# must say at most 89 for an object to be gone within the promised 90 days (review R1-7).
MAX_RULE_DAYS = PROMISED_MAX_DAYS - 1


def chat_export_tf() -> str:
    text = (INFRA / "chat_export.tf").read_text(encoding="utf-8")
    # Drop comments, so a commented-out block can't satisfy a check.
    return re.sub(r"(?m)#.*$", "", text)


def block(text: str, header: str) -> str:
    """The body of the first `header { ... }` block (braces balanced)."""
    start = text.index(header)
    open_at = text.index("{", start)
    depth = 0
    for index in range(open_at, len(text)):
        depth += {"{": 1, "}": -1}.get(text[index], 0)
        if depth == 0:
            return text[open_at + 1 : index]
    raise AssertionError(f"unclosed block {header!r}")


def lifecycle() -> str:
    return block(chat_export_tf(), 'resource "aws_s3_bucket_lifecycle_configuration" "chat_export"')


def rule_blocks() -> list[str]:
    rules: list[str] = []
    text = lifecycle()
    while (start := text.find("rule {")) != -1:
        body = block(text[start:], "rule {")
        rules.append(body)
        text = text[start + len(body) :]
    return rules


def expiry_days(rule: str) -> int:
    expirations = re.findall(r"\bexpiration\s*\{([^}]*)\}", rule)
    assert len(expirations) == 1, "exactly one expiration block per rule"
    days = re.fullmatch(r"\s*days\s*=\s*(\d+)\s*", expirations[0])
    assert days is not None, "the expiry must be a plain number of days, never a variable"
    return int(days.group(1))


def test_objects_expire_within_the_promised_90_days() -> None:
    rules = rule_blocks()

    assert rules
    for rule in rules:
        assert 1 <= expiry_days(rule) <= MAX_RULE_DAYS


def test_the_whole_bucket_rule_is_enabled_and_covers_every_object() -> None:
    whole_bucket = [rule for rule in rule_blocks() if re.search(r"\bfilter\s*\{\s*\}", rule)]

    assert len(whole_bucket) == 1, "one rule with an empty filter: every object"
    assert re.search(r'\bstatus\s*=\s*"Enabled"', whole_bucket[0])
    assert expiry_days(whole_bucket[0]) <= MAX_RULE_DAYS


def test_export_requests_expire_after_a_day() -> None:
    requests = [rule for rule in rule_blocks() if 'prefix = "requests/"' in rule]

    assert len(requests) == 1
    assert expiry_days(requests[0]) == 1


def test_noncurrent_versions_expire_after_one_day() -> None:
    noncurrent = re.search(r"noncurrent_days\s*=\s*(\d+)", lifecycle())

    assert noncurrent is not None
    assert int(noncurrent.group(1)) <= 1


def test_versioning_is_never_turned_on_here() -> None:
    assert "aws_s3_bucket_versioning" not in chat_export_tf()


def test_public_access_is_blocked_and_objects_are_encrypted() -> None:
    text = chat_export_tf()
    public = block(text, 'resource "aws_s3_bucket_public_access_block" "chat_export"')
    for setting in (
        "block_public_acls",
        "block_public_policy",
        "ignore_public_acls",
        "restrict_public_buckets",
    ):
        assert re.search(rf"\b{setting}\s*=\s*true", public), setting
    assert re.search(r'sse_algorithm\s*=\s*"(AES256|aws:kms)"', text)


def test_bucket_lives_in_the_eu_region_of_the_stack() -> None:
    # No provider alias or region override: the bucket uses the stack's provider, whose
    # region is var.region (eu-central-1, variables.tf).
    assert not re.search(
        r"\b(provider|region)\s*=",
        block(chat_export_tf(), 'resource "aws_s3_bucket" "chat_export"'),
    )
    variables = (INFRA / "variables.tf").read_text(encoding="utf-8")
    region = re.search(r'variable "region"\s*\{[^}]*default\s*=\s*"([^"]+)"', variables)
    assert region is not None and region.group(1).startswith("eu-")


def test_only_the_admins_and_the_export_task_may_use_the_bucket() -> None:
    policy = block(chat_export_tf(), 'data "aws_iam_policy_document" "chat_export_bucket"')

    assert re.search(r'effect\s*=\s*"Deny"', policy)
    assert '"ArnNotEquals"' in policy
    assert "var.chat_export_admin_arns" in policy
    assert "aws_iam_role.chat_export_task.arn" in policy
    assert '"aws:SecureTransport"' in policy
