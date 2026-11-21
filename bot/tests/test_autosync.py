from src.handlers.admin import render_autosync


def test_disabled():
    assert render_autosync({"hours": 0}).startswith("🔄 Автосинк вимкнено.")


def test_enabled_before_first_run():
    assert "перший — за кілька хвилин" in render_autosync({"hours": 24})


def test_last_result_and_next_run():
    text = render_autosync(
        {
            "hours": 24,
            "last_started": "2026-11-22T01:00:00+00:00",
            "last_finished": "2026-11-22T01:09:00+00:00",
            "last_result": {
                "notion": {"changed": 3, "removed": 1, "failed": 0},
                "gdrive": {"changed": 5, "removed": 0, "failed": 10, "ocr_failed": 1},
                "drives": {"error": "DriveApiEmpty()"},
            },
        }
    )
    # час у Києві: UTC+2 у листопаді
    assert "наступний ≈ 23.11 03:00" in text
    assert "завершено 22.11 03:09" in text
    assert "✓ Notion: змінено 3, прибрано 1, збоїв 0" in text
    assert "✓ Drive: змінено 5, прибрано 0, збоїв 10, OCR не вдався 1" in text
    assert "✗ Права дисків: DriveApiEmpty()" in text
    assert "ще триває" not in text


def test_running_sync_keeps_previous_result():
    text = render_autosync(
        {
            "hours": 24,
            "last_started": "2026-11-23T01:00:00+00:00",
            "last_finished": "2026-11-22T01:09:00+00:00",
            "last_result": {"notion": {}, "gdrive": {}, "drives": {"ok": True}},
        }
    )
    assert "Синк від 23.11 03:00 ще триває." in text
    assert "завершено 22.11 03:09" in text
