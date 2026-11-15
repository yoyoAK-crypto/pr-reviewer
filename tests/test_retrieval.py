# Retrieval: finding the changed functions by line number, and related code by search.

import app.tools.retrieval as retrieval
from app.rag.chunker import chunk_repo

BILLING = '''TAX_RATE = 0.14


def discount(amount: float) -> float:
    """Orders of 100 or more get the bulk discount."""
    if amount > 100:
        return amount * 0.1
    return 0.0


def total(amount: float) -> float:
    return round((amount - discount(amount)) * (1 + TAX_RATE), 2)
'''

ORDERS = '''from app.billing import total


# Formats the amount for the receipt.
def receipt_text(amount: float) -> str:
    return f"Total due: {total(amount):.2f} EGP"
'''

DIFF = '''diff --git a/app/billing.py b/app/billing.py
--- a/app/billing.py
+++ b/app/billing.py
@@ -4,5 +4,5 @@ TAX_RATE = 0.14
 def discount(amount: float) -> float:
     """Orders of 100 or more get the bulk discount."""
-    if amount >= 100:
+    if amount > 100:
         return amount * 0.1
     return 0.0
'''


def make_repo(tmp_path):
    (tmp_path / "app").mkdir()
    (tmp_path / "app" / "billing.py").write_text(BILLING)
    (tmp_path / "app" / "orders.py").write_text(ORDERS)
    return tmp_path


def test_changed_lines_follow_the_new_file_numbering():
    assert retrieval.changed_lines(DIFF) == {"app/billing.py": {6}}


def test_chunker_keeps_comments_with_their_function(tmp_path):
    chunks = {c.name: c for c in chunk_repo(make_repo(tmp_path))}
    assert chunks["receipt_text"].code.startswith("# Formats the amount")
    assert "total" in chunks["receipt_text"].calls


def test_changed_function_is_found_without_search(tmp_path):
    chunks = chunk_repo(make_repo(tmp_path))
    assert [c.name for c in retrieval.changed_chunks(chunks, DIFF)] == ["discount"]


def test_keyword_retrieval_adds_related_code_but_not_the_changed_function_twice(tmp_path, monkeypatch):
    monkeypatch.setattr(retrieval, "MODE", "keyword")
    context, ids = retrieval.build_context(DIFF, make_repo(tmp_path))
    assert ids[0] == "app/billing.py::discount"            # changed function first, in full
    assert "app/billing.py::total" in ids                    # its caller, found by search
    assert ids.count("app/billing.py::discount") == 1
    assert "[changed function, full]" in context and "[related]" in context


def test_context_respects_the_size_cap(tmp_path, monkeypatch):
    monkeypatch.setattr(retrieval, "MODE", "keyword")
    monkeypatch.setattr(retrieval, "MAX_CONTEXT_CHARS", 300)
    context, _ = retrieval.build_context(DIFF, make_repo(tmp_path))
    assert len(context) <= 300


def test_retrieval_off_adds_nothing(tmp_path, monkeypatch):
    monkeypatch.setattr(retrieval, "MODE", "off")
    assert retrieval.build_context(DIFF, make_repo(tmp_path)) == ("", [])
