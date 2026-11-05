from unittest.mock import patch

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
