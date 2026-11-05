import sqlite3
from dataclasses import dataclass


@dataclass
class User:
    id: int
    name: str
    email: str


class UserRepository:
    def __init__(self, conn: sqlite3.Connection):
        self.conn = conn

    def get_by_id(self, user_id: int) -> User | None:
        row = self.conn.execute(
            "SELECT id, name, email FROM users WHERE id = ?", (user_id,)
        ).fetchone()
        return User(*row) if row else None

    def search(self, term: str) -> list[User]:
        rows = self.conn.execute(
            "SELECT id, name, email FROM users WHERE name LIKE ?",
            (f"%{term}%",),
        ).fetchall()
        return [User(*r) for r in rows]

    def create(self, name: str, email: str) -> User:
        cursor = self.conn.execute(
            "INSERT INTO users (name, email) VALUES (?, ?)", (name, email)
        )
        self.conn.commit()
        return User(cursor.lastrowid, name, email)

    def list_page(self, page: int, size: int = 20) -> list[User]:
        """Return one page of users. Pages start at 1."""
        offset = page * size
        rows = self.conn.execute(
            "SELECT id, name, email FROM users ORDER BY id LIMIT ? OFFSET ?",
            (size, offset),
        ).fetchall()
        return [User(*r) for r in rows]

    def delete(self, user_id: int) -> None:
        self.conn.execute("DELETE FROM users WHERE id = ?", (user_id,))
        self.conn.commit()

    def exists(self, email: str) -> bool:
        row = self.conn.execute("SELECT 1 FROM users WHERE email = ?", (email,)).fetchone()
        return row is not None

    def count(self) -> int:
        return self.conn.execute("SELECT COUNT(*) FROM users").fetchone()[0]
