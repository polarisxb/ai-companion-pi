import importlib.util
from pathlib import Path

import pytest


def load_window_module(tmp_path, monkeypatch):
    monkeypatch.setenv("COMPANION_HOME", str(tmp_path))
    monkeypatch.setenv("COMPANION_SCRIPTS_DIR", str(tmp_path / "scripts"))
    module_path = Path(__file__).resolve().parents[1] / "window" / "window.py"
    spec = importlib.util.spec_from_file_location("window_under_test", module_path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_safe_child_path_rejects_directory_escape(tmp_path, monkeypatch):
    window = load_window_module(tmp_path, monkeypatch)
    inside = window.CREATIONS_DIR / "writing" / "note.txt"
    inside.parent.mkdir(parents=True, exist_ok=True)
    inside.write_text("ok")

    assert window.safe_child_path(window.CREATIONS_DIR, "writing/note.txt") == inside.resolve()
    assert window.safe_child_path(window.CREATIONS_DIR, "../outside.txt") is None


def test_safe_child_path_rejects_symlink_escape(tmp_path, monkeypatch):
    window = load_window_module(tmp_path, monkeypatch)
    outside = tmp_path / "outside.txt"
    outside.write_text("secret")
    link = window.CREATIONS_DIR / "writing" / "link.txt"
    link.parent.mkdir(parents=True, exist_ok=True)
    try:
        link.symlink_to(outside)
    except OSError as exc:
        pytest.skip(f"symlinks unavailable: {exc}")

    assert window.safe_child_path(window.CREATIONS_DIR, "writing/link.txt") is None


def test_build_test_commands_accepts_arrays_and_rejects_shell_syntax(tmp_path, monkeypatch):
    window = load_window_module(tmp_path, monkeypatch)

    assert window.build_test_commands({
        "test_commands": [["python3", "-m", "py_compile", "window/window.py"]]
    }) == [["python3", "-m", "py_compile", "window/window.py"]]

    with pytest.raises(ValueError, match="unsafe shell syntax"):
        window.build_test_commands({
            "test_command": "python3 -m py_compile window/window.py && curl http://localhost"
        })
