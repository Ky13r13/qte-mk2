"""Trusted, bounded identity preflight for the two fixed local test runners.

The captured files are an explicit useful scope, not a complete dependency
graph or proof that a C++ binary was built from the captured source.
"""

from __future__ import annotations

import ast
from dataclasses import dataclass
import importlib.metadata
import json
import os
from pathlib import Path, PurePosixPath
import platform
import re
import selectors
import signal
import stat
import subprocess
import sys
import time
from typing import Literal

from .artifacts import strict_json
from .artifact_io import ArtifactIOError, read_stable
from .test_identity import MAX_BINARY_BYTES, _snapshot, capture_identity, validate_identity


PAYLOAD_LIMIT = 1024 * 1024
DISCOVERY_OUTPUT_LIMIT = 2 * 1024 * 1024
DISCOVERY_TIMEOUT = 15.0
MAX_DISCOVERED_FILES = 10_000
_FIELDS = frozenset({
    "schema_version", "runner", "repository_path", "identity_scope",
    "source_to_binary", "identity", "versions", "ctest_tests",
})
_VERSIONS = frozenset({"python", "pytest", "ctest", "platform"})
_SOURCE_PREFIXES = ("include/", "src/", "bindings/", "python/qte/")
_COMMON_BUILD = frozenset({"CMakeLists.txt", "CMakePresets.json", "pyproject.toml", ".venv/pyvenv.cfg"})
_SAFE_NAME = re.compile(r"[A-Za-z0-9_.-]{1,256}\Z")


class TestPreflightError(ValueError):
    """The fixed test runner or its explicit identity scope is unavailable."""

    __test__ = False


def _error() -> TestPreflightError:
    return TestPreflightError("Test preflight is unavailable or inconsistent.")


@dataclass(frozen=True)
class PreparedTestRun:
    payload: bytes

    @property
    def value(self) -> dict:
        """Return a fresh owned value, never mutable validated state."""
        value = strict_json(self.payload)
        if not isinstance(value, dict):  # Defensive; construction validates this.
            raise _error()
        return value


@dataclass(frozen=True)
class _Discovery:
    groups: dict[str, tuple[str, ...]]
    versions: dict[str, str]
    ctest_tests: tuple[tuple[str, tuple[str, ...]], ...]


def _canonical(value: object) -> bytes:
    try:
        data = json.dumps(value, sort_keys=True, separators=(",", ":"),
                          ensure_ascii=True, allow_nan=False).encode("ascii")
    except (TypeError, ValueError, RecursionError, OverflowError):
        raise _error() from None
    if len(data) > PAYLOAD_LIMIT:
        raise _error()
    return data


def _repository_text(value: object) -> str:
    if type(value) is not str or not 1 < len(value) <= 1024 or "\\" in value:
        raise _error()
    if any(ord(character) < 32 or ord(character) == 127 for character in value):
        raise _error()
    path = PurePosixPath(value)
    if not path.is_absolute() or str(path) != value or ".." in path.parts or value.startswith("//"):
        raise _error()
    return value


def _version(value: object) -> str:
    if type(value) is not str or not value or len(value) > 256:
        raise _error()
    if any(ord(character) < 32 or ord(character) == 127 for character in value):
        raise _error()
    return value


def _in_scope(name: str, runner: str, group: str) -> bool:
    if group == "source":
        return name.startswith(_SOURCE_PREFIXES)
    if group == "tests":
        if runner == "pytest":
            return (name.startswith(("tests/python/", "tests/fixtures/", "docs/", "examples/", "scripts/"))
                    or name in {"tests/conftest.py", "conftest.py"})
        return name.startswith("tests/cpp/")
    if group == "build":
        return (name in _COMMON_BUILD or name.startswith("requirements-") and
                name.endswith((".lock", ".txt", ".in")) or runner == "ctest" and
                name.startswith("build/local-dev/") and name.endswith((".cmake", ".txt", ".ninja")))
    if (name.startswith(".venv/lib/") and
                ("/site-packages/qte/_core." in name or name.endswith(
                    ("/site-packages/_qte_editable.py", "/site-packages/_qte_editable.pth")))):
        return True
    if runner == "pytest":
        return False
    return (name == ".venv/bin/ctest" or
            name.startswith(".venv/lib/") and name.endswith(("/cmake/data/bin/ctest", "/cmake/__init__.py")) or
            name.startswith("build/local-dev/tests/cpp/"))


