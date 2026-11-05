from app.services.billing import Invoice, LineItem, _apply_tax, total


def test_apply_tax():
    assert _apply_tax(100) == 114.0


def test_total_applies_discount_then_tax():
    invoice = Invoice(customer_id=1, items=[LineItem("A", 50.0, 2)])
    assert total(invoice) == 102.6
