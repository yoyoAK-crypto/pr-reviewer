from app.repositories.users import UserRepository
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