def validate_prepared(value: object) -> PreparedTestRun:
    """Purely validate and canonicalize a prepared request; perform no discovery."""
    try:
        if isinstance(value, PreparedTestRun):
            if type(value.payload) is not bytes or len(value.payload) > PAYLOAD_LIMIT:
                raise _error()
            decoded = strict_json(value.payload)
        elif type(value) is bytes:
            if len(value) > PAYLOAD_LIMIT:
                raise _error()
            decoded = strict_json(value)
        else:
            decoded = value
        if type(decoded) is not dict or set(decoded) != _FIELDS:
            raise _error()
        if type(decoded["schema_version"]) is not int or decoded["schema_version"] != 1:
            raise _error()
        runner = decoded["runner"]
        if runner not in {"pytest", "ctest"}:
            raise _error()
        repository = _repository_text(decoded["repository_path"])
        if decoded["identity_scope"] != "explicit_files_v1" or decoded["source_to_binary"] != "unknown":
            raise _error()
        identity = validate_identity(decoded["identity"])
        if any(not identity[group] for group in ("source", "tests", "binaries", "build")):
            raise _error()
        for group, entries in identity.items():
            if any(not _in_scope(name, runner, group) for name in entries):
                raise _error()

        versions = decoded["versions"]
        if type(versions) is not dict or set(versions) != _VERSIONS:
            raise _error()
        checked_versions = {name: _version(versions[name]) for name in sorted(_VERSIONS)}

        raw_tests = decoded["ctest_tests"]
        if type(raw_tests) is not list or (runner == "pytest" and raw_tests) or (runner == "ctest" and not raw_tests):
            raise _error()
        tests: list[dict[str, object]] = []
        names: set[str] = set()
        for item in raw_tests:
            if type(item) is not dict or set(item) != {"name", "argv"}:
                raise _error()
            name, argv = item["name"], item["argv"]
            if type(name) is not str or _SAFE_NAME.fullmatch(name) is None or name in names:
                raise _error()
            if type(argv) is not list or len(argv) != 1 or type(argv[0]) is not str:
                raise _error()
            executable = PurePosixPath(argv[0])
            expected_root = PurePosixPath(repository) / "build/local-dev/tests/cpp"
            if (not executable.is_absolute() or executable.parent != expected_root
                    or str(executable) != argv[0] or _SAFE_NAME.fullmatch(executable.name) is None):
                raise _error()
            relative = executable.relative_to(PurePosixPath(repository)).as_posix()
            if relative not in identity["binaries"]:
                raise _error()
            names.add(name)
            tests.append({"name": name, "argv": [argv[0]]})
        canonical = {
            "schema_version": 1,
            "runner": runner,
            "repository_path": repository,
            "identity_scope": "explicit_files_v1",
            "source_to_binary": "unknown",
            "identity": identity,
            "versions": checked_versions,
            "ctest_tests": tests,
        }
        return PreparedTestRun(_canonical(canonical))
    except TestPreflightError:
        raise
    except (KeyError, TypeError, ValueError, UnicodeError, RecursionError, OverflowError):
        raise _error() from None


def _regular(path: Path, *, executable: bool = False) -> None:
    try:
        info = path.lstat()
    except OSError:
        raise _error() from None
    if not stat.S_ISREG(info.st_mode) or executable and not info.st_mode & 0o111:
        raise _error()


def _relative(repository: Path, path: Path) -> str:
    try:
        resolved = path.resolve(strict=True)
        relative = resolved.relative_to(repository).as_posix()
    except (OSError, ValueError):
        raise _error() from None
    return relative


