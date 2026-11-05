import pytest

from app.services.files import BASE_DIR, _safe_path


def test_safe_path_allows_nested_files():
    assert _safe_path("reports/q1.pdf") == BASE_DIR / "reports" / "q1.pdf"


def test_safe_path_blocks_parent_traversal():
    with pytest.raises(PermissionError):
        _safe_path("../etc/passwd")


def test_safe_path_blocks_sibling_prefix():
    with pytest.raises(PermissionError):
        _safe_path("../uploads-backup/secret.txt")
