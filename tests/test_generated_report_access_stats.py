from src.models.db.generated_report import GeneratedReport


def test_access_stats_timestamp_is_naive_for_the_naive_column() -> None:
    report = GeneratedReport(access_count=0)

    report.update_access_stats()

    assert report.access_count == 1
    assert report.last_accessed_at is not None
    assert report.last_accessed_at.tzinfo is None