def _tree_files(repository: Path, root_name: str, suffixes: frozenset[str]) -> tuple[str, ...]:
    root = repository / root_name
    try:
        if not root.is_dir() or root.is_symlink():
            raise _error()
        pending = [root]
        result: list[str] = []
        entry_count = 0
        while pending:
            directory = pending.pop()
            with os.scandir(directory) as entries:
                for entry in entries:
                    entry_count += 1
                    if entry_count > MAX_DISCOVERED_FILES:
                        raise _error()
                    if entry.name == "__pycache__":
                        if not entry.is_dir(follow_symlinks=False):
                            raise _error()
                        continue
                    if entry.name.startswith(".") or entry.is_symlink():
                        raise _error()
                    if entry.is_dir(follow_symlinks=False):
                        pending.append(Path(entry.path))
                    elif entry.is_file(follow_symlinks=False) and Path(entry.name).suffix in suffixes:
                        result.append(Path(entry.path).relative_to(repository).as_posix())
                    else:
                        raise _error()
                    if len(result) + len(pending) > MAX_DISCOVERED_FILES:
                        raise _error()
        return tuple(sorted(result))
    except TestPreflightError:
        raise
    except OSError:
        raise _error() from None


def _read_small(path: Path) -> bytes:
    try:
        # No unbounded read before checking size; refuse symlink components.
        relative = path.relative_to(Path("/")).as_posix()
        snapshot = _snapshot(Path("/"), relative, PAYLOAD_LIMIT)
        return read_stable(Path("/"), relative, PAYLOAD_LIMIT, snapshot)
    except (ArtifactIOError, OSError, ValueError):
        raise _error() from None


def _common_build(repository: Path) -> tuple[str, ...]:
    paths = list(_COMMON_BUILD)
    for count, entry in enumerate(repository.iterdir(), 1):
        if count > MAX_DISCOVERED_FILES:
            raise _error()
        if entry.is_symlink():
            if entry.name.startswith("requirements-"):
                raise _error()
            continue
        if entry.is_file() and entry.name.startswith("requirements-") and entry.name.endswith((".lock", ".txt", ".in")):
            paths.append(entry.name)
    for name in paths:
        _regular(repository / name)
    return tuple(sorted(paths))


def _python_runtime(repository: Path) -> tuple[str, ...]:
    import qte

    if Path(sys.executable).absolute() != repository / ".venv/bin/python":
        raise _error()
    core = sys.modules.get("qte._core")
    if core is None or not isinstance(getattr(core, "__file__", None), str):
        raise _error()
    if Path(qte.__file__ or "").resolve(strict=True) != (repository / "python/qte/__init__.py").resolve(strict=True):
        raise _error()
    core_path = Path(core.__file__).resolve(strict=True)
    _regular(Path(core.__file__))
    library = (repository / ".venv/lib").resolve(strict=True)
    try:
        core_path.relative_to(library)
    except ValueError:
        raise _error() from None
    if core_path.parent.name != "qte" or not core_path.name.startswith("_core."):
        raise _error()
    site = core_path.parent.parent
    hook, pth = site / "_qte_editable.py", site / "_qte_editable.pth"
    if _read_small(pth).decode("utf-8") != f"import _qte_editable\n{repository / 'python'}\n":
        raise _error()
    try:
        tree = ast.parse(_read_small(hook).decode("utf-8"))
        call = tree.body[-1].value  # type: ignore[union-attr]
        if not isinstance(call, ast.Call) or not isinstance(call.func, ast.Name) or call.func.id != "install":
            raise _error()
        args = [ast.literal_eval(argument) for argument in call.args]
        known_source, known_wheel = args[0], args[1]
        if (len(args) != 8 or call.keywords or not isinstance(known_source, dict)
                or not isinstance(known_wheel, dict)
                or known_wheel != {"qte._core": core_path.relative_to(site).as_posix()}
                or args[3] is not False):
            raise _error()
        if Path(args[2]).resolve(strict=True) != (repository / "build/python-package").resolve(strict=True):
            raise _error()
        for module, source in known_source.items():
            if not isinstance(module, str) or not (module == "qte" or module.startswith("qte.")):
                raise _error()
            Path(source).resolve(strict=True).relative_to(repository / "python/qte")
    except (IndexError, AttributeError, SyntaxError, ValueError, TypeError, UnicodeError, OSError):
        raise _error() from None
    return tuple(sorted(_relative(repository, path) for path in (core_path, hook, pth)))


