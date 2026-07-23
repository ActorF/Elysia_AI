from config.settings import parse_bool


def test_parse_bool_accepts_true_values() -> None:
    true_values = [
        "1",
        "true",
        "TRUE",
        " yes ",
        "on",
    ]

    for value in true_values:
        assert parse_bool(value) is True


def test_parse_bool_returns_false_for_other_values() -> None:
    false_values = [
        "0",
        "false",
        "FALSE",
        " no ",
        "off",
        "",
    ]

    for value in false_values:
        assert parse_bool(value) is False