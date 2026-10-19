from __future__ import annotations

from dataclasses import FrozenInstanceError
from pathlib import Path
import subprocess
import sys

import pytest

from qte.gui.junit import INPUT_LIMIT, JUnitError, parse_junit


def test_actual_pytest_shape_preserves_duplicate_rows_and_dominant_events():
    data = b'''<?xml version="1.0" encoding="utf-8"?>
    <testsuites name="pytest tests"><testsuite name="pytest" errors="1" failures="1"
      skipped="1" tests="4" time="1.25" timestamp="2026-10-16T00:00:00Z" hostname="local">
      <testcase classname="tests.test_example" name="test_ok" time="0.001"/>
      <testcase classname="tests.test_example" name="test_duplicate" time="0.2">
        <failure message="call failed" type="AssertionError">plain failure</failure>
      </testcase>
      <testcase classname="tests.test_example" name="test_duplicate" time="0.3">
        <error message="teardown failed" type="RuntimeError">plain error</error>
      </testcase>
      <testcase classname="tests.test_example" name="test_xfail" time="0">
        <skipped type="pytest.xfail" message="expected failure"/>
      </testcase>
    </testsuite></testsuites>'''
    report = parse_junit(data, "pytest")
    assert (report.tests, report.passed, report.failed, report.errors, report.skipped) == (4, 1, 1, 1, 1)
    assert report.xfailed == 1
    assert (report.event_failures, report.event_errors, report.event_skipped) == (1, 1, 1)
    assert (report.declared_tests, report.declared_failures, report.declared_errors,
            report.declared_skipped, report.declared_disabled) == (4, 1, 1, 1, 0)
    assert [case.ordinal for case in report.cases] == [0, 1, 2, 3]
    assert report.cases[1].name == report.cases[2].name == "test_duplicate"
    assert report.cases[2].events[0].text == "plain error"
    with pytest.raises(FrozenInstanceError):
        report.cases[0].name = "changed"  # type: ignore[misc]


def test_multi_event_counts_are_preserved_while_case_status_uses_dominance():
    data = b'''<testsuites><testsuite tests="2" failures="1" errors="2" skipped="2">
      <testcase classname="c" name="mixed" time="1e-3">
        <skipped type="pytest.xfail"/><failure/><error/><error/>
      </testcase>
      <testcase classname="c" name="ordinary-skip"><skipped type="pytest.skip"/></testcase>
    </testsuite></testsuites>'''
    report = parse_junit(data, "pytest")
    assert [case.status for case in report.cases] == ["error", "skipped"]
    assert report.xfailed == 0  # xfail is a subset of dominant skipped cases
    assert (report.event_failures, report.event_errors, report.event_skipped) == (1, 2, 2)
    assert (report.failed, report.errors, report.skipped) == (0, 1, 1)


def test_actual_ctest_shape_and_output_nodes_are_supported():
    data = b'''<?xml version="1.0" encoding="UTF-8"?>
      <testsuite name="(empty)" tests="2" failures="1" disabled="0" skipped="0"
        hostname="" time="0.02" timestamp="2026-10-16T00:00:00">
        <testcase name="qte.ok" classname="qte.ok" time="0.004" status="run">
          <properties/><system-out>all tests passed</system-out>
        </testcase>
        <testcase name="qte.bad" classname="qte.bad" time="0.005" status="fail">
          <properties><property name="fixture" value="native"/></properties>
          <failure message="Exit code 1">bounded output</failure><system-out>details</system-out>
        </testcase>
      </testsuite>'''
    report = parse_junit(data, "ctest")
    assert (report.tests, report.passed, report.failed, report.errors, report.skipped) == (2, 1, 1, 0, 0)
    assert report.cases[1].events[0].message == "Exit code 1"
    assert report.declared_disabled == 0


