"""G8a trusted fixed-runner preflight regressions."""

from __future__ import annotations

from hashlib import sha256
import json
import os
from pathlib import Path
import subprocess
import sys
from types import SimpleNamespace

import pytest

import qte.gui.test_preflight as preflight


def _digest(value: str) -> str:
    return sha256(value.encode()).hexdigest()


def _value(runner="pytest"):
    repository = "/local/qte"
    binaries = ({".venv/lib/python3.14/site-packages/qte/_core.test.so": _digest("core")}
                if runner == "pytest" else {
                    ".venv/bin/ctest": _digest("launcher"),
                    ".venv/lib/python3.14/site-packages/cmake/data/bin/ctest": _digest("ctest"),
                    "build/local-dev/tests/cpp/qte_bar_tests": _digest("bar"),
                })
    return {
        "schema_version": 1, "runner": runner, "repository_path": repository,
        "identity_scope": "explicit_files_v1", "source_to_binary": "unknown",
        "identity": {
            "source": {"python/qte/a.py": _digest("source")},
            "tests": {("tests/python/test_a.py" if runner == "pytest" else
                       "tests/cpp/test_bar.cpp"): _digest("test")},
            "binaries": binaries,
            "build": {"pyproject.toml": _digest("build")},
        },
        "versions": {"python": "3.14.7", "pytest": "9.1.1", "ctest": "4.4.3", "platform": "test"},
        "ctest_tests": ([] if runner == "pytest" else [{
            "name": "qte.bar", "argv": [repository + "/build/local-dev/tests/cpp/qte_bar_tests"],
        }]),
    }


@pytest.mark.parametrize("runner", ["pytest", "ctest"])
def test_validate_canonicalizes_and_returns_owned_values(runner):
    prepared = preflight.validate_prepared(_value(runner))
    assert preflight.validate_prepared(prepared) == prepared
    assert preflight.validate_prepared(prepared.payload) == prepared
    changed = prepared.value
    changed["versions"]["python"] = "forged"
    assert prepared.value["versions"]["python"] == "3.14.7"
    assert prepared.payload == json.dumps(prepared.value, sort_keys=True, separators=(",", ":")).encode()


@pytest.mark.parametrize("change", [
    lambda value: value.update(runner="shell"),
    lambda value: value.update(repository_path="relative"),
    lambda value: value.update(source_to_binary="same"),
    lambda value: value["identity"].update(source={}),
    lambda value: value["identity"].update(source={"elsewhere/a.py": "a" * 64}),
    lambda value: value.update(versions={"python": "3"}),
    lambda value: value.update(extra=True),
])
def test_pure_validation_rejects_forged_shape_scope_and_empty_groups(change):
    value = _value()
    change(value)
    with pytest.raises(preflight.TestPreflightError):
        preflight.validate_prepared(value)


def test_runner_specific_ctest_inventory_is_exact():
    value = _value("pytest")
    value["ctest_tests"] = [{"name": "qte.bar", "argv": ["/local/qte/build/local-dev/tests/cpp/qte_bar_tests"]}]
    with pytest.raises(preflight.TestPreflightError):
        preflight.validate_prepared(value)


def test_pytest_support_roots_are_explicitly_allowed_but_arbitrary_files_are_not():
    for name in ("docs/architecture.md", "examples/research-run.json", "scripts/benchmark_replay.py"):
        value = _value(); value["identity"]["tests"] = {name: _digest(name)}
        assert name in preflight.validate_prepared(value).value["identity"]["tests"]
    value = _value(); value["identity"]["tests"] = {"build/accidental-output.txt": _digest("bad")}
    with pytest.raises(preflight.TestPreflightError):
        preflight.validate_prepared(value)
    value = _value("ctest")
    value["ctest_tests"][0]["argv"].append("--user-argument")
    with pytest.raises(preflight.TestPreflightError):
        preflight.validate_prepared(value)
    value = _value("ctest")
    value["ctest_tests"][0]["argv"] = ["/bin/sh"]
    with pytest.raises(preflight.TestPreflightError):
        preflight.validate_prepared(value)


def _discovery(runner="pytest"):
    groups = {
        "source": ("python/qte/a.py",),
        "tests": (("tests/python/test_a.py",) if runner == "pytest" else ("tests/cpp/test_bar.cpp",)),
        "binaries": ((".venv/lib/python3.14/site-packages/qte/_core.test.so",) if runner == "pytest"
                     else (".venv/bin/ctest", ".venv/lib/python3.14/site-packages/cmake/data/bin/ctest",
                           "build/local-dev/tests/cpp/qte_bar_tests")),
        "build": ("pyproject.toml",),
    }
    tests = (() if runner == "pytest" else (("qte.bar", ("/tmp/repository/build/local-dev/tests/cpp/qte_bar_tests",)),))
    return preflight._Discovery(groups, _value()["versions"], tests)


