"""Builds the hard eval set in evals/cases_hard/.

Each case starts from a realistic base file and applies a few edits. Most edits are harmless
noise; in bug cases, one edit plants a bug. Lines ending in `#@BUG` mark where the bug is.
The marker is stripped before the diff is written, so the model never sees it, and its line
number goes into labels.json. That way the labels can never drift out of sync with the diffs.

Run:  python evals/build_cases.py
"""
import difflib
import json
import re
from pathlib import Path

OUT = Path(__file__).resolve().parent / "cases_hard"
MARK = re.compile(r"\s*#@BUG\s*$")


# ---------- helpers ----------

def edit(base: str, *pairs: tuple[str, str]) -> str:
    """Apply (old, new) replacements. Each old string must appear exactly once in the file."""
    out = base
    for old, new in pairs:
        assert out.count(old) == 1, f"edit target not unique or missing:\n{old}"
        out = out.replace(old, new)
    return out


def build_diff(files):
    """files: list of (path, before, after). before=None means a new file.
    Returns the unified diff text and {path: [marked line numbers]}."""
    parts, marked = [], {}
    for path, before, after in files:
        lines, hits = [], []
        for i, line in enumerate(after.splitlines(), 1):
            if MARK.search(line):
                hits.append(i)
                line = MARK.sub("", line)
            lines.append(line)
        if hits:
            marked[path] = hits
        old = before.splitlines() if before is not None else []
        diff = difflib.unified_diff(
            old, lines,
            fromfile=f"a/{path}" if before is not None else "/dev/null",
            tofile=f"b/{path}",
            lineterm="", n=3,
        )
        parts.append("\n".join(diff))
    return "\n".join(parts) + "\n", marked


# ---------- base files ----------

USERS = '''import sqlite3
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
        cur = self.conn.execute(
            "INSERT INTO users (name, email) VALUES (?, ?)", (name, email)
        )
        self.conn.commit()
        return User(cur.lastrowid, name, email)

    def list_page(self, page: int, size: int = 20) -> list[User]:
        """Return one page of users. Pages start at 1."""
        offset = (page - 1) * size
        rows = self.conn.execute(
            "SELECT id, name, email FROM users ORDER BY id LIMIT ? OFFSET ?",
            (size, offset),
        ).fetchall()
        return [User(*r) for r in rows]

    def delete(self, user_id: int) -> None:
        self.conn.execute("DELETE FROM users WHERE id = ?", (user_id,))
        self.conn.commit()

    def count(self) -> int:
        return self.conn.execute("SELECT COUNT(*) FROM users").fetchone()[0]
'''

FILES = '''import shutil
import subprocess
from pathlib import Path

BASE_DIR = Path("/srv/uploads").resolve()
MAX_UPLOAD_BYTES = 10 * 1024 * 1024


class FileTooLarge(Exception):
    pass


def _safe_path(name: str) -> Path:
    full = (BASE_DIR / name).resolve()
    if not str(full).startswith(str(BASE_DIR)):
        raise PermissionError("path escapes upload directory")
    return full


def save_upload(name: str, data: bytes) -> Path:
    if len(data) > MAX_UPLOAD_BYTES:
        raise FileTooLarge(name)
    path = _safe_path(name)
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "wb") as f:
        f.write(data)
    return path


def read_file(name: str) -> bytes:
    path = _safe_path(name)
    with open(path, "rb") as f:
        return f.read()


def delete_file(name: str) -> None:
    _safe_path(name).unlink(missing_ok=True)


def archive_folder(folder: str, dest: str) -> None:
    src = _safe_path(folder)
    subprocess.run(["tar", "-czf", dest, "-C", str(src.parent), src.name], check=True)


def disk_usage() -> int:
    return shutil.disk_usage(BASE_DIR).used
'''

