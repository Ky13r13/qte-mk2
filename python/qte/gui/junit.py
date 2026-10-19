"""Strict, bounded parsing for the fixed pytest and CTest JUnit producers.

Pytest's JUnit XML does not identify a non-strict XPASS.  Such a testcase is
therefore reported as passed. ``xfail_strict=true`` makes the default strict,
but a test's explicit ``strict=False`` still overrides it. An explicit ``pytest.xfail``
``skipped`` node is retained as skipped and counted in ``xfailed``.
"""

from __future__ import annotations

from dataclasses import dataclass
import math
import re
from typing import Literal
import xml.etree.ElementTree as ET


INPUT_LIMIT = 16 * 1024 * 1024
DEPTH_LIMIT = 16
NODE_LIMIT = 100_000
CASE_LIMIT = 10_000
IDENTIFIER_LIMIT = 4096
DESCRIPTOR_LIMIT = 64 * 1024

Status = Literal["passed", "skipped", "failed", "error"]
IssueKind = Literal["failure", "error", "skipped"]


class JUnitError(ValueError):
    """The input is not one of the supported, internally consistent reports."""


@dataclass(frozen=True)
class JUnitIssue:
    kind: IssueKind
    message: str | None
    type: str | None
    text: str | None


@dataclass(frozen=True)
class JUnitCase:
    ordinal: int
    name: str
    classname: str
    status: Status
    duration: str | None
    events: tuple[JUnitIssue, ...]
    xfail: bool


@dataclass(frozen=True)
class JUnitReport:
    runner: Literal["pytest", "ctest"]
    cases: tuple[JUnitCase, ...]
    tests: int
    passed: int
    failed: int
    errors: int
    skipped: int
    xfailed: int
    event_failures: int
    event_errors: int
    event_skipped: int
    declared_tests: int
    declared_failures: int
    declared_errors: int
    declared_skipped: int
    declared_disabled: int


_XML_DECLARATION = re.compile(br"\A(?:\xef\xbb\xbf)?<\?xml\s+([^?]*)\?>")
_ENCODING = re.compile(br"(?:\A|\s)encoding\s*=\s*(['\"])([^'\"]+)\1", re.IGNORECASE)
_FORBIDDEN_DECLARATION = re.compile(br"<!\s*(?:DOCTYPE|ENTITY)\b", re.IGNORECASE)
_COUNT = re.compile(r"(?:0|[1-9][0-9]*)\Z")

_ROOT_ATTRS = frozenset({"name", "time", "tests", "failures", "errors", "skipped", "disabled"})
_SUITE_ATTRS = frozenset({
    "name", "tests", "failures", "errors", "skipped", "disabled",
    "time", "timestamp", "hostname", "id", "package",
})
_PYTEST_CASE_ATTRS = frozenset({"name", "classname", "time", "file", "line", "assertions"})
_CTEST_CASE_ATTRS = frozenset({"name", "classname", "time", "status"})
_ISSUE_ATTRS = frozenset({"message", "type"})


def _parse_xml(data: bytes) -> ET.Element:
    if not isinstance(data, bytes):
        raise TypeError("JUnit input must be bytes")
    if not data or len(data) > INPUT_LIMIT:
        raise JUnitError("JUnit input is empty or exceeds the size limit")
    try:
        data.decode("utf-8-sig")
    except UnicodeDecodeError as exc:
        raise JUnitError("JUnit input must be UTF-8") from exc

    declaration = _XML_DECLARATION.match(data)
    if data.startswith((b"<?xml", b"\xef\xbb\xbf<?xml")) and declaration is None:
        raise JUnitError("malformed XML declaration")
    remainder = data[declaration.end():] if declaration else data
    if declaration:
        encoding = _ENCODING.search(declaration.group(1))
        if encoding and encoding.group(2).lower().replace(b"-", b"") != b"utf8":
            raise JUnitError("unsupported XML encoding")
    if b"<?" in remainder:
        raise JUnitError("XML processing instructions are forbidden")
    if _FORBIDDEN_DECLARATION.search(data):
        raise JUnitError("XML DTDs and entity declarations are forbidden")
    parser = ET.XMLPullParser(events=("start", "end", "comment", "pi", "start-ns"))
    root: ET.Element | None = None
    depth = 0
    nodes = 0
    cases = 0

    def consume() -> None:
        nonlocal root, depth, nodes, cases
        for event, element in parser.read_events():
            if event == "start-ns":
                raise JUnitError("XML namespaces are unsupported")
            if event in {"comment", "pi"}:
                raise JUnitError("XML comments and processing instructions are unsupported")
            if event == "start":
                if root is None:
                    root = element
                depth += 1
                nodes += 1
                if element.tag == "testcase":
                    cases += 1
                if depth > DEPTH_LIMIT or nodes > NODE_LIMIT or cases > CASE_LIMIT:
                    raise JUnitError("JUnit structure exceeds a parser limit")
            else:
                depth -= 1

    try:
        for offset in range(0, len(data), 65536):
            parser.feed(data[offset:offset + 65536])
            consume()
        parser.close()
        consume()
    except ET.ParseError as exc:
        raise JUnitError("malformed JUnit XML") from exc
    if root is None or depth != 0:
        raise JUnitError("malformed JUnit XML")
    return root


