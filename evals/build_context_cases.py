"""Builds the cross-file eval set in evals/cases_context/.

Every bug here is INVISIBLE from the diff alone: the changed lines look fine on their own,
and the problem only shows up in code elsewhere in the repo (usually a caller in another file).
These are the cases retrieval is supposed to help with.

The clean cases change the same functions in backwards-compatible ways, so they measure
whether extra context makes the reviewers invent problems.

Run:  python evals/build_context_cases.py
"""
import json
from pathlib import Path

from build_cases import (BASE_FILES, P_AUTH, P_BILLING, P_CONFIG, P_FILES, P_JOBS, P_USERS,
                         AUTH, BILLING, CONFIG, FILES, JOBS, USERS, build_diff, edit, write_repo)

OUT = Path(__file__).resolve().parent / "cases_context"

# Two extra files that CALL into the rest of the repo. They never appear in a diff;
# reviewers can only learn about them through retrieval.
P_ORDERS = "app/api/orders.py"
ORDERS = '''from app.repositories.users import UserRepository
from app.services.billing import Invoice, installments, total
from app.services.files import read_file
from app.utils.config_loader import get


def invoice_summary(repo: UserRepository, user_id: int, invoice: Invoice) -> dict:
    user = repo.get_by_id(user_id)
    return {"customer": user.email, "total": total(invoice)}


def receipt_text(invoice: Invoice) -> str:
    amount = total(invoice)
    return f"Total due: {amount:.2f} EGP"


def monthly_plan(invoice: Invoice, months: int) -> dict:
    payments = installments(invoice, months)
    return {"first_payment": payments[0], "count": len(payments)}


def attachment_text(name: str) -> str:
    return read_file(name).decode("utf-8")


def first_users(repo: UserRepository) -> list[str]:
    return [u.email for u in repo.list_page(1)]


def request_timeout() -> int:
    return int(get("request_timeout", 30))
'''

P_SCHEDULER = "app/workers/scheduler.py"
SCHEDULER = '''from app.api.auth import create_token
from app.workers.jobs import JobStats, run_with_retry

stats = JobStats()


def run_nightly(tasks) -> dict:
    for task in tasks:
        run_with_retry(task, stats)
    return {"processed": stats.processed, "failed": stats.failed}


def mark_external_result(ok: bool) -> None:
    stats.record(ok=ok)


def service_token() -> str:
    return create_token(0)
'''

REPO = {**BASE_FILES, P_ORDERS: ORDERS, P_SCHEDULER: SCHEDULER}

CASES = {}


def bug(name, files, type_, severity, keywords, affected):
    CASES[name] = {"files": files, "bug": {"type": type_, "category": "cross_file", "severity": severity,
                                           "keywords": keywords, "affected": affected}}


def clean(name, files):
    CASES[name] = {"files": files, "bug": None}


# --- bugs: the change is fine locally, but breaks a caller in another file ---

bug("ctx_get_by_id_returns_dict", [(P_USERS, USERS, edit(USERS, (
    "        return User(*row) if row else None\n",
    '        return dict(zip(("id", "name", "email"), row)) if row else None  #@BUG\n'),
    ("    def get_by_id(self, user_id: int) -> User | None:\n", "    def get_by_id(self, user_id: int) -> dict | None:\n")))],
    "return_type_change", "high", ["invoice_summary", ".email", "attribute", "caller", "orders"],
    "app/api/orders.py::invoice_summary")

bug("ctx_total_returns_string", [(P_BILLING, BILLING, edit(BILLING, (
    "def total(invoice: Invoice) -> float:\n", "def total(invoice: Invoice) -> str:\n"), (
    "    return round(amount * (1 + TAX_RATE), 2)\n",
    '    return f"{amount * (1 + TAX_RATE):.2f}"  #@BUG\n')))],
    "return_type_change", "high", ["receipt_text", "installments", "caller", "format", "orders"],
    "app/api/orders.py::receipt_text")

bug("ctx_installments_returns_dict", [(P_BILLING, BILLING, edit(BILLING, (
    "def installments(invoice: Invoice, months: int) -> list[float]:\n",
    "def installments(invoice: Invoice, months: int) -> dict[int, float]:\n"), (
    "    return payments\n", "    return {month: p for month, p in enumerate(payments, start=1)}  #@BUG\n")))],
    "return_type_change", "high", ["monthly_plan", "payments[0]", "keyerror", "caller", "index"],
    "app/api/orders.py::monthly_plan")

bug("ctx_read_file_returns_str", [(P_FILES, FILES, edit(FILES, (
    "def read_file(name: str) -> bytes:\n", "def read_file(name: str) -> str:\n"), (
    '    with open(path, "rb") as f:\n        return f.read()\n',
    '    with open(path, "r", encoding="utf-8") as f:  #@BUG\n        return f.read()\n')))],
    "return_type_change", "high", ["attachment_text", "decode", "caller", "bytes"],
    "app/api/orders.py::attachment_text")

bug("ctx_list_page_zero_based", [(P_USERS, USERS, edit(USERS, (
    '        """Return one page of users. Pages start at 1."""\n        offset = (page - 1) * size\n',
    '        """Return one page of users. Pages start at 0."""\n        offset = page * size  #@BUG\n')))],
    "contract_change", "medium", ["first_users", "list_page(1)", "caller", "skip", "first page"],
    "app/api/orders.py::first_users")

