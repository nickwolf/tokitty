import pytest

from tokitty.mood import compute_mood


@pytest.mark.parametrize(
    "session_pct,weekly_pct,expected_mood,expected_tag",
    [
        (0, 0, "sleeping", "5h"),
        (24.9, 0, "sleeping", "5h"),
        (25.0, 0, "content", "5h"),
        (49.9, 0, "content", "5h"),
        (50.0, 0, "interested", "5h"),
        (74.9, 0, "interested", "5h"),
        (75.0, 0, "alert", "5h"),
        (89.9, 0, "alert", "5h"),
        (90.0, 0, "panicked", "5h"),
        (99.9, 0, "panicked", "5h"),
        (10, 60, "interested", "7d"),
    ],
)
def test_compute_mood_thresholds(session_pct, weekly_pct, expected_mood, expected_tag):
    mood, tag = compute_mood(session_pct, weekly_pct)

    assert mood == expected_mood
    assert tag == expected_tag