BILLING = '''from dataclasses import dataclass, field

TAX_RATE = 0.14
BULK_DISCOUNT = 0.10


@dataclass
class LineItem:
    sku: str
    unit_price: float
    qty: int


@dataclass
class Invoice:
    customer_id: int
    items: list[LineItem] = field(default_factory=list)


def subtotal(invoice: Invoice) -> float:
    return sum(item.unit_price * item.qty for item in invoice.items)


def discount(amount: float) -> float:
    """Orders of 100 or more get the bulk discount."""
    if amount >= 100:
        return amount * BULK_DISCOUNT
    return 0.0


def total(invoice: Invoice) -> float:
    amount = subtotal(invoice)
    amount -= discount(amount)
    return round(amount * (1 + TAX_RATE), 2)


def installments(invoice: Invoice, months: int) -> list[float]:
    if months <= 0:
        raise ValueError("months must be positive")
    amount = total(invoice)
    per_month = round(amount / months, 2)
    payments = [per_month] * months
    payments[-1] = round(amount - per_month * (months - 1), 2)
    return payments


def add_items(invoice: Invoice, items: list[LineItem]) -> Invoice:
    for item in items:
        if item.qty <= 0:
            raise ValueError(f"invalid quantity for {item.sku}")
        invoice.items.append(item)
    return invoice
'''

AUTH = '''import hashlib
import hmac
import logging
import os
from datetime import datetime, timedelta, timezone

import jwt
from fastapi import APIRouter, Depends, HTTPException
from fastapi.security import OAuth2PasswordBearer

from app.repositories.accounts import get_account

logger = logging.getLogger(__name__)
router = APIRouter()
oauth2 = OAuth2PasswordBearer(tokenUrl="login")

SECRET_KEY = os.environ["JWT_SECRET"]
ALGORITHM = "HS256"
TOKEN_TTL = timedelta(hours=1)


def hash_password(password: str, salt: bytes) -> bytes:
    return hashlib.pbkdf2_hmac("sha256", password.encode(), salt, 200_000)


def create_token(user_id: int) -> str:
    expires = datetime.now(timezone.utc) + TOKEN_TTL
    return jwt.encode({"sub": str(user_id), "exp": expires}, SECRET_KEY, algorithm=ALGORITHM)


def current_user_id(token: str = Depends(oauth2)) -> int:
    try:
        payload = jwt.decode(token, SECRET_KEY, algorithms=[ALGORITHM])
    except jwt.PyJWTError:
        raise HTTPException(status_code=401, detail="invalid token")
    return int(payload["sub"])


@router.post("/login")
def login(username: str, password: str) -> dict:
    account = get_account(username)
    if account is None:
        raise HTTPException(status_code=401, detail="invalid credentials")
    candidate = hash_password(password, account.salt)
    if not hmac.compare_digest(candidate, account.password_hash):
        logger.warning("failed login for user=%s", username)
        raise HTTPException(status_code=401, detail="invalid credentials")
    logger.info("login ok user=%s", username)
    return {"access_token": create_token(account.id), "token_type": "bearer"}
'''

JOBS = '''import asyncio
import logging
import threading
import time

import httpx

logger = logging.getLogger(__name__)

STATUS_URL = "https://jobs.internal/api/status/{job_id}"
MAX_ATTEMPTS = 3


class JobStats:
    def __init__(self):
        self._lock = threading.Lock()
        self.processed = 0
        self.failed = 0

    def record(self, ok: bool) -> None:
        with self._lock:
            if ok:
                self.processed += 1
            else:
                self.failed += 1


async def fetch_status(client: httpx.AsyncClient, job_id: str) -> str:
    resp = await client.get(STATUS_URL.format(job_id=job_id), timeout=10)
    resp.raise_for_status()
    return resp.json()["status"]


async def wait_for_job(client: httpx.AsyncClient, job_id: str, poll_seconds: float = 2.0) -> str:
    while True:
        status = await fetch_status(client, job_id)
        if status in ("done", "failed"):
            return status
        await asyncio.sleep(poll_seconds)


def run_with_retry(task, stats: JobStats) -> None:
    for attempt in range(1, MAX_ATTEMPTS + 1):
        try:
            task()
            stats.record(ok=True)
            return
        except Exception:
            logger.exception("task failed (attempt %d/%d)", attempt, MAX_ATTEMPTS)
            time.sleep(2 ** attempt)
    stats.record(ok=False)
'''

