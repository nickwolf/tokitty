from tokitty.tray_tooltip import MAX_TOOLTIP, fit_tooltip, tooltip_text, usage_line


def test_usage_line_with_numbers_and_a_reset():
    assert usage_line("work", (42.0, 70.6, "resets 6:00 PM")) == "work: session 42%, week 71%, resets 6:00 PM"


def test_usage_line_without_a_reset_phrase():
    assert usage_line("work", (42.0, 71.0, "")) == "work: session 42%, week 71%"


def test_usage_line_without_data():
    assert usage_line("Cat 2", None) == "Cat 2: no data yet"


def test_no_rows_falls_back_to_the_app_name():
    assert tooltip_text([]) == "Tokitty"


def test_lines_that_fit_are_joined_by_newlines():
    assert fit_tooltip(["a: x", "b: y"]) == "a: x\nb: y"


def test_overflow_drops_whole_lines_and_counts_them():
    line = usage_line("work", (42.0, 71.0, "resets 6:00 PM"))
    text = fit_tooltip([line] * 5)
    assert len(text) <= MAX_TOOLTIP
    kept = text.split("\n")[:-1]
    assert kept == [line] * len(kept) and 0 < len(kept) < 5
    assert text.endswith(f"+{5 - len(kept)} more")


def test_the_limit_is_inclusive():
    lines = ["x" * 60, "y" * 66]
    assert len("\n".join(lines)) == MAX_TOOLTIP
    assert fit_tooltip(lines) == "\n".join(lines)
    assert fit_tooltip(["x" * 60, "y" * 67]).endswith("+1 more")


def test_never_over_the_limit_for_any_count_or_label_length():
    for count in range(1, 15):
        for label_len in (1, 10, 40, 100, 200):
            rows = [("L" * label_len, (99.0, 100.0, "resets in 2h 14m"))] * count
            assert len(tooltip_text(rows)) <= MAX_TOOLTIP


def test_a_single_overlong_line_is_cut_with_an_ellipsis():
    text = fit_tooltip(["z" * 300])
    assert len(text) == MAX_TOOLTIP and text.endswith("…")