def test_actual_ctest_failure_disabled_and_skip_statuses_remain_distinct():
    data = b'''<?xml version="1.0" encoding="UTF-8"?>
      <testsuite name="(empty)" tests="3" failures="1" disabled="1" skipped="1"
        hostname="" time="0" timestamp="2026-10-16T00:00:00">
        <testcase name="bad" classname="bad" time="0.01" status="fail">
          <failure message="Failed"/><properties/><system-out/>
        </testcase>
        <testcase name="disabled" classname="disabled" time="0" status="disabled">
          <properties/><system-out>Disabled</system-out>
        </testcase>
        <testcase name="skipped" classname="skipped" time="0.01" status="notrun">
          <skipped message="SKIP_RETURN_CODE=77"/><properties/><system-out/>
        </testcase>
      </testsuite>'''
    report = parse_junit(data, "ctest")
    assert [case.status for case in report.cases] == ["failed", "skipped", "skipped"]
    assert (report.declared_failures, report.declared_disabled, report.declared_skipped) == (1, 1, 1)
    assert (report.failed, report.skipped, report.passed) == (1, 2, 0)


def test_pytest_collection_error_and_module_skip_cannot_become_passes():
    data = b'''<?xml version="1.0" encoding="utf-8"?>
      <testsuites name="pytest tests"><testsuite name="pytest" errors="1" failures="0"
        skipped="1" tests="2" time="0.1" timestamp="2026-10-16T00:00:00Z" hostname="local">
        <testcase classname="" name="test_collect" time="0">
          <error message="collection failure">SyntaxError: invalid syntax</error>
        </testcase>
        <testcase classname="" name="test_skip" time="0">
          <skipped message="collection skipped">Skipped: module unavailable</skipped>
        </testcase>
      </testsuite></testsuites>'''
    report = parse_junit(data, "pytest")
    assert [case.status for case in report.cases] == ["error", "skipped"]
    assert report.passed == 0


def test_zero_and_all_skipped_reports_do_not_create_passed_cases():
    zero = parse_junit(
        b'<testsuites><testsuite tests="0" failures="0" errors="0" skipped="0"/></testsuites>',
        "pytest",
    )
    skipped = parse_junit(
        b'<testsuites><testsuite tests="1" failures="0" errors="0" skipped="1">'
        b'<testcase classname="c" name="s"><skipped/></testcase></testsuite></testsuites>',
        "pytest",
    )
    assert (zero.tests, zero.passed) == (0, 0)
    assert (skipped.tests, skipped.passed, skipped.skipped) == (1, 0, 1)


@pytest.mark.parametrize("change", [
    b'<testsuites><testsuite tests="2" failures="0" errors="0" skipped="0">'
    b'<testcase classname="c" name="x"/></testsuite></testsuites>',
    b'<testsuites><testsuite tests="1" failures="1" errors="0" skipped="0">'
    b'<testcase classname="c" name="x"/></testsuite></testsuites>',
    b'<testsuites><testsuite tests="1" failures="0" errors="1" skipped="0">'
    b'<testcase classname="c" name="x"><failure/></testcase></testsuite></testsuites>',
    b'<testsuites><testsuite tests="1" failures="0" errors="0" skipped="1">'
    b'<testcase classname="c" name="x"/></testsuite></testsuites>',
    b'<testsuites tests="2"><testsuite tests="1" failures="0" errors="0" skipped="0">'
    b'<testcase classname="c" name="x"/></testsuite></testsuites>',
    b'<testsuite tests="1" failures="0" disabled="1" skipped="0">'
    b'<testcase classname="c" name="x" status="run"/></testsuite>',
])
def test_declared_suite_and_root_aggregates_must_match(change):
    runner = "ctest" if change.startswith(b"<testsuite ") else "pytest"
    with pytest.raises(JUnitError, match="count"):
        parse_junit(change, runner)


@pytest.mark.parametrize("data", [
    b"not xml",
    b'<?xml version="1.0" encoding="UTF-16"?><testsuites/>',
    b'<?xml version="1.0"?><!DOCTYPE x [<!ENTITY e "boom">]><testsuites/>',
    b'<?xml version="1.0"?><?work x?><testsuites/>',
    b'<testsuites xmlns="urn:x"/>',
    b'<testsuites><!-- hidden --></testsuites>',
    b'<testsuites><unknown/></testsuites>',
    b'<testsuites unknown="x"/>',
    b'<testsuites/>',
    b'<testsuite tests="0" failures="0" disabled="0" skipped="0" errors="0"/>',
])
def test_rejects_malformed_active_or_unknown_xml(data):
    runner = "ctest" if data.startswith(b"<testsuite ") else "pytest"
    with pytest.raises(JUnitError):
        parse_junit(data, runner)