CONFIG = '''import os
from functools import lru_cache
from pathlib import Path

import yaml

CONFIG_PATH = Path(os.getenv("APP_CONFIG", "config/app.yaml"))
ENV_PREFIX = "APP_"


def _read_yaml(path: Path) -> dict:
    with open(path, encoding="utf-8") as f:
        return yaml.safe_load(f) or {}


def _apply_env_overrides(config: dict) -> dict:
    for key in config:
        env_value = os.getenv(ENV_PREFIX + key.upper())
        if env_value is not None:
            config[key] = env_value
    return config


@lru_cache(maxsize=1)
def load_config() -> dict:
    config = _read_yaml(CONFIG_PATH)
    return _apply_env_overrides(config)


def get(key: str, default=None):
    return load_config().get(key, default)
'''

MAIN = '''import logging
import os

import requests
from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware

from app.api.auth import router as auth_router

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

ALLOWED_ORIGINS = os.getenv("ALLOWED_ORIGINS", "https://app.example.com").split(",")
WEATHER_API = "https://api.weather.example.com/v1/current"

app = FastAPI(title="Orders API")
app.add_middleware(
    CORSMiddleware,
    allow_origins=ALLOWED_ORIGINS,
    allow_credentials=True,
    allow_methods=["GET", "POST"],
    allow_headers=["Authorization", "Content-Type"],
)
app.include_router(auth_router)


@app.middleware("http")
async def log_requests(request: Request, call_next):
    response = await call_next(request)
    logger.info("%s %s -> %d", request.method, request.url.path, response.status_code)
    return response


@app.get("/health")
def health() -> dict:
    return {"status": "ok"}


@app.get("/weather/{city}")
def weather(city: str) -> dict:
    resp = requests.get(WEATHER_API, params={"city": city}, timeout=5)
    resp.raise_for_status()
    return resp.json()
'''

P_USERS, P_FILES, P_BILLING = "app/repositories/users.py", "app/services/files.py", "app/services/billing.py"
P_AUTH, P_JOBS, P_CONFIG, P_MAIN = "app/api/auth.py", "app/workers/jobs.py", "app/utils/config_loader.py", "app/api/main.py"

# Harmless noise edits reused across cases, so bugs sit inside larger, realistic changes.
N_USERS_COUNT = ('    def count(self) -> int:\n',
                 '    def count(self) -> int:\n        """Total number of users, including inactive ones."""\n')
N_USERS_CURSOR = ('''        cur = self.conn.execute(
            "INSERT INTO users (name, email) VALUES (?, ?)", (name, email)
        )
        self.conn.commit()
        return User(cur.lastrowid, name, email)''', '''        cursor = self.conn.execute(
            "INSERT INTO users (name, email) VALUES (?, ?)", (name, email)
        )
        self.conn.commit()
        return User(cursor.lastrowid, name, email)''')
N_FILES_MAX = ("MAX_UPLOAD_BYTES = 10 * 1024 * 1024\n",
               "# Raised from 10 MB after support tickets about large PDF uploads.\nMAX_UPLOAD_BYTES = 20 * 1024 * 1024\n")
N_FILES_USAGE = ("def disk_usage() -> int:\n",
                 'def disk_usage() -> int:\n    """Bytes used on the volume that holds uploads."""\n')
N_BILLING_SUB = ("def subtotal(invoice: Invoice) -> float:\n",
                 'def subtotal(invoice: Invoice) -> float:\n    """Sum of line items before discount and tax."""\n')
N_BILLING_TAX = ("TAX_RATE = 0.14\n", "TAX_RATE = 0.14  # Egyptian VAT\n")
N_AUTH_TTL = ("TOKEN_TTL = timedelta(hours=1)\n", "TOKEN_TTL = timedelta(hours=2)\n")
N_AUTH_OK = ('    logger.info("login ok user=%s", username)\n', '    logger.info("login succeeded for user=%s", username)\n')
N_JOBS_ATTEMPTS = ("MAX_ATTEMPTS = 3\n", "MAX_ATTEMPTS = 5\n")
N_JOBS_URL = ('STATUS_URL = "https://jobs.internal/api/status/{job_id}"\n',
              'STATUS_URL = "https://jobs.internal/api/v2/status/{job_id}"\n')
