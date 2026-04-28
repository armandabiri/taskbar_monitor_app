from services.resource_control.models import CleanupMode, ReleaseResult, SkipReason


def test_release_result_zero_action_summary_includes_dominant_reason() -> None:
    result = ReleaseResult(mode=CleanupMode.SYSTEM_RECLAIM.value, profile_name="Balanced")
    result.record_skip(SkipReason.VISIBLE_WINDOW, count=5)
    result.record_skip(SkipReason.KEEP_LIST, count=1)

    assert "Blocked mostly by visible-window protection (5)." in result.summary
    assert "Top block reasons: visible-window protection (5), user keep-list (1)" in result.details


def test_release_result_snapshot_summary_reports_found_selected_and_killed() -> None:
    result = ReleaseResult(
        mode=CleanupMode.SNAPSHOT_EXTRAS.value,
        profile_name="Aggressive",
        snapshot_name="Baseline",
        snapshot_extras_found=4,
        snapshot_extras_selected=2,
        snapshot_matched_count=8,
    )
    result.record_cleaned(100, "killed", "python.exe")
    result.record_cleaned(101, "killed", "notepad.exe")

    assert result.summary == "Snapshot extras: 4 found, 2 selected, 2 killed"
    assert "Snapshot: Baseline" in result.details
    assert "Snapshot live diff: matched 8, extras 4, selected 2" in result.details