def test_prepare_captures_between_two_identical_discoveries(tmp_path, monkeypatch):
    discoveries = [_discovery(), _discovery()]
    monkeypatch.setattr(preflight, "_discover", lambda repository, runner: discoveries.pop(0))
    monkeypatch.setattr(preflight, "capture_identity", lambda repository, groups: _value()["identity"])
    prepared = preflight.prepare_test_run(tmp_path, "pytest")
    assert prepared.value["repository_path"] == str(tmp_path)
    assert prepared.value["identity"] == _value()["identity"]
    assert not discoveries


def test_prepare_rejects_new_or_deleted_discovered_files_during_capture(tmp_path, monkeypatch):
    changed = _discovery()
    changed.groups["source"] = changed.groups["source"] + ("python/qte/new.py",)
    discoveries = [_discovery(), changed]
    monkeypatch.setattr(preflight, "_discover", lambda repository, runner: discoveries.pop(0))
    monkeypatch.setattr(preflight, "capture_identity", lambda repository, groups: _value()["identity"])
    with pytest.raises(preflight.TestPreflightError):
        preflight.prepare_test_run(tmp_path, "pytest")


def test_recheck_requires_same_repository_and_fresh_payload(tmp_path, monkeypatch):
    value = _value(); value["repository_path"] = str(tmp_path)
    prepared = preflight.validate_prepared(value)
    monkeypatch.setattr(preflight, "prepare_test_run", lambda repository, runner: prepared)
    assert preflight.recheck_prepared(tmp_path, prepared) == prepared
    other = tmp_path / "other"; other.mkdir()
    with pytest.raises(preflight.TestPreflightError):
        preflight.recheck_prepared(other, prepared)
    different = _value(); different["repository_path"] = str(tmp_path); different["versions"]["python"] = "changed"
    monkeypatch.setattr(preflight, "prepare_test_run",
                        lambda repository, runner: preflight.validate_prepared(different))
    with pytest.raises(preflight.TestPreflightError):
        preflight.recheck_prepared(tmp_path, prepared)


def test_tree_discovery_ignores_pycache_but_rejects_symlinks(tmp_path):
    root = tmp_path / "python/qte"; root.mkdir(parents=True)
    (root / "a.py").write_text("source")
    cache = root / "__pycache__"; cache.mkdir(); (cache / "a.pyc").write_bytes(b"cache")
    assert preflight._tree_files(tmp_path, "python/qte", frozenset({".py"})) == ("python/qte/a.py",)
    (root / "linked.py").symlink_to(root / "a.py")
    with pytest.raises(preflight.TestPreflightError):
        preflight._tree_files(tmp_path, "python/qte", frozenset({".py"}))


def test_ctest_discovery_parser_rejects_args_and_unapproved_properties(tmp_path, monkeypatch):
    import cmake
    executable = tmp_path / "build/local-dev/tests/cpp/qte_bar_tests"
    executable.parent.mkdir(parents=True); executable.write_bytes(b"bin"); executable.chmod(0o755)
    source = tmp_path / "tests/cpp/CMakeLists.txt"; source.parent.mkdir(parents=True); source.write_text("add_test()")
    launcher = tmp_path / ".venv/bin/ctest"; launcher.parent.mkdir(parents=True); launcher.write_text("runner"); launcher.chmod(0o755)
    real = tmp_path / ".venv/lib/python3/site-packages/cmake/data/bin/ctest"
    real.parent.mkdir(parents=True); real.write_bytes(b"runner"); real.chmod(0o755)
    package = real.parents[2] / "__init__.py"
    package.write_bytes(b"# fixture package")
    monkeypatch.setattr(cmake, "CMAKE_BIN_DIR", str(real.parent))
    monkeypatch.setattr(cmake, "__file__", str(package))
    cache = tmp_path / "build/local-dev/CMakeCache.txt"
    cache.parent.mkdir(parents=True, exist_ok=True)
    cache.write_text(f"CMAKE_CTEST_COMMAND:INTERNAL={real}\n")
    monkeypatch.setattr(preflight, "_validate_ctest_configuration", lambda repository: ("build/local-dev/CMakeCache.txt",))
    monkeypatch.setattr(preflight, "_generated_tests", lambda repository: (("qte.bar", (str(executable),)),))

    def document(command, properties=None):
        return json.dumps({"backtraceGraph": {"commands": ["add_test"], "files": [str(source)],
              "nodes": [{"file": 0}, {"command": 0, "file": 0, "line": 1, "parent": 0}]},
            "kind": "ctestInfo", "version": {"major": 1, "minor": 0},
            "tests": [{"backtrace": 1, "command": command,
              "name": "qte.bar", "properties": properties if properties is not None else [
                  {"name": "WORKING_DIRECTORY", "value": str(executable.parent)}]}]}).encode()

    monkeypatch.setattr(preflight, "_run_fixed", lambda repository, argv: (document([str(executable)]), b""))
    tests, binaries, generated = preflight._ctest_discovery(tmp_path)
    assert tests == (("qte.bar", (str(executable),)),)
    assert binaries[-1] == "build/local-dev/tests/cpp/qte_bar_tests"
    assert generated == ("build/local-dev/CMakeCache.txt",)
    monkeypatch.setattr(preflight, "_run_fixed", lambda repository, argv: (document([str(executable), "--arg"]), b""))
    with pytest.raises(preflight.TestPreflightError): preflight._ctest_discovery(tmp_path)
    monkeypatch.setattr(preflight, "_run_fixed", lambda repository, argv: (
        document([str(executable)], [{"name": "TIMEOUT", "value": 1}]), b""))
    with pytest.raises(preflight.TestPreflightError): preflight._ctest_discovery(tmp_path)