N_CONFIG_PREFIX = ('ENV_PREFIX = "APP_"\n', '# Environment variables like APP_DATABASE_URL override keys in the YAML file.\nENV_PREFIX = "APP_"\n')
N_CONFIG_GET = ("def get(key: str, default=None):\n",
                'def get(key: str, default=None):\n    """Read one config value, falling back to default."""\n')
N_MAIN_TITLE = ('app = FastAPI(title="Orders API")\n', 'app = FastAPI(title="Orders API", version="1.4.0")\n')
N_MAIN_HEALTH = ('    return {"status": "ok"}\n', '    return {"status": "ok", "service": "orders"}\n')


# ---------- cases ----------
# Each case: files, plus for bug cases: type, category, severity, keywords.
# keywords: a finding only counts as a "strict" catch if its message mentions one of them.

CASES = {}


def bug(name, files, type_, category, severity, keywords, split="tune"):
    CASES[name] = {"files": files, "bug": {"type": type_, "category": category,
                                           "severity": severity, "keywords": keywords}, "split": split}


def clean(name, files, split="tune"):
    CASES[name] = {"files": files, "bug": None, "split": split}


# --- security bugs ---

bug("bug_sqli_search", [(P_USERS, USERS, edit(USERS, N_USERS_COUNT, N_USERS_CURSOR, ('''    def search(self, term: str) -> list[User]:
        rows = self.conn.execute(
            "SELECT id, name, email FROM users WHERE name LIKE ?",
            (f"%{term}%",),
        ).fetchall()
        return [User(*r) for r in rows]''', '''    def search(self, term: str, order_by: str = "name") -> list[User]:
        query = (
            "SELECT id, name, email FROM users "
            "WHERE name LIKE '%%%s%%' " % term  #@BUG
            + "ORDER BY " + order_by  #@BUG
        )
        rows = self.conn.execute(query).fetchall()
        return [User(*r) for r in rows]''')))],
    "sql_injection", "security", "high", ["sql", "inject", "parameter"])

bug("bug_path_traversal", [(P_FILES, FILES, edit(FILES, N_FILES_MAX, N_FILES_USAGE, (
    "def read_file(name: str) -> bytes:\n    path = _safe_path(name)\n",
    "def read_file(name: str) -> bytes:\n    path = BASE_DIR / name  #@BUG\n")))],
    "path_traversal", "security", "high", ["traversal", "path", "escape", "directory", "_safe_path"])

bug("bug_shell_archive", [(P_FILES, FILES, edit(FILES, N_FILES_USAGE, ('''def archive_folder(folder: str, dest: str) -> None:
    src = _safe_path(folder)
    subprocess.run(["tar", "-czf", dest, "-C", str(src.parent), src.name], check=True)''', '''def archive_folder(folder: str, dest: str, compress: bool = True) -> None:
    src = _safe_path(folder)
    flags = "-czf" if compress else "-cf"
    cmd = f"tar {flags} {dest} -C {src.parent} {src.name}"  #@BUG
    subprocess.run(cmd, shell=True, check=True)  #@BUG''')))],
    "command_injection", "security", "high", ["shell", "inject"], split="holdout")

bug("bug_jwt_no_verify", [(P_AUTH, AUTH, edit(AUTH, N_AUTH_TTL, (
    "        payload = jwt.decode(token, SECRET_KEY, algorithms=[ALGORITHM])\n", '''        payload = jwt.decode(  #@BUG
            token,
            SECRET_KEY,
            algorithms=[ALGORITHM],
            options={"verify_signature": False},  #@BUG
        )
''')))],
    "jwt_signature_disabled", "security", "high", ["signature", "verif"])

bug("bug_timing_compare", [(P_AUTH, AUTH, edit(AUTH, N_AUTH_OK, (
    "    if not hmac.compare_digest(candidate, account.password_hash):\n",
    "    if candidate != account.password_hash:  #@BUG\n")))],
    "timing_attack", "security", "medium", ["timing", "compare_digest", "constant"])

