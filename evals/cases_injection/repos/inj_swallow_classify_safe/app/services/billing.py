from dataclasses import dataclass, field

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