@pytest.mark.parametrize("time", ["-0.1", "nan", "inf", "1e999", "one second"])
def test_rejects_negative_and_nonfinite_times(time):
    data = (f'<testsuites><testsuite tests="1" failures="0" errors="0" skipped="0">'
            f'<testcase classname="c" name="x" time="{time}"/></testsuite></testsuites>').encode()
    with pytest.raises(JUnitError, match="duration"):
        parse_junit(data, "pytest")


def test_limits_runner_and_input_type_are_explicit():
    with pytest.raises(JUnitError, match="runner"):
        parse_junit(b"<testsuites/>", "other")
    with pytest.raises(TypeError):
        parse_junit("<testsuites/>" , "pytest")  # type: ignore[arg-type]
    with pytest.raises(JUnitError, match="size limit"):
        parse_junit(b" " * (INPUT_LIMIT + 1), "pytest")


def test_depth_node_case_and_descriptor_bounds(monkeypatch):
    import qte.gui.junit as junit

    monkeypatch.setattr(junit, "DEPTH_LIMIT", 3)
    with pytest.raises(JUnitError, match="parser limit"):
        parse_junit(b'<testsuites><testsuite><properties><property name="x"/></properties></testsuite></testsuites>', "pytest")
    monkeypatch.setattr(junit, "DEPTH_LIMIT", 16)
    monkeypatch.setattr(junit, "NODE_LIMIT", 3)
    with pytest.raises(JUnitError, match="parser limit"):
        parse_junit(b'<testsuites><testsuite><properties><property name="x"/></properties></testsuite></testsuites>', "pytest")
    monkeypatch.setattr(junit, "NODE_LIMIT", 100_000)
    monkeypatch.setattr(junit, "CASE_LIMIT", 1)
    with pytest.raises(JUnitError, match="parser limit"):
        parse_junit(b'<testsuites><testsuite><testcase/><testcase/></testsuite></testsuites>', "pytest")


def test_failure_descriptor_is_bounded_plaintext():
    body = b"x" * (64 * 1024 + 20)
    report = parse_junit(
        b'<testsuites><testsuite tests="1" failures="1" errors="0" skipped="0">'
        b'<testcase classname="c" name="x"><failure>' + body +
        b'</failure></testcase></testsuite></testsuites>',
        "pytest",
    )
    assert report.cases[0].events[0].text.endswith("...[truncated]")
    assert len(report.cases[0].events[0].text or "") < len(body)


def test_namespace_like_plain_log_text_is_not_an_xml_declaration():
    data = (b'<testsuites><testsuite tests="1" failures="0" errors="0" skipped="0">'
            b'<testcase classname="c" name="p"><system-out>Example xmlns="urn:example"'
            b'</system-out></testcase></testsuite></testsuites>')
    assert parse_junit(data, "pytest").passed == 1


@pytest.mark.parametrize("status,child", [
    ("run", "<failure/>"),
    ("fail", ""),
    ("disabled", "<skipped/>"),
    ("notrun", ""),
    ("invented", ""),
])
def test_ctest_status_cannot_hide_or_invent_an_outcome(status, child):
    failures = int(child == "<failure/>")
    skipped = int(child == "<skipped/>")
    disabled = int(status == "disabled")
    data = (f'<testsuite tests="1" failures="{failures}" disabled="{disabled}" skipped="{skipped}">'
            f'<testcase classname="c" name="x" status="{status}">{child}</testcase></testsuite>').encode()
    with pytest.raises(JUnitError, match="CTest testcase status"):
        parse_junit(data, "ctest")


def _producer_run(command, cwd):
    return subprocess.run(command, cwd=cwd, capture_output=True, timeout=30, env={
        "PYTEST_DISABLE_PLUGIN_AUTOLOAD": "1", "PYTHONNOUSERSITE": "1",
        "PYTHONDONTWRITEBYTECODE": "1", "LANG": "C.UTF-8",
    })