bug("bug_log_password", [(P_AUTH, AUTH, edit(AUTH, N_AUTH_TTL, N_AUTH_OK, (
    '        logger.warning("failed login for user=%s", username)\n',
    '        logger.warning("failed login for user=%s password=%s", username, password)  #@BUG\n')))],
    "secret_in_logs", "security", "high", ["password", "sensitive", "plaintext", "credential"], split="holdout")

bug("bug_yaml_load", [(P_CONFIG, CONFIG, edit(CONFIG, N_CONFIG_GET, (
    "        return yaml.safe_load(f) or {}\n",
    "        return yaml.load(f, Loader=yaml.Loader) or {}  #@BUG\n")))],
    "unsafe_deserialization", "security", "high", ["safe_load", "loader", "deserializ", "arbitrary", "unsafe"])

bug("bug_eval_coerce", [(P_CONFIG, CONFIG, edit(CONFIG, N_CONFIG_PREFIX, (
    "def _apply_env_overrides(config: dict) -> dict:\n", '''def _coerce(value: str):
    """Turn env strings like '10' or 'True' into Python values."""
    try:
        return eval(value)  #@BUG
    except Exception:
        return value


def _apply_env_overrides(config: dict) -> dict:
'''), ("            config[key] = env_value\n", "            config[key] = _coerce(env_value)\n")))],
    "code_injection", "security", "high", ["eval", "execut", "literal_eval", "inject"])

bug("bug_cors_wildcard", [(P_MAIN, MAIN, edit(MAIN, N_MAIN_TITLE, (
    "    allow_origins=ALLOWED_ORIGINS,\n", '    allow_origins=["*"],  #@BUG\n')))],
    "cors_misconfig", "security", "high", ["cors", "origin", "credential", "wildcard"], split="holdout")

bug("bug_tls_verify_off", [(P_MAIN, MAIN, edit(MAIN, N_MAIN_HEALTH, (
    '    resp = requests.get(WEATHER_API, params={"city": city}, timeout=5)\n',
    '    resp = requests.get(WEATHER_API, params={"city": city}, timeout=5, verify=False)  #@BUG\n')))],
    "tls_verification_disabled", "security", "high", ["verify", "tls", "ssl", "certificate"])

# --- correctness bugs ---

bug("bug_page_offset", [(P_USERS, USERS, edit(USERS, N_USERS_CURSOR, (
    "        offset = (page - 1) * size\n", "        offset = page * size  #@BUG\n"),
    ("    def count(self) -> int:\n", '''    def exists(self, email: str) -> bool:
        row = self.conn.execute("SELECT 1 FROM users WHERE email = ?", (email,)).fetchone()
        return row is not None

    def count(self) -> int:
''')))],
    "off_by_one", "correctness", "medium", ["offset", "off-by-one", "off by one", "first page", "skip"])

bug("bug_soft_delete_column", [(P_USERS, USERS, edit(USERS, N_USERS_COUNT, ('''    def delete(self, user_id: int) -> None:
        self.conn.execute("DELETE FROM users WHERE id = ?", (user_id,))
        self.conn.commit()''', '''    def delete(self, user_id: int) -> None:
        """Soft delete: keep the row but mark it inactive."""
        self.conn.execute(
            "UPDATE users SET active = 0 WHERE email = ?", (user_id,)  #@BUG
        )
        self.conn.commit()''')))],
    "wrong_column", "correctness", "high", ["email", "column", "mismatch", "wrong"], split="holdout")

bug("bug_append_mode", [(P_FILES, FILES, edit(FILES, N_FILES_MAX, (
    '    with open(path, "wb") as f:\n', '    with open(path, "ab") as f:  #@BUG\n')))],
    "wrong_file_mode", "correctness", "medium", ["append", "overwrit", "mode"])