def _minimal_environment() -> dict[str, str]:
    return {"PATH": "/usr/bin:/bin", "LANG": "C.UTF-8", "LC_ALL": "C.UTF-8",
            "PYTHONNOUSERSITE": "1", "PYTHONDONTWRITEBYTECODE": "1",
            "PYTEST_DISABLE_PLUGIN_AUTOLOAD": "1"}


def _run_fixed(repository: Path, argv: tuple[str, ...]) -> tuple[bytes, bytes]:
    """Run one trusted discovery command with bounded time, output and env."""
    process: subprocess.Popen[bytes] | None = None
    selector = selectors.DefaultSelector()
    chunks: dict[str, list[bytes]] = {"stdout": [], "stderr": []}
    total = 0
    try:
        process = subprocess.Popen(argv, cwd=repository, env=_minimal_environment(),
            stdin=subprocess.DEVNULL, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
            start_new_session=True, shell=False)
        assert process.stdout is not None and process.stderr is not None
        selector.register(process.stdout, selectors.EVENT_READ, "stdout")
        selector.register(process.stderr, selectors.EVENT_READ, "stderr")
        deadline = time.monotonic() + DISCOVERY_TIMEOUT
        while selector.get_map():
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                raise _error()
            for key, _ in selector.select(min(0.1, remaining)):
                block = os.read(key.fileobj.fileno(), 65536)
                if not block:
                    selector.unregister(key.fileobj)
                    continue
                total += len(block)
                if total > DISCOVERY_OUTPUT_LIMIT:
                    raise _error()
                chunks[key.data].append(block)
        code = process.wait(timeout=1)
        if code != 0:
            raise _error()
        return b"".join(chunks["stdout"]), b"".join(chunks["stderr"])
    except (OSError, subprocess.SubprocessError):
        raise _error() from None
    finally:
        if process is not None:
            # Timeout, failed setup after spawn, and surviving descendants all
            # need cleanup; a leader exit alone does not close its whole group.
            try:
                os.killpg(process.pid, signal.SIGKILL)
            except ProcessLookupError:
                pass
            finally:
                process.wait()
                for stream in (process.stdout, process.stderr):
                    if stream is not None:
                        stream.close()
        selector.close()