def _check_name(name: object) -> str:
    if not isinstance(name, str) or not name or "{" in name or "}" in name or ":" in name:
        raise JUnitError("namespaced or invalid XML name")
    return name


def _attributes(element: ET.Element, allowed: frozenset[str]) -> None:
    for name in element.attrib:
        if _check_name(name) not in allowed:
            raise JUnitError(f"unsupported {element.tag} attribute")


def _only_whitespace(value: str | None, label: str) -> None:
    if value and value.strip():
        raise JUnitError(f"unexpected text in {label}")


def _plain(value: str | None, *, limit: int = DESCRIPTOR_LIMIT) -> str | None:
    if value is None:
        return None
    if len(value) <= limit:
        return value
    return value[:limit] + "\n...[truncated]"


def _required_text(element: ET.Element, name: str, *, empty: bool = False) -> str:
    value = element.attrib.get(name)
    if value is None or (not empty and not value) or len(value) > IDENTIFIER_LIMIT:
        raise JUnitError(f"testcase {name} is missing or oversized")
    return value


def _count(element: ET.Element, name: str, *, required: bool, default: int = 0) -> int:
    value = element.attrib.get(name)
    if value is None:
        if required:
            raise JUnitError(f"testsuite is missing {name}")
        return default
    if not _COUNT.fullmatch(value):
        raise JUnitError(f"invalid declared {name} count")
    result = int(value)
    if result > NODE_LIMIT:
        raise JUnitError(f"declared {name} count exceeds the limit")
    return result


def _time(element: ET.Element) -> str | None:
    value = element.attrib.get("time")
    if value is None:
        return None
    try:
        number = float(value)
    except ValueError as exc:
        raise JUnitError("invalid JUnit duration") from exc
    if not math.isfinite(number) or number < 0:
        raise JUnitError("invalid JUnit duration")
    return value


def _validate_leaf(element: ET.Element, allowed: frozenset[str]) -> None:
    _attributes(element, allowed)
    if list(element):
        raise JUnitError(f"unsupported child of {element.tag}")


def _validate_properties(element: ET.Element) -> None:
    _attributes(element, frozenset())
    _only_whitespace(element.text, "properties")
    for child in element:
        if _check_name(child.tag) != "property":
            raise JUnitError("unsupported properties child")
        _validate_leaf(child, frozenset({"name", "value"}))
        if "name" not in child.attrib or len(child.attrib["name"]) > IDENTIFIER_LIMIT:
            raise JUnitError("property name is missing or oversized")
        _only_whitespace(child.text, "property")
        _only_whitespace(child.tail, "properties")


def _case(element: ET.Element, runner: str, ordinal: int) -> tuple[JUnitCase, dict[str, int]]:
    _attributes(element, _PYTEST_CASE_ATTRS if runner == "pytest" else _CTEST_CASE_ATTRS)
    name = _required_text(element, "name")
    classname = _required_text(element, "classname", empty=runner == "pytest")
    duration = _time(element)
    ctest_status = element.attrib.get("status")
    if runner == "ctest" and ctest_status not in {"run", "fail", "disabled", "notrun"}:
        raise JUnitError("unsupported CTest testcase status")
    _only_whitespace(element.text, "testcase")

    events: list[JUnitIssue] = []
    present = {"failure": 0, "error": 0, "skipped": 0}
    disabled = int(runner == "ctest" and ctest_status == "disabled")
    for child in element:
        tag = _check_name(child.tag)
        if tag == "properties":
            _validate_properties(child)
        elif tag in {"system-out", "system-err"}:
            _validate_leaf(child, frozenset())
        elif tag in present:
            if runner == "ctest" and tag == "error":
                raise JUnitError("CTest reports do not declare error outcomes")
            _validate_leaf(child, _ISSUE_ATTRS)
            present[tag] += 1
            events.append(JUnitIssue(
                kind=tag,  # type: ignore[arg-type]
                message=_plain(child.attrib.get("message"), limit=IDENTIFIER_LIMIT),
                type=_plain(child.attrib.get("type"), limit=IDENTIFIER_LIMIT),
                text=_plain(child.text),
            ))
        else:
            raise JUnitError("unsupported testcase child")
        _only_whitespace(child.tail, "testcase")

    if runner == "ctest":
        consistent = (
            (ctest_status == "run" and not any(present.values()))
            or (ctest_status == "fail" and bool(present["failure"]) and not present["skipped"])
            or (ctest_status == "disabled" and not any(present.values()))
            or (ctest_status == "notrun" and bool(present["skipped"]) and not present["failure"])
        )
        if not consistent:
            raise JUnitError("CTest testcase status disagrees with its outcome nodes")

    if present["error"]:
        status: Status = "error"
    elif present["failure"]:
        status = "failed"
    elif present["skipped"] or disabled:
        status = "skipped"
    else:
        status = "passed"
    xfail = status == "skipped" and any(
        event.kind == "skipped" and event.type == "pytest.xfail" for event in events
    )
    return JUnitCase(ordinal, name, classname, status, duration, tuple(events), xfail), {
        "failure": int(bool(present["failure"])),
        "error": int(bool(present["error"])),
        "skipped": int(bool(present["skipped"])),
        "disabled": disabled,
        "event_failure": present["failure"],
        "event_error": present["error"],
        "event_skipped": present["skipped"],
    }