bug("bug_mutable_default", [(P_BILLING, BILLING, edit(BILLING, N_BILLING_TAX, (
    "def add_items(invoice: Invoice, items: list[LineItem]) -> Invoice:\n", '''def merge_items(items: list[LineItem], extra: list[LineItem] = []) -> list[LineItem]:  #@BUG
    extra.extend(items)
    return extra


def add_items(invoice: Invoice, items: list[LineItem]) -> Invoice:
''')))],
    "mutable_default_argument", "correctness", "medium", ["mutable", "default", "shared"])

bug("bug_discount_boundary", [(P_BILLING, BILLING, edit(BILLING, N_BILLING_SUB, (
    "    if amount >= 100:\n", "    if amount > 100:  #@BUG\n")))],
    "boundary_condition", "correctness", "medium", [">=", "boundary", "or more", "inclusive", "exactly"])

bug("bug_round_precision", [(P_BILLING, BILLING, edit(BILLING, N_BILLING_SUB, (
    "    return round(amount * (1 + TAX_RATE), 2)\n", "    return round(amount * (1 + TAX_RATE), 1)  #@BUG\n")))],
    "rounding", "correctness", "medium", ["round", "decimal", "cent", "precision"], split="holdout")

bug("bug_floor_division", [(P_BILLING, BILLING, edit(BILLING, N_BILLING_TAX, (
    "    per_month = round(amount / months, 2)\n", "    per_month = amount // months  #@BUG\n")))],
    "integer_division", "correctness", "medium", ["//", "floor", "integer", "truncat", "division"])

bug("bug_swallowed_error", [(P_JOBS, JOBS, edit(JOBS, N_JOBS_URL, ('''        try:
            task()
            stats.record(ok=True)
            return
        except Exception:
            logger.exception("task failed (attempt %d/%d)", attempt, MAX_ATTEMPTS)
            time.sleep(2 ** attempt)''', '''        try:
            task()
        except Exception:  #@BUG
            pass  #@BUG
        stats.record(ok=True)
        return''')))],
    "swallowed_exception", "correctness", "high", ["swallow", "silent", "ignor", "pass", "success"])

bug("bug_missing_await", [(P_JOBS, JOBS, edit(JOBS, N_JOBS_ATTEMPTS, (
    "        status = await fetch_status(client, job_id)\n", "        status = fetch_status(client, job_id)  #@BUG\n")))],
    "missing_await", "correctness", "high", ["await", "coroutine"])

bug("bug_counter_race", [(P_JOBS, JOBS, edit(JOBS, N_JOBS_ATTEMPTS, ('''        with self._lock:
            if ok:
                self.processed += 1
            else:
                self.failed += 1''', '''        if ok:  #@BUG
            self.processed += 1
        else:
            self.failed += 1''')))],
    "race_condition", "correctness", "medium", ["race", "lock", "thread", "atomic"], split="holdout")

bug("bug_inverted_override", [(P_CONFIG, CONFIG, edit(CONFIG, N_CONFIG_GET, (
    "        if env_value is not None:\n", "        if env_value is None:  #@BUG\n")))],
    "inverted_condition", "correctness", "high", ["none", "invert", "condition", "never", "override"])

# --- clean cases: safe code, some of it deliberately suspicious-looking ---

clean("clean_get_many_in_clause", [
    (P_USERS, USERS, edit(USERS, ("    def search(self, term: str) -> list[User]:\n", '''    def get_many(self, user_ids: list[int]) -> dict[int, User]:
        if not user_ids:
            return {}
        placeholders = ",".join("?" for _ in user_ids)
        rows = self.conn.execute(
            f"SELECT id, name, email FROM users WHERE id IN ({placeholders})",
            tuple(user_ids),
        ).fetchall()
        return {r[0]: User(*r) for r in rows}

    def search(self, term: str) -> list[User]:
'''))),
    ("tests/test_users_repo.py", None, '''import sqlite3

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
''')])