def test_minimal_discovery_environment_has_no_parent_credentials(monkeypatch):
    monkeypatch.setenv("ALPACA_API_KEY", "secret")
    monkeypatch.setenv("AWS_SECRET_ACCESS_KEY", "secret")
    environment = preflight._minimal_environment()
    assert "ALPACA_API_KEY" not in environment and "AWS_SECRET_ACCESS_KEY" not in environment
    assert environment["PYTHONNOUSERSITE"] == "1"
    assert environment["PYTEST_DISABLE_PLUGIN_AUTOLOAD"] == "1"


def test_changed_bytes_after_first_capture_are_not_an_unchanged_preflight(tmp_path, monkeypatch):
    monkeypatch.setattr(preflight, "_discover", lambda *args: _discovery())
    first = _value()["identity"]
    second = _value()["identity"]
    second["source"]["python/qte/a.py"] = "f" * 64
    captures = [first, second]
    monkeypatch.setattr(preflight, "capture_identity", lambda *args: captures.pop(0))
    with pytest.raises(preflight.TestPreflightError):
        preflight.prepare_test_run(tmp_path, "pytest")


def test_metadata_read_is_bounded_and_rejects_symlink_components(tmp_path, monkeypatch):
    monkeypatch.setattr(preflight, "PAYLOAD_LIMIT", 16)
    path = tmp_path / "metadata"
    path.write_bytes(b"a" * 17)
    with pytest.raises(preflight.TestPreflightError): preflight._read_small(path)
    path.write_bytes(b"ok")
    link = tmp_path / "link"
    link.symlink_to(path)
    with pytest.raises(preflight.TestPreflightError): preflight._read_small(link)
    directory = tmp_path / "dir"; directory.mkdir()
    (directory / "file").write_bytes(b"ok")
    alias = tmp_path / "alias"; alias.symlink_to(directory, target_is_directory=True)
    with pytest.raises(preflight.TestPreflightError): preflight._read_small(alias / "file")
    assert preflight._read_small(path) == b"ok"


def test_discovery_counts_empty_directories_and_rejects_unknown_files(tmp_path, monkeypatch):
    root = tmp_path / "tree"; root.mkdir()
    (root / "a").mkdir(); (root / "b").mkdir()
    monkeypatch.setattr(preflight, "MAX_DISCOVERED_FILES", 1)
    with pytest.raises(preflight.TestPreflightError):
        preflight._tree_files(tmp_path, "tree", frozenset({".py"}))
    monkeypatch.setattr(preflight, "MAX_DISCOVERED_FILES", 10)
    (root / "unexpected.data").write_bytes(b"unlisted")
    with pytest.raises(preflight.TestPreflightError):
        preflight._tree_files(tmp_path, "tree", frozenset({".py"}))


def generated_fixture(repository):
    root = repository / "build/local-dev"
    cpp = root / "tests/cpp"; cpp.mkdir(parents=True)
    (root / "CTestTestfile.cmake").write_text('# comment\nsubdirs("tests/cpp")\n')
    source = repository / "tests/cpp/CMakeLists.txt"
    binary = cpp / "qte_bar_tests"
    (cpp / "CTestTestfile.cmake").write_text(
        f'add_test("qte.bar" "{binary}")\n'
        f'set_tests_properties("qte.bar" PROPERTIES  _BACKTRACE_TRIPLES "{source};5;add_test;{source};0;")\n')
    return root, cpp, binary


