import pytest

from app.services.billing import Invoice, LineItem, installments


def test_installments_rejects_more_than_36_months():
    with pytest.raises(ValueError):
        installments(Invoice(1, [LineItem("A", 10.0, 1)]), 37)


def test_installments_allows_36_months():
    assert len(installments(Invoice(1, [LineItem("A", 360.0, 1)]), 36)) == 36
