"""Prevent the superseded model identity from returning to active runtime code."""

from pathlib import Path


def test_active_runtime_uses_qpx600dp_identity_and_reference_paths() -> None:
    root = Path(__file__).resolve().parents[1]
    old_package_name = "cpx" + "_electrolyser"
    old_model_name = "CPX400" + "DP"

    active_python = [root / "main.py", *sorted((root / "qpx_electrolyser").rglob("*.py"))]
    for path in active_python:
        contents = path.read_text(encoding="utf-8")
        assert old_package_name not in contents, path
        assert old_model_name not in contents, path

    assert not (root / old_package_name).exists()
    assert (root / "docs" / "qpx600dp_commands.md").is_file()
    assert not (root / "docs" / (old_model_name.lower() + "_commands.md")).exists()
    assert (
        root / "docs" / "reference" / "superseded" / "cpx400dp_manual_issue1.pdf"
    ).is_file()
    assert (
        root / "docs" / "reference" /
        "qpx600d_dp_instruction_manual_48511-1460_issue14.pdf"
    ).is_file()
    assert (
        root / "docs" / "reference" / "qpx600_addendum_48511-1461_issue1.pdf"
    ).is_file()
