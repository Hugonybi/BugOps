import pytest

from bugops import reliability


class FakeSettings:
    def __init__(self, path, *, min_sample_size=3, merge_rate_threshold=0.8, enable_auto_approve=True):
        self.pr_outcome_store_path = path
        self.reliability_min_sample_size = min_sample_size
        self.reliability_merge_rate_threshold = merge_rate_threshold
        self.enable_reliability_auto_approve = enable_auto_approve


class FakeGitHubClient:
    def __init__(self, states: dict[int, str]):
        self._states = states

    def pr_state(self, owner, name, number):
        return self._states[number]


@pytest.fixture
def settings(tmp_path):
    return FakeSettings(tmp_path / "pr_outcomes.json")


def _seed(settings, records):
    reliability.save_records(settings, records)


def test_record_pending_appends_and_persists(settings):
    reliability.record_pending(settings, repo="myorg/backend-api", pr_number=1, pr_url="url1", risk_category="low")
    reliability.record_pending(settings, repo="myorg/backend-api", pr_number=2, pr_url="url2", risk_category="high")

    records = reliability.load_records(settings)
    assert records == [
        {"repo": "myorg/backend-api", "pr_number": 1, "pr_url": "url1", "risk_category": "low", "status": "pending"},
        {"repo": "myorg/backend-api", "pr_number": 2, "pr_url": "url2", "risk_category": "high", "status": "pending"},
    ]


def test_reconcile_is_a_noop_with_no_pending_records(settings):
    reliability.reconcile(settings, FakeGitHubClient({}))

    assert reliability.load_records(settings) == []
    assert not settings.pr_outcome_store_path.exists()


def test_reconcile_flips_pending_to_merged_or_closed_and_leaves_open_untouched(settings):
    _seed(
        settings,
        [
            {"repo": "myorg/backend-api", "pr_number": 1, "pr_url": "url1", "risk_category": "low", "status": "pending"},
            {"repo": "myorg/backend-api", "pr_number": 2, "pr_url": "url2", "risk_category": "low", "status": "pending"},
            {"repo": "myorg/backend-api", "pr_number": 3, "pr_url": "url3", "risk_category": "low", "status": "pending"},
        ],
    )
    gh = FakeGitHubClient({1: "merged", 2: "closed", 3: "open"})

    reliability.reconcile(settings, gh)

    records = {r["pr_number"]: r["status"] for r in reliability.load_records(settings)}
    assert records == {1: "merged", 2: "closed", 3: "pending"}


def test_is_reliable_false_below_min_sample_size(settings):
    _seed(
        settings,
        [
            {"repo": "r", "pr_number": 1, "pr_url": "u", "risk_category": "low", "status": "merged"},
            {"repo": "r", "pr_number": 2, "pr_url": "u", "risk_category": "low", "status": "merged"},
        ],
    )

    assert reliability.is_reliable(settings, "low") is False


def test_is_reliable_false_below_merge_rate_threshold(settings):
    _seed(
        settings,
        [
            {"repo": "r", "pr_number": 1, "pr_url": "u", "risk_category": "low", "status": "merged"},
            {"repo": "r", "pr_number": 2, "pr_url": "u", "risk_category": "low", "status": "closed"},
            {"repo": "r", "pr_number": 3, "pr_url": "u", "risk_category": "low", "status": "closed"},
        ],
    )

    assert reliability.is_reliable(settings, "low") is False


def test_is_reliable_true_once_both_thresholds_are_met(settings):
    _seed(
        settings,
        [
            {"repo": "r", "pr_number": 1, "pr_url": "u", "risk_category": "low", "status": "merged"},
            {"repo": "r", "pr_number": 2, "pr_url": "u", "risk_category": "low", "status": "merged"},
            {"repo": "r", "pr_number": 3, "pr_url": "u", "risk_category": "low", "status": "merged"},
            {"repo": "r", "pr_number": 4, "pr_url": "u", "risk_category": "low", "status": "merged"},
            {"repo": "r", "pr_number": 5, "pr_url": "u", "risk_category": "low", "status": "closed"},
        ],
    )

    assert reliability.is_reliable(settings, "low") is True


def test_is_reliable_ignores_pending_records_and_other_risk_categories(settings):
    _seed(
        settings,
        [
            {"repo": "r", "pr_number": 1, "pr_url": "u", "risk_category": "low", "status": "merged"},
            {"repo": "r", "pr_number": 2, "pr_url": "u", "risk_category": "low", "status": "merged"},
            {"repo": "r", "pr_number": 3, "pr_url": "u", "risk_category": "low", "status": "pending"},
            {"repo": "r", "pr_number": 4, "pr_url": "u", "risk_category": "high", "status": "merged"},
        ],
    )

    assert reliability.is_reliable(settings, "low") is False


def test_is_reliable_always_false_when_feature_flag_is_off(tmp_path):
    settings = FakeSettings(tmp_path / "pr_outcomes.json", enable_auto_approve=False)
    _seed(
        settings,
        [
            {"repo": "r", "pr_number": 1, "pr_url": "u", "risk_category": "low", "status": "merged"},
            {"repo": "r", "pr_number": 2, "pr_url": "u", "risk_category": "low", "status": "merged"},
            {"repo": "r", "pr_number": 3, "pr_url": "u", "risk_category": "low", "status": "merged"},
        ],
    )

    assert reliability.is_reliable(settings, "low") is False
