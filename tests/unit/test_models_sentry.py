import pytest

from bugops.models.sentry import parse_issue_url


def test_parse_issue_url_bare():
    ref = parse_issue_url("https://my-org.sentry.io/issues/PROJECT-1z43")
    assert ref.org_slug == "my-org"
    assert ref.short_id == "PROJECT-1Z43"


def test_parse_issue_url_with_event_permalink():
    ref = parse_issue_url("https://moob-cx.sentry.io/issues/141205107/events/a97c600b6805448da7e0e6fbaa700e98/")
    assert ref.org_slug == "moob-cx"
    assert ref.short_id == "141205107"


def test_parse_issue_url_rejects_non_issue_url():
    with pytest.raises(ValueError, match="Could not parse"):
        parse_issue_url("https://my-org.sentry.io/projects/backend-api/")