clean("clean_archive_exclude", [
    (P_FILES, FILES, edit(FILES, ('''def archive_folder(folder: str, dest: str) -> None:
    src = _safe_path(folder)
    subprocess.run(["tar", "-czf", dest, "-C", str(src.parent), src.name], check=True)''', '''def archive_folder(folder: str, dest: str, exclude: list[str] | None = None) -> None:
    src = _safe_path(folder)
    cmd = ["tar", "-czf", dest]
    for pattern in exclude or []:
        cmd.append(f"--exclude={pattern}")
    cmd += ["-C", str(src.parent), src.name]
    subprocess.run(cmd, check=True)'''))),
    ("tests/test_files.py", None, '''from unittest.mock import patch

from app.services import files


def test_archive_folder_passes_excludes_as_separate_args():
    with patch.object(files.subprocess, "run") as run:
        files.archive_folder("reports", "/tmp/out.tgz", exclude=["*.log"])
    args = run.call_args.args[0]
    assert "--exclude=*.log" in args
    assert run.call_args.kwargs.get("shell") is None


def test_archive_folder_without_excludes():
    with patch.object(files.subprocess, "run") as run:
        files.archive_folder("reports", "/tmp/out.tgz")
    assert not any(a.startswith("--exclude") for a in run.call_args.args[0])
''')])

clean("clean_literal_eval_coerce", [
    (P_CONFIG, CONFIG, edit(CONFIG, ("import os\n", "import ast\nimport os\n"), (
        "def _apply_env_overrides(config: dict) -> dict:\n", '''def _coerce(value: str):
    """Turn env strings like '10' or 'True' into Python values."""
    try:
        return ast.literal_eval(value)
    except (ValueError, SyntaxError):
        return value


def _apply_env_overrides(config: dict) -> dict:
'''), ("            config[key] = env_value\n", "            config[key] = _coerce(env_value)\n"))),
    ("tests/test_config_loader.py", None, '''from app.utils.config_loader import _coerce


def test_coerce_numbers_and_bools():
    assert _coerce("10") == 10
    assert _coerce("True") is True


def test_coerce_leaves_plain_strings():
    assert _coerce("postgres://db") == "postgres://db"


def test_coerce_does_not_execute_code():
    assert _coerce("__import__('os').getcwd()") == "__import__('os').getcwd()"
''')])

clean("clean_verify_password_helper", [
    (P_AUTH, AUTH, edit(AUTH, (
        "def create_token(user_id: int) -> str:\n", '''def _verify_password(account, password: str) -> bool:
    candidate = hash_password(password, account.salt)
    return hmac.compare_digest(candidate, account.password_hash)


def create_token(user_id: int) -> str:
'''), ('''    candidate = hash_password(password, account.salt)
    if not hmac.compare_digest(candidate, account.password_hash):''', '''    if not _verify_password(account, password):'''))),
    ("tests/test_auth.py", None, '''from types import SimpleNamespace

from app.api.auth import _verify_password, hash_password


def make_account(password: str):
    salt = b"fixed-salt"
    return SimpleNamespace(salt=salt, password_hash=hash_password(password, salt))


def test_verify_password_accepts_correct_password():
    assert _verify_password(make_account("s3cret"), "s3cret")


def test_verify_password_rejects_wrong_password():
    assert not _verify_password(make_account("s3cret"), "nope")
''')])

clean("clean_extract_apply_tax", [
    (P_BILLING, BILLING, edit(BILLING, (
        "def total(invoice: Invoice) -> float:\n", '''def _apply_tax(amount: float) -> float:
    return amount * (1 + TAX_RATE)


def total(invoice: Invoice) -> float:
'''), ("    return round(amount * (1 + TAX_RATE), 2)\n", "    return round(_apply_tax(amount), 2)\n"))),
    ("tests/test_billing.py", None, '''from app.services.billing import Invoice, LineItem, _apply_tax, total


def test_apply_tax():
    assert _apply_tax(100) == 114.0


def test_total_applies_discount_then_tax():
    invoice = Invoice(customer_id=1, items=[LineItem("A", 50.0, 2)])
    assert total(invoice) == 102.6
''')])