@pytest.mark.parametrize("change", ["include", "execute_process", "extra_property", "wrapper", "other_subdir"])
def test_generated_definitions_reject_execution_hooks_before_discovery(tmp_path, change):
    root, cpp, binary = generated_fixture(tmp_path)
    assert preflight._generated_tests(tmp_path) == (("qte.bar", (str(binary),)),)
    path = cpp / "CTestTestfile.cmake"
    content = path.read_text()
    if change == "include": content += 'include("untrusted.cmake")\n'
    elif change == "execute_process": content += 'execute_process(COMMAND whatever)\n'
    elif change == "extra_property": content = content.replace("PROPERTIES  ", 'PROPERTIES ENVIRONMENT "TOKEN=1" ')
    elif change == "wrapper": content = content.replace(str(binary), "/bin/sh")
    else:
        (root / "CTestTestfile.cmake").write_text('subdirs("elsewhere")\n')
    path.write_text(content)
    with pytest.raises(preflight.TestPreflightError): preflight._generated_tests(tmp_path)


def test_python_runtime_follows_only_owned_editable_source_and_extension(tmp_path, monkeypatch):
    import qte
    source = tmp_path / "python/qte/__init__.py"
    source.parent.mkdir(parents=True); source.write_bytes(b"# fixture")
    (tmp_path / "build/python-package").mkdir(parents=True)
    site = tmp_path / ".venv/lib/python3/site-packages"
    core = site / "qte/_core.test.so"; core.parent.mkdir(parents=True); core.write_bytes(b"extension")
    (tmp_path / ".venv/lib64").symlink_to("lib", target_is_directory=True)
    pth = site / "_qte_editable.pth"
    pth.write_text(f'import _qte_editable\n{tmp_path / "python"}\n')
    hook = site / "_qte_editable.py"
    args = [{"qte": str(source)}, {"qte._core": "qte/_core.test.so"},
            str(tmp_path / "build/python-package"), False, True, [], [], ""]
    hook.write_text("install(" + ", ".join(repr(item) for item in args) + ")\n")
    monkeypatch.setattr(qte, "__file__", str(source))
    monkeypatch.setitem(sys.modules, "qte._core", SimpleNamespace(__file__=str(core).replace("/.venv/lib/", "/.venv/lib64/")))
    monkeypatch.setattr(sys, "executable", str(tmp_path / ".venv/bin/python"))
    assert preflight._python_runtime(tmp_path) == tuple(sorted(
        path.relative_to(tmp_path).as_posix() for path in (core, hook, pth)))
    args[3] = True  # Auto-build hooks are outside this milestone's authority.
    hook.write_text("install(" + ", ".join(repr(item) for item in args) + ")\n")
    with pytest.raises(preflight.TestPreflightError): preflight._python_runtime(tmp_path)


@pytest.mark.parametrize("case", ["output_limit", "timeout", "closed_pipes", "failed_exit", "success"])
def test_fixed_discovery_subprocess_is_bounded_and_reaped(tmp_path, monkeypatch, case):
    processes = []
    original = subprocess.Popen
    def launch(*args, **kwargs):
        process = original(*args, **kwargs)
        processes.append(process)
        return process
    monkeypatch.setattr(preflight.subprocess, "Popen", launch)
    monkeypatch.setattr(preflight, "DISCOVERY_TIMEOUT", 0.5)
    monkeypatch.setattr(preflight, "DISCOVERY_OUTPUT_LIMIT", 128)
    code = {"output_limit": 'print("x" * 256)', "timeout": 'import time; time.sleep(30)',
            "closed_pipes": 'import os,time; os.close(1); os.close(2); time.sleep(30)',
            "failed_exit": 'raise SystemExit(2)', "success": 'print("ok")'}[case]
    argv = (sys.executable, "-c", code)
    if case == "success":
        assert preflight._run_fixed(tmp_path, argv) == (b"ok\n", b"")
    else:
        with pytest.raises(preflight.TestPreflightError): preflight._run_fixed(tmp_path, argv)
    assert len(processes) == 1 and processes[0].poll() is not None
    assert processes[0].stdout.closed and processes[0].stderr.closed


def test_prepared_payload_limit_also_applies_to_dataclass(monkeypatch):
    value = preflight.validate_prepared(_value())
    monkeypatch.setattr(preflight, "PAYLOAD_LIMIT", len(value.payload) - 1)
    with pytest.raises(preflight.TestPreflightError): preflight.validate_prepared(value)


def test_symlinked_ctest_bin_directory_cannot_execute_before_capture(tmp_path, monkeypatch):
    directory = tmp_path / "outside-bin"; directory.mkdir()
    launcher = directory / "ctest"; launcher.write_bytes(b"fake launcher"); launcher.chmod(0o755)
    environment = tmp_path / ".venv"; environment.mkdir()
    (environment / "bin").symlink_to(directory, target_is_directory=True)
    monkeypatch.setattr(preflight, "_validate_ctest_configuration", lambda repository: ())
    monkeypatch.setattr(preflight, "_run_fixed", lambda *args: pytest.fail("unsafe launcher executed"))
    with pytest.raises(ValueError): preflight._ctest_discovery(tmp_path)