def _validate_ctest_configuration(repository: Path) -> tuple[str, ...]:
    for forbidden in ("CMakeUserPresets.json", "CTestCustom.cmake", "CTestConfig.cmake",
                      "build/local-dev/CTestCustom.cmake", "build/local-dev/CTestConfig.cmake",
                      "build/local-dev/tests/cpp/CTestCustom.cmake"):
        if (repository / forbidden).exists() or (repository / forbidden).is_symlink():
            raise _error()
    presets = strict_json(_read_small(repository / "CMakePresets.json"))
    if not isinstance(presets, dict) or any(key in presets for key in ("include", "workflowPresets", "vendor")):
        raise _error()
    test_presets = presets.get("testPresets")
    if not isinstance(test_presets, list):
        raise _error()
    dev = [item for item in test_presets if isinstance(item, dict) and item.get("name") == "dev"]
    if len(dev) != 1 or dev[0] != {"name": "dev", "configurePreset": "dev"}:
        raise _error()
    configure = presets.get("configurePresets")
    if type(configure) is not list:
        raise _error()
    dev_config = [item for item in configure if type(item) is dict and item.get("name") == "dev"]
    if (len(dev_config) != 1 or dev_config[0].get("binaryDir") != "${sourceDir}/build/local-dev"
            or dev_config[0].get("generator") != "Ninja"
            or set(dev_config[0]) - {"name", "displayName", "generator", "binaryDir", "cacheVariables"}):
        raise _error()
    forbidden_keys = {"include", "environment", "condition", "vendor", "filter", "execution", "output"}
    stack: list[object] = [presets]
    while stack:
        item = stack.pop()
        if isinstance(item, dict):
            if set(item) & forbidden_keys:
                raise _error()
            stack.extend(item.values())
        elif isinstance(item, list):
            stack.extend(item)
    for source in (repository / "CMakeLists.txt", repository / "tests/cpp/CMakeLists.txt"):
        text = _read_small(source).decode("utf-8")
        if re.search(r"(?i)\b(include|set_tests_properties|ctest_[a-z_]*)\s*\(", text):
            raise _error()
        if re.search(r"(?i)\bset_property\s*\(\s*TEST\b", text):
            raise _error()

    generated = [
        "build/local-dev/CMakeCache.txt", "build/local-dev/build.ninja",
        "build/local-dev/CMakeFiles/rules.ninja", "build/local-dev/CTestTestfile.cmake",
        "build/local-dev/tests/cpp/CTestTestfile.cmake",
    ]
    version_dirs = [path for path in (repository / "build/local-dev/CMakeFiles").iterdir()
                    if path.is_dir() and re.fullmatch(r"[0-9]+(?:\.[0-9]+){1,3}", path.name)]
    matching = [path for path in version_dirs if (path / "CMakeCXXCompiler.cmake").is_file()
                and (path / "CMakeSystem.cmake").is_file()]
    if len(matching) != 1:
        raise _error()
    generated.extend([
        f"build/local-dev/CMakeFiles/{matching[0].name}/CMakeCXXCompiler.cmake",
        f"build/local-dev/CMakeFiles/{matching[0].name}/CMakeSystem.cmake",
    ])
    for name in generated:
        _regular(repository / name)
    cache = _read_small(repository / "build/local-dev/CMakeCache.txt").decode("utf-8")
    if re.search(r"(?im)^CTEST_(?:CUSTOM|INCLUDE|SCRIPT)", cache):
        raise _error()
    for key, path in (("CMAKE_HOME_DIRECTORY", repository),
                      ("CMAKE_CACHEFILE_DIR", repository / "build/local-dev")):
        if re.findall(rf"(?m)^{key}:INTERNAL=(.+)$", cache) != [str(path)]:
            raise _error()
    _generated_tests(repository)
    return tuple(sorted(generated))


def _generated_tests(repository: Path) -> tuple[tuple[str, tuple[str, ...]], ...]:
    """Admit only the currently implemented generated CMake test grammar.

    show-only still evaluates CMake; reject hooks/includes before invoking it.
    Future launchers/properties require a separately tested extension here.
    """
    def lines(name):
        return [line.strip() for line in _read_small(repository / name).decode("utf-8").splitlines()
                if line.strip() and not line.lstrip().startswith("#")]
    if lines("build/local-dev/CTestTestfile.cmake") != ['subdirs("tests/cpp")']:
        raise _error()
    body = lines("build/local-dev/tests/cpp/CTestTestfile.cmake")
    if not body or len(body) % 2 or len(body) > 20_000:
        raise _error()
    binary_root = re.escape(str(repository / "build/local-dev/tests/cpp"))
    source = re.escape(str(repository / "tests/cpp/CMakeLists.txt"))
    result = []
    for index in range(0, len(body), 2):
        match = re.fullmatch(r'add_test\("([A-Za-z0-9_.-]{1,256})" "(' + binary_root
                             + r'/[A-Za-z0-9_.-]{1,256})"\)', body[index])
        if match is None:
            raise _error()
        name, executable = match.groups()
        if re.fullmatch(r'set_tests_properties\("' + re.escape(name)
                       + r'" PROPERTIES\s+_BACKTRACE_TRIPLES "' + source
                       + r';[0-9]+;add_test;' + source + r';0;"\)', body[index + 1]) is None:
            raise _error()
        result.append((name, (executable,)))
    return tuple(result)