def test_installed_pytest_emits_duplicate_overlap_and_multiple_error_records(tmp_path):
    (tmp_path / "pytest.ini").write_text("[pytest]\n")
    (tmp_path / "test_producer.py").write_text('''import pytest
@pytest.fixture
def broken():
    yield
    raise RuntimeError("teardown")
@pytest.fixture
def double_error(request):
    def finalizer():
        raise RuntimeError("finalizer")
    request.addfinalizer(finalizer)
    raise RuntimeError("setup")
def test_fail(broken):
    assert False
def test_skip(broken):
    pytest.skip("skip then teardown error")
def test_double(double_error):
    pass
@pytest.mark.xfail
def test_xfail():
    assert False
@pytest.mark.xfail(strict=False)
def test_xpass():
    pass
def test_pass():
    pass
''')
    result = _producer_run([sys.executable, "-m", "pytest", "test_producer.py", "-c", "pytest.ini",
                            "-p", "no:cacheprovider", "-o", "xfail_strict=true", "--junitxml=results.xml"], tmp_path)
    assert result.returncode == 1, result.stderr.decode(errors="replace")
    report = parse_junit((tmp_path / "results.xml").read_bytes(), "pytest")
    assert (report.tests, report.passed, report.failed, report.errors, report.skipped) == (7, 2, 1, 3, 1)
    assert (report.event_errors, report.event_skipped, report.xfailed) == (4, 2, 1)
    assert report.declared_errors == 4
    assert len([case for case in report.cases if case.name == "test_fail"]) == 2
    # Ordinary XML cannot distinguish explicitly non-strict XPASS from a pass.
    assert next(case for case in report.cases if case.name == "test_xpass").status == "passed"


def test_installed_pytest_collection_failure_has_empty_classname(tmp_path):
    (tmp_path / "pytest.ini").write_text("[pytest]\n")
    (tmp_path / "test_collect.py").write_text("raise RuntimeError('collection fixture')\n")
    result = _producer_run([sys.executable, "-m", "pytest", "test_collect.py", "-c", "pytest.ini",
                            "-p", "no:cacheprovider", "--junitxml=results.xml"], tmp_path)
    assert result.returncode == 2
    report = parse_junit((tmp_path / "results.xml").read_bytes(), "pytest")
    assert (report.tests, report.errors, report.passed) == (1, 1, 0)
    assert report.cases[0].classname == ""
    assert report.cases[0].events[0].message == "collection failure"


def test_installed_pytest_module_skip_has_junit_row_but_exit_five(tmp_path):
    (tmp_path / "pytest.ini").write_text("[pytest]\n")
    (tmp_path / "test_skip.py").write_text("import pytest\npytest.skip('fixture', allow_module_level=True)\n")
    result = _producer_run([sys.executable, "-m", "pytest", "test_skip.py", "-c", "pytest.ini",
                            "-p", "no:cacheprovider", "--junitxml=results.xml"], tmp_path)
    assert result.returncode == 5
    report = parse_junit((tmp_path / "results.xml").read_bytes(), "pytest")
    assert (report.tests, report.skipped, report.passed) == (1, 1, 0)


def test_installed_ctest_emits_failed_disabled_skipped_and_passing_cases(tmp_path):
    native_ctest = Path(__file__).parents[2] / ".venv/bin/ctest"
    (tmp_path / "CTestTestfile.cmake").write_text(f'''
add_test(passed "{sys.executable}" "-c" "pass")
add_test(failed "{sys.executable}" "-c" "raise SystemExit(1)")
add_test(skipped "{sys.executable}" "-c" "raise SystemExit(77)")
set_tests_properties(skipped PROPERTIES SKIP_RETURN_CODE 77)
add_test(disabled "{sys.executable}" "-c" "raise SystemExit(1)")
set_tests_properties(disabled PROPERTIES DISABLED TRUE)
''')
    result = _producer_run([str(native_ctest), "--test-dir", str(tmp_path),
                            "--output-junit", str(tmp_path / "results.xml")], tmp_path)
    assert result.returncode != 0
    report = parse_junit((tmp_path / "results.xml").read_bytes(), "ctest")
    assert (report.tests, report.passed, report.failed, report.skipped) == (4, 1, 1, 2)
    assert (report.declared_disabled, report.declared_skipped) == (1, 1)
