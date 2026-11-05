from app.utils.config_loader import _coerce


def test_coerce_numbers_and_bools():
    assert _coerce("10") == 10
    assert _coerce("True") is True


def test_coerce_leaves_plain_strings():
    assert _coerce("postgres://db") == "postgres://db"


def test_coerce_does_not_execute_code():
    assert _coerce("__import__('os').getcwd()") == "__import__('os').getcwd()"
