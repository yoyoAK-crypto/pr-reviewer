import sqlite3

from app.repositories.users import UserRepository


def make_repo():
    conn = sqlite3.connect(":memory:")
    conn.execute("CREATE TABLE users (id INTEGER PRIMARY KEY, name TEXT, email TEXT)")
    repo = UserRepository(conn)
    repo.create("ana", "ana@example.com")
    repo.create("bo", "bo@example.com")
    return repo


def test_get_many_returns_matching_users():
    users = make_repo().get_many([1, 2])
    assert set(users) == {1, 2}
    assert users[1].name == "ana"


def test_get_many_empty_list():
    assert make_repo().get_many([]) == {}