def _ctest_discovery(repository: Path):
    import cmake

    generated = _validate_ctest_configuration(repository)
    launcher = repository / ".venv/bin/ctest"
    _regular(launcher, executable=True)
    # Inspect every launcher path component BEFORE it can execute. A leaf-only
    # lstat would allow a symlinked .venv/bin to run an outside program first.
    _snapshot(repository, ".venv/bin/ctest", PAYLOAD_LIMIT)
    cache = _read_small(repository / "build/local-dev/CMakeCache.txt").decode("utf-8")
    matches = re.findall(r"(?m)^CMAKE_CTEST_COMMAND:INTERNAL=(.+)$", cache)
    if len(matches) != 1:
        raise _error()
    real_runner = Path(matches[0]).resolve(strict=True)
    actual_runner = (Path(cmake.CMAKE_BIN_DIR) / "ctest").resolve(strict=True)
    if real_runner != actual_runner:
        raise _error()
    library = (repository / ".venv/lib").resolve(strict=True)
    try:
        real_runner.relative_to(library)
        library.relative_to(repository / ".venv")
    except ValueError:
        raise _error() from None
    if not real_runner.as_posix().endswith("/site-packages/cmake/data/bin/ctest"):
        raise _error()
    _regular(real_runner, executable=True)
    runner_relative = _relative(repository, real_runner)
    package_relative = _relative(repository, Path(cmake.__file__))
    _snapshot(repository, runner_relative, MAX_BINARY_BYTES)
    _snapshot(repository, package_relative, PAYLOAD_LIMIT)
    output, error_output = _run_fixed(repository, (str(launcher), "--preset", "dev", "--show-only=json-v1"))
    if error_output.strip():
        raise _error()
    value = strict_json(output)
    if (type(value) is not dict or set(value) != {"backtraceGraph", "kind", "tests", "version"}
            or value["kind"] != "ctestInfo" or value["version"] != {"major": 1, "minor": 0}):
        raise _error()
    graph = value["backtraceGraph"]
    if type(graph) is not dict or set(graph) != {"commands", "files", "nodes"} or graph["commands"] != ["add_test"]:
        raise _error()
    expected_source = str(repository / "tests/cpp/CMakeLists.txt")
    if type(graph["files"]) is not list or set(graph["files"]) != {expected_source}:
        raise _error()
    if type(graph["nodes"]) is not list:
        raise _error()

    tests: list[tuple[str, tuple[str, ...]]] = []
    binaries = [".venv/bin/ctest", runner_relative, package_relative]
    names: set[str] = set()
    expected_directory = repository / "build/local-dev/tests/cpp"
    raw_tests = value["tests"]
    if type(raw_tests) is not list or not raw_tests or len(raw_tests) > 10_000:
        raise _error()
    for item in raw_tests:
        if type(item) is not dict or set(item) != {"backtrace", "command", "name", "properties"}:
            raise _error()
        name, command, properties = item["name"], item["command"], item["properties"]
        if type(name) is not str or _SAFE_NAME.fullmatch(name) is None or name in names:
            raise _error()
        if type(command) is not list or len(command) != 1 or type(command[0]) is not str:
            raise _error()
        executable = Path(command[0])
        if not executable.is_absolute() or executable.parent != expected_directory or executable.is_symlink():
            raise _error()
        _regular(executable, executable=True)
        if executable.resolve(strict=True) != executable:
            raise _error()
        expected_properties = [{"name": "WORKING_DIRECTORY", "value": str(expected_directory)}]
        if properties != expected_properties:
            raise _error()
        backtrace = item["backtrace"]
        if type(backtrace) is not int or not 0 <= backtrace < len(graph["nodes"]):
            raise _error()
        node = graph["nodes"][backtrace]
        if type(node) is not dict or node.get("command") != 0 or node.get("file") != 0:
            raise _error()
        relative = _relative(repository, executable)
        binaries.append(relative)
        tests.append((name, (str(executable),)))
        names.add(name)
    if tuple(tests) != _generated_tests(repository):
        raise _error()
    return tuple(tests), tuple(sorted(binaries)), generated