def _suite(element: ET.Element, runner: str, ordinal: int) -> tuple[list[JUnitCase], dict[str, int]]:
    _attributes(element, _SUITE_ATTRS)
    _time(element)
    required = runner == "ctest"
    declared = {
        "tests": _count(element, "tests", required=True),
        "failure": _count(element, "failures", required=True),
        "error": _count(element, "errors", required=not required),
        "skipped": _count(element, "skipped", required=True),
        "disabled": _count(element, "disabled", required=required),
    }
    if runner == "ctest" and "errors" in element.attrib:
        raise JUnitError("CTest testsuite must not declare errors")
    _only_whitespace(element.text, "testsuite")

    cases: list[JUnitCase] = []
    actual = {name: 0 for name in ("failure", "error", "skipped", "disabled",
                                   "event_failure", "event_error", "event_skipped")}
    saw_case = False
    for child in element:
        tag = _check_name(child.tag)
        if tag == "testcase":
            saw_case = True
            case, counts = _case(child, runner, ordinal + len(cases))
            cases.append(case)
            for name, value in counts.items():
                actual[name] += value
        elif tag == "properties" and not saw_case:
            _validate_properties(child)
        elif tag in {"system-out", "system-err"}:
            _validate_leaf(child, frozenset())
        else:
            raise JUnitError("unsupported testsuite child")
        _only_whitespace(child.tail, "testsuite")

    expected = {"tests": len(cases), "disabled": actual["disabled"],
                **{name: actual[f"event_{name}"] for name in ("failure", "error", "skipped")}}
    if declared != expected:
        raise JUnitError("declared testsuite counts do not match testcase outcomes")
    return cases, {**declared, **{name: actual[name] for name in ("event_failure", "event_error", "event_skipped")}}


def parse_junit(data: bytes, runner: str) -> JUnitReport:
    """Parse one fixed-runner JUnit report without inferring the process outcome."""
    if runner not in {"pytest", "ctest"}:
        raise JUnitError("unsupported JUnit runner")
    root = _parse_xml(data)
    root_tag = _check_name(root.tag)
    if (runner == "pytest" and root_tag != "testsuites") or (runner == "ctest" and root_tag != "testsuite"):
        raise JUnitError("unexpected JUnit root")

    cases: list[JUnitCase] = []
    totals = {name: 0 for name in (
        "tests", "failure", "error", "skipped", "disabled",
        "event_failure", "event_error", "event_skipped",
    )}
    if runner == "ctest":
        parsed, counts = _suite(root, runner, 0)
        cases.extend(parsed)
        totals.update(counts)
    else:
        _attributes(root, _ROOT_ATTRS)
        _time(root)
        _only_whitespace(root.text, "testsuites")
        suite_count = 0
        for child in root:
            if _check_name(child.tag) != "testsuite":
                raise JUnitError("unsupported testsuites child")
            suite_count += 1
            parsed, counts = _suite(child, runner, len(cases))
            cases.extend(parsed)
            for name, value in counts.items():
                totals[name] += value
            _only_whitespace(child.tail, "testsuites")
        if suite_count == 0:
            raise JUnitError("pytest JUnit report has no testsuite")
        for attr, key in (("tests", "tests"), ("failures", "failure"), ("errors", "error"),
                          ("skipped", "skipped"), ("disabled", "disabled")):
            if attr in root.attrib and _count(root, attr, required=True) != totals[key]:
                raise JUnitError("testsuites aggregate counts do not match child suites")

    summary = {status: sum(case.status == status for case in cases)
               for status in ("passed", "failed", "error", "skipped")}
    return JUnitReport(
        runner=runner,  # type: ignore[arg-type]
        cases=tuple(cases),
        tests=len(cases),
        passed=summary["passed"],
        failed=summary["failed"],
        errors=summary["error"],
        skipped=summary["skipped"],
        xfailed=sum(case.xfail for case in cases),
        event_failures=totals["event_failure"],
        event_errors=totals["event_error"],
        event_skipped=totals["event_skipped"],
        declared_tests=totals["tests"],
        declared_failures=totals["failure"],
        declared_errors=totals["error"],
        declared_skipped=totals["skipped"],
        declared_disabled=totals["disabled"],
    )
