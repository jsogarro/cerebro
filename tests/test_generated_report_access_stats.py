from src.models.db.generated_report import GeneratedReport


def test_access_stats_timestamp_is_naive_for_the_naive_column() -> None:
    report = GeneratedReport(access_count=0)

    report.update_access_stats()

    assert report.access_count == 1
    assert report.last_accessed_at is not None
    assert report.last_accessed_at.tzinfo is None


def test_generation_timestamps_are_naive_for_the_naive_columns() -> None:
    started = GeneratedReport()
    started.mark_generation_started()
    completed = GeneratedReport()
    completed.mark_generation_started()
    completed.mark_generation_completed(["markdown"], {"markdown": 1})
    failed = GeneratedReport(generation_errors=[])
    failed.mark_generation_failed("boom")

    stamps = [
        started.generation_started_at,
        completed.generation_completed_at,
        failed.generation_completed_at,
    ]
    assert all(s is not None and s.tzinfo is None for s in stamps), stamps
    assert completed.generation_time_seconds is not None