def _versions() -> dict[str, str]:
    try:
        return {
            "python": platform.python_version(),
            "pytest": importlib.metadata.version("pytest"),
            "ctest": importlib.metadata.version("cmake"),
            "platform": platform.platform(),
        }
    except importlib.metadata.PackageNotFoundError:
        raise _error() from None


def _discover(repository: Path, runner: Literal["pytest", "ctest"]) -> _Discovery:
    # These files override pytest's fixed project configuration. They have no
    # supported interpretation in this initial runner contract.
    if runner == "pytest" and any((repository / name).exists() or (repository / name).is_symlink()
                                 for name in ("pytest.ini", ".pytest.ini", "tox.ini", "setup.cfg")):
        raise _error()
    source: list[str] = []
    for root, suffixes in (
        ("include", frozenset({".h", ".hpp"})), ("src", frozenset({".c", ".cc", ".cpp"})),
        ("bindings", frozenset({".c", ".cc", ".cpp", ".py"})),
        ("python/qte", frozenset({".py", ".html", ".js", ".css"})),
    ):
        source.extend(_tree_files(repository, root, suffixes))
    if runner == "pytest":
        tests = list(_tree_files(repository, "tests/python", frozenset({".py"})))
        tests.extend(_tree_files(repository, "tests/fixtures", frozenset({".json", ".csv", ".txt"})))
        tests.extend(_tree_files(repository, "docs", frozenset({".md", ".json"})))
        tests.extend(_tree_files(repository, "examples", frozenset({".json", ".csv"})))
        tests.extend(_tree_files(repository, "scripts", frozenset({".py", ".sh"})))
        for name in ("conftest.py", "tests/conftest.py"):
            conftest = repository / name
            if conftest.exists() or conftest.is_symlink():
                _regular(conftest)
                tests.append(name)
        binaries = _python_runtime(repository)
        generated: tuple[str, ...] = ()
        ctest_tests: tuple[tuple[str, tuple[str, ...]], ...] = ()
    else:
        tests = list(_tree_files(repository, "tests/cpp", frozenset({".cpp", ".hpp", ".txt"})))
        ctest_tests, binaries, generated = _ctest_discovery(repository)
        binaries = tuple(sorted(binaries + _python_runtime(repository)))
    build = tuple(sorted(set(_common_build(repository)) | set(generated)))
    groups = {"source": tuple(sorted(source)), "tests": tuple(sorted(tests)),
              "binaries": binaries, "build": build}
    if any(not groups[name] for name in groups):
        raise _error()
    return _Discovery(groups, _versions(), ctest_tests)


def prepare_test_run(repository: Path, runner: Literal["pytest", "ctest"]) -> PreparedTestRun:
    """Capture a fixed runner's current explicit identity and trusted discovery."""
    if runner not in {"pytest", "ctest"}:
        raise _error()
    try:
        root = Path(repository).resolve(strict=True)
    except OSError:
        raise _error() from None
    if not root.is_dir():
        raise _error()
    before = _discover(root, runner)
    identity = capture_identity(root, before.groups)
    after = _discover(root, runner)
    if before != after or capture_identity(root, after.groups) != identity:
        raise _error()
    value = {
        "schema_version": 1,
        "runner": runner,
        "repository_path": str(root),
        "identity_scope": "explicit_files_v1",
        "source_to_binary": "unknown",
        "identity": identity,
        "versions": before.versions,
        "ctest_tests": [{"name": name, "argv": list(argv)} for name, argv in before.ctest_tests],
    }
    return validate_prepared(value)


def recheck_prepared(repository: Path, prepared: object) -> PreparedTestRun:
    """Freshly rediscover/capture and require exact equality before execution."""
    checked = validate_prepared(prepared)
    try:
        root = Path(repository).resolve(strict=True)
    except OSError:
        raise _error() from None
    if checked.value["repository_path"] != str(root):
        raise _error()
    fresh = prepare_test_run(root, checked.value["runner"])
    if fresh.payload != checked.payload:
        raise _error()
    return fresh