bug("ctx_config_get_no_default", [(P_CONFIG, CONFIG, edit(CONFIG, (
    "def get(key: str, default=None):\n    return load_config().get(key, default)\n",
    'def get(key: str):  #@BUG\n    """Read a required config value; missing keys are an error."""\n    return load_config()[key]\n')))],
    "signature_change", "high", ["request_timeout", "default", "caller", "argument", "typeerror"],
    "app/api/orders.py::request_timeout")

bug("ctx_record_renamed", [(P_JOBS, JOBS, edit(JOBS, (
    "    def record(self, ok: bool) -> None:\n", "    def record_result(self, ok: bool) -> None:  #@BUG\n"), (
    "            stats.record(ok=True)\n", "            stats.record_result(ok=True)\n"), (
    "    stats.record(ok=False)\n", "    stats.record_result(ok=False)\n")))],
    "rename_breaks_caller", "high", ["mark_external_result", "scheduler", "caller", "record(", "attributeerror"],
    "app/workers/scheduler.py::mark_external_result")

bug("ctx_create_token_required_arg", [(P_AUTH, AUTH, edit(AUTH, (
    "def create_token(user_id: int) -> str:\n    expires = datetime.now(timezone.utc) + TOKEN_TTL\n"
    '    return jwt.encode({"sub": str(user_id), "exp": expires}, SECRET_KEY, algorithm=ALGORITHM)\n',
    "def create_token(user_id: int, scopes: list[str]) -> str:  #@BUG\n"
    "    expires = datetime.now(timezone.utc) + TOKEN_TTL\n"
    '    claims = {"sub": str(user_id), "exp": expires, "scopes": scopes}\n'
    "    return jwt.encode(claims, SECRET_KEY, algorithm=ALGORITHM)\n"), (
    '    return {"access_token": create_token(account.id), "token_type": "bearer"}\n',
    '    return {"access_token": create_token(account.id, ["user"]), "token_type": "bearer"}\n')))],
    "signature_change", "high", ["service_token", "scheduler", "scopes", "caller", "argument"],
    "app/workers/scheduler.py::service_token")

# --- clean: same functions, backwards-compatible changes; callers keep working ---

clean("ctx_clean_get_by_id_docstring", [(P_USERS, USERS, edit(USERS, (
    "    def get_by_id(self, user_id: int) -> User | None:\n",
    '    def get_by_id(self, user_id: int) -> User | None:\n        """Fetch one user by primary key; None if no such user."""\n')))])

clean("ctx_clean_total_helper", [(P_BILLING, BILLING, edit(BILLING, (
    "def total(invoice: Invoice) -> float:\n", '''def _with_tax(amount: float) -> float:
    return amount * (1 + TAX_RATE)


def total(invoice: Invoice) -> float:
'''), ("    return round(amount * (1 + TAX_RATE), 2)\n", "    return round(_with_tax(amount), 2)\n")))])

clean("ctx_clean_installments_cap", [(P_BILLING, BILLING, edit(BILLING, ('''    if months <= 0:
        raise ValueError("months must be positive")''', '''    if months <= 0:
        raise ValueError("months must be positive")
    if months > 36:
        raise ValueError("installment plans are limited to 36 months")''')))])

clean("ctx_clean_config_get_cast", [(P_CONFIG, CONFIG, edit(CONFIG, (
    "def get(key: str, default=None):\n    return load_config().get(key, default)\n",
    '''def get(key: str, default=None, cast=None):
    """Read one config value; optionally convert it, e.g. cast=int."""
    value = load_config().get(key, default)
    return cast(value) if cast is not None and value is not None else value
''')))])

clean("ctx_clean_record_alias", [(P_JOBS, JOBS, edit(JOBS, (
    "async def fetch_status(", '''    def record_many(self, results: list[bool]) -> None:
        for ok in results:
            self.record(ok)


async def fetch_status(''')))])

clean("ctx_clean_create_token_optional_arg", [(P_AUTH, AUTH, edit(AUTH, (
    "def create_token(user_id: int) -> str:\n    expires = datetime.now(timezone.utc) + TOKEN_TTL\n"
    '    return jwt.encode({"sub": str(user_id), "exp": expires}, SECRET_KEY, algorithm=ALGORITHM)\n',
    "def create_token(user_id: int, scopes: list[str] | None = None) -> str:\n"
    "    expires = datetime.now(timezone.utc) + TOKEN_TTL\n"
    '    claims = {"sub": str(user_id), "exp": expires, "scopes": scopes or []}\n'
    "    return jwt.encode(claims, SECRET_KEY, algorithm=ALGORITHM)\n")))])


if __name__ == "__main__":
    OUT.mkdir(exist_ok=True)
    labels = {}
    for name, case in CASES.items():
        diff, marked = build_diff(case["files"])
        filename = f"{name}.diff"
        (OUT / filename).write_text(diff, encoding="utf-8")
        repo = f"repos/{name}"
        write_repo(OUT / repo, REPO, case["files"])

        if case["bug"] is None:
            assert not marked, f"{name}: clean case has a bug marker"
            labels[filename] = {"split": "tune", "repo": repo, "bugs": []}
        else:
            assert len(marked) == 1, f"{name}: needs bug markers in exactly one file"
            (path, lines), = marked.items()
            labels[filename] = {"split": "tune", "repo": repo, "bugs": [
                {"file": path, "lines": [min(lines), max(lines)], **case["bug"]}]}

    (OUT / "labels.json").write_text(json.dumps(labels, indent=2), encoding="utf-8")
    n_bugs = sum(1 for c in CASES.values() if c["bug"])
    print(f"Wrote {len(CASES)} cases ({n_bugs} bug, {len(CASES) - n_bugs} clean) to {OUT}")
