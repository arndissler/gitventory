"""Unit tests for repo change event detection and alert snapshot recording."""

from __future__ import annotations

from datetime import datetime, timezone
from unittest.mock import MagicMock, call

import pytest

from gitventory.models.repository import Repository
from gitventory.runner import CollectionRunner

_NOW = datetime(2026, 1, 15, 12, 0, 0, tzinfo=timezone.utc)


def _make_repo(repo_id: str, full_name: str, **kwargs) -> Repository:
    defaults = dict(
        id=repo_id,
        provider_id=repo_id.split(":")[-1],
        provider="github",
        source_adapter="github",
        collected_at=_NOW,
        org=full_name.split("/")[0],
        name=full_name.split("/")[1],
        full_name=full_name,
        url=f"https://github.com/{full_name}",
        visibility="private",
        is_archived=False,
        ghas_enabled=True,
        open_secret_alerts=0,
        open_code_scanning_alerts=0,
        open_dependabot_alerts=0,
    )
    defaults.update(kwargs)
    return Repository(**defaults)


def _make_runner(store):
    cfg = MagicMock()
    return CollectionRunner(cfg, store)


# ---------------------------------------------------------------------------
# _snapshot_repo_state
# ---------------------------------------------------------------------------

def test_snapshot_returns_tracked_fields():
    store = MagicMock()
    repo = _make_repo("github:1", "my-org/repo-a", visibility="private", is_archived=False, ghas_enabled=True)
    store.query.return_value = [repo]

    runner = _make_runner(store)
    state = runner._snapshot_repo_state()

    assert state == {
        "github:1": {"visibility": "private", "is_archived": False, "ghas_enabled": True}
    }


def test_snapshot_filters_by_repo():
    store = MagicMock()
    store.query.return_value = []
    runner = _make_runner(store)
    runner._snapshot_repo_state(repo="my-org/repo-a")

    store.query.assert_called_once()
    filters = store.query.call_args[0][1]
    assert filters.get("full_name") == "my-org/repo-a"


def test_snapshot_filters_by_stable_id():
    store = MagicMock()
    store.query.return_value = []
    runner = _make_runner(store)
    runner._snapshot_repo_state(repo="github:999")

    filters = store.query.call_args[0][1]
    assert filters.get("id") == "github:999"


# ---------------------------------------------------------------------------
# _record_repo_change_events
# ---------------------------------------------------------------------------

def test_records_visibility_change():
    store = MagicMock()
    repo = _make_repo("github:1", "my-org/repo-a", visibility="public")
    store.query.return_value = [repo]

    runner = _make_runner(store)
    pre_state = {"github:1": {"visibility": "private", "is_archived": False, "ghas_enabled": True}}
    count = runner._record_repo_change_events(pre_state, _NOW)

    assert count == 1
    events = store.insert_repo_change_events.call_args[0][0]
    assert len(events) == 1
    assert events[0]["field"] == "visibility"
    assert events[0]["old_value"] == "private"
    assert events[0]["new_value"] == "public"


def test_records_multiple_field_changes():
    store = MagicMock()
    repo = _make_repo("github:2", "my-org/repo-b", visibility="public", is_archived=True)
    store.query.return_value = [repo]

    runner = _make_runner(store)
    pre_state = {"github:2": {"visibility": "private", "is_archived": False, "ghas_enabled": True}}
    count = runner._record_repo_change_events(pre_state, _NOW)

    assert count == 2
    events = store.insert_repo_change_events.call_args[0][0]
    fields = {e["field"] for e in events}
    assert fields == {"visibility", "is_archived"}


def test_no_events_when_nothing_changed():
    store = MagicMock()
    repo = _make_repo("github:3", "my-org/repo-c", visibility="private", is_archived=False, ghas_enabled=True)
    store.query.return_value = [repo]

    runner = _make_runner(store)
    pre_state = {"github:3": {"visibility": "private", "is_archived": False, "ghas_enabled": True}}
    count = runner._record_repo_change_events(pre_state, _NOW)

    assert count == 0
    store.insert_repo_change_events.assert_called_once_with([])


def test_skips_newly_seen_repos():
    store = MagicMock()
    runner = _make_runner(store)
    # Empty pre_state → runner returns early; no store calls at all
    count = runner._record_repo_change_events({}, _NOW)

    assert count == 0
    store.insert_repo_change_events.assert_not_called()


def test_no_events_when_pre_state_empty():
    store = MagicMock()
    runner = _make_runner(store)
    count = runner._record_repo_change_events({}, _NOW)

    assert count == 0
    store.insert_repo_change_events.assert_not_called()


# ---------------------------------------------------------------------------
# _record_alert_snapshots
# ---------------------------------------------------------------------------

def test_alert_snapshots_written_for_all_repos():
    store = MagicMock()
    repos = [
        _make_repo("github:10", "org/repo-x", open_secret_alerts=3, open_code_scanning_alerts=0, open_dependabot_alerts=7),
        _make_repo("github:11", "org/repo-y", open_secret_alerts=0, open_code_scanning_alerts=2, open_dependabot_alerts=0),
    ]
    store.query.return_value = repos

    runner = _make_runner(store)
    count = runner._record_alert_snapshots(_NOW)

    assert count == 2
    snapshots = store.insert_alert_snapshots.call_args[0][0]
    assert len(snapshots) == 2

    by_repo = {s["repo_id"]: s for s in snapshots}
    assert by_repo["github:10"]["open_secret_alerts"] == 3
    assert by_repo["github:10"]["open_dependabot_alerts"] == 7
    assert by_repo["github:11"]["open_code_scanning_alerts"] == 2


def test_alert_snapshots_observed_date():
    store = MagicMock()
    repo = _make_repo("github:20", "org/repo-z")
    store.query.return_value = [repo]

    runner = _make_runner(store)
    runner._record_alert_snapshots(_NOW)

    snapshot = store.insert_alert_snapshots.call_args[0][0][0]
    assert snapshot["observed_date"] == "2026-01-15"


def test_alert_snapshots_zero_counts_recorded():
    store = MagicMock()
    repo = _make_repo("github:30", "org/repo-w",
                      open_secret_alerts=0,
                      open_code_scanning_alerts=0,
                      open_dependabot_alerts=0)
    store.query.return_value = [repo]

    runner = _make_runner(store)
    runner._record_alert_snapshots(_NOW)

    snapshot = store.insert_alert_snapshots.call_args[0][0][0]
    assert snapshot["open_secret_alerts"] == 0
    assert snapshot["open_code_scanning_alerts"] == 0
    assert snapshot["open_dependabot_alerts"] == 0
