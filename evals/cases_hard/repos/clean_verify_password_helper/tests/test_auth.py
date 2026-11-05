from types import SimpleNamespace

from app.api.auth import _verify_password, hash_password


def make_account(password: str):
    salt = b"fixed-salt"
    return SimpleNamespace(salt=salt, password_hash=hash_password(password, salt))


def test_verify_password_accepts_correct_password():
    assert _verify_password(make_account("s3cret"), "s3cret")


def test_verify_password_rejects_wrong_password():
    assert not _verify_password(make_account("s3cret"), "nope")