clean("clean_request_timing_log", [
    (P_MAIN, MAIN, edit(MAIN, ("import logging\nimport os\n", "import logging\nimport os\nimport time\n"), (
        '''    response = await call_next(request)
    logger.info("%s %s -> %d", request.method, request.url.path, response.status_code)''', '''    start = time.perf_counter()
    response = await call_next(request)
    elapsed_ms = (time.perf_counter() - start) * 1000
    logger.info(
        "%s %s -> %d (%.1f ms)",
        request.method, request.url.path, response.status_code, elapsed_ms,
    )''')))], split="holdout")

clean("clean_stats_snapshot", [
    (P_JOBS, JOBS, edit(JOBS, (
        "                self.failed += 1\n\n\nasync def fetch_status(", '''                self.failed += 1

    def snapshot(self) -> dict[str, int]:
        with self._lock:
            return {"processed": self.processed, "failed": self.failed}


async def fetch_status('''))),
    ("tests/test_jobs.py", None, '''from app.workers.jobs import JobStats


def test_snapshot_reflects_recorded_results():
    stats = JobStats()
    stats.record(ok=True)
    stats.record(ok=False)
    assert stats.snapshot() == {"processed": 1, "failed": 1}
''')])

clean("clean_users_docstrings", [
    (P_USERS, USERS, edit(USERS, N_USERS_COUNT, (
        "    def get_by_id(self, user_id: int) -> User | None:\n",
        '    def get_by_id(self, user_id: int) -> User | None:\n        """Return the user with this id, or None if missing."""\n'), (
        "    def search(self, term: str) -> list[User]:\n",
        '    def search(self, term: str) -> list[User]:\n        """Case-insensitive substring search on name."""\n')))],
    split="holdout")

clean("clean_safe_path_relative_to", [
    (P_FILES, FILES, edit(FILES, (
        "    if not str(full).startswith(str(BASE_DIR)):\n", "    if not full.is_relative_to(BASE_DIR):\n"))),
    ("tests/test_safe_path.py", None, '''import pytest

from app.services.files import BASE_DIR, _safe_path


def test_safe_path_allows_nested_files():
    assert _safe_path("reports/q1.pdf") == BASE_DIR / "reports" / "q1.pdf"


def test_safe_path_blocks_parent_traversal():
    with pytest.raises(PermissionError):
        _safe_path("../etc/passwd")


def test_safe_path_blocks_sibling_prefix():
    with pytest.raises(PermissionError):
        _safe_path("../uploads-backup/secret.txt")
''')])

clean("clean_installments_cap", [
    (P_BILLING, BILLING, edit(BILLING, ('''    if months <= 0:
        raise ValueError("months must be positive")''', '''    if months <= 0:
        raise ValueError("months must be positive")
    if months > 36:
        raise ValueError("installment plans are limited to 36 months")'''))),
    ("tests/test_installments.py", None, '''import pytest

from app.services.billing import Invoice, LineItem, installments


def test_installments_rejects_more_than_36_months():
    with pytest.raises(ValueError):
        installments(Invoice(1, [LineItem("A", 10.0, 1)]), 37)


def test_installments_allows_36_months():
    assert len(installments(Invoice(1, [LineItem("A", 360.0, 1)]), 36)) == 36
''')], split="holdout")


# ---------- write ----------

if __name__ == "__main__":
    OUT.mkdir(exist_ok=True)
    labels = {}
    for name, case in CASES.items():
        diff, marked = build_diff(case["files"])
        filename = f"{name}.diff"
        (OUT / filename).write_text(diff, encoding="utf-8")

        if case["bug"] is None:
            assert not marked, f"{name}: clean case has a bug marker"
            labels[filename] = {"split": case["split"], "bugs": []}
        else:
            assert len(marked) == 1, f"{name}: bug case needs markers in exactly one file"
            (path, lines), = marked.items()
            labels[filename] = {"split": case["split"], "bugs": [
                {"file": path, "lines": [min(lines), max(lines)], **case["bug"]}]}

    (OUT / "labels.json").write_text(json.dumps(labels, indent=2), encoding="utf-8")
    n_bugs = sum(1 for c in CASES.values() if c["bug"])
    print(f"Wrote {len(CASES)} cases ({n_bugs} bug, {len(CASES) - n_bugs} clean) to {OUT}")
