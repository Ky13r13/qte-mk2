"""G8a explicit test-job identity capture regressions."""

from __future__ import annotations

import os
from pathlib import Path

import pytest

import qte.gui.test_identity as identity


def _groups() -> dict[str, tuple[str, ...]]:
    return {
        "source": ("python/qte/module.py",),
        "tests": ("tests/python/test_module.py",),
        "binaries": ("build/bin/qte_tests",),
        "build": ("build/CMakeCache.txt",),
    }


def _repository(tmp_path: Path) -> Path:
    for name, value in {
        "python/qte/module.py": b"source",
        "tests/python/test_module.py": b"tests",
        "build/bin/qte_tests": b"binary",
        "build/CMakeCache.txt": b"configuration",
    }.items():
        path = tmp_path / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(value)
    return tmp_path


def test_capture_is_explicit_sorted_and_ignores_unlisted_files(tmp_path):
    repository = _repository(tmp_path)
    first = identity.capture_identity(repository, _groups())
    (repository / "unlisted.txt").write_bytes(b"unlisted")
    second = identity.capture_identity(repository, _groups())

    assert first == second
    assert tuple(first) == identity.IDENTITY_GROUPS
    assert first["build"] == {"build/CMakeCache.txt": first["build"]["build/CMakeCache.txt"]}


@pytest.mark.parametrize("bad", [
    "/absolute", "./relative", "a/../b", "a//b", "a\\b", "a\x00b", "a/",
])
def test_capture_rejects_noncanonical_path_spellings_without_leaking_them(tmp_path, bad):
    groups = _groups(); groups["source"] = (bad,)
    with pytest.raises(identity.TestIdentityError) as error:
        identity.capture_identity(_repository(tmp_path), groups)
    assert bad not in str(error.value)


def test_capture_rejects_duplicate_names_across_groups(tmp_path):
    groups = _groups(); groups["tests"] = ("python/qte/module.py",)
    with pytest.raises(identity.TestIdentityError):
        identity.capture_identity(_repository(tmp_path), groups)


def test_capture_rejects_duplicate_names_within_a_group(tmp_path):
    groups = _groups(); groups["source"] = ("python/qte/module.py", "python/qte/module.py")
    with pytest.raises(identity.TestIdentityError):
        identity.capture_identity(_repository(tmp_path), groups)


@pytest.mark.parametrize("change", ["symlink", "directory", "fifo"])
def test_capture_rejects_non_regular_and_symlink_components(tmp_path, change):
    repository = _repository(tmp_path)
    groups = _groups()
    if change == "symlink":
        (repository / "linked").symlink_to(repository / "python", target_is_directory=True)
        groups["source"] = ("linked/qte/module.py",)
    elif change == "directory":
        groups["source"] = ("python/qte",)
    else:
        fifo = repository / "pipe"; os.mkfifo(fifo)
        groups["source"] = ("pipe",)
    with pytest.raises(identity.TestIdentityError):
        identity.capture_identity(repository, groups)


def test_capture_rejects_a_listed_file_replaced_during_capture(tmp_path, monkeypatch):
    repository = _repository(tmp_path)
    original = identity.read_stable
    changed = False

    def replace_after_read(*args, **kwargs):
        nonlocal changed
        data = original(*args, **kwargs)
        if not changed:
            changed = True
            target = repository / "tests/python/test_module.py"
            target.unlink()
            target.write_bytes(b"replacement")
        return data

    monkeypatch.setattr(identity, "read_stable", replace_after_read)
    with pytest.raises(identity.TestIdentityError):
        identity.capture_identity(repository, _groups())


def test_compare_reports_independent_group_changes_and_unknown_current(tmp_path):
    repository = _repository(tmp_path)
    recorded = identity.capture_identity(repository, _groups())
    (repository / "python/qte/module.py").write_bytes(b"changed source")
    current = identity.capture_identity(repository, _groups())

    assert identity.compare_identity(recorded, current) == {
        "source": "different", "tests": "same", "binaries": "same", "build": "same",
    }
    assert identity.compare_identity(recorded, None) == {
        "source": "unknown", "tests": "unknown", "binaries": "unknown", "build": "unknown",
    }


def test_validate_identity_checks_shape_digest_and_cross_group_names(tmp_path):
    value = identity.capture_identity(_repository(tmp_path), _groups())
    assert identity.validate_identity(value) == value
    invalid = {group: dict(entries) for group, entries in value.items()}
    invalid["source"]["python/qte/module.py"] = "A" * 64
    with pytest.raises(identity.TestIdentityError):
        identity.validate_identity(invalid)
    invalid = {group: dict(entries) for group, entries in value.items()}
    digest = next(iter(invalid["source"].values()))
    invalid["tests"] = {"python/qte/module.py": digest}
    with pytest.raises(identity.TestIdentityError):
        identity.validate_identity(invalid)


def test_compare_marks_empty_validated_groups_as_unavailable(tmp_path):
    value = identity.capture_identity(_repository(tmp_path), _groups())
    unavailable = {group: dict(entries) for group, entries in value.items()}
    unavailable["build"] = {}
    assert identity.compare_identity(unavailable, unavailable)["build"] == "unknown"
