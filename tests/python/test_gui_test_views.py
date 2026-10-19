"""Bounded System projections preserve case counts, not unbounded producer text."""
import json

from qte.gui.junit import JUnitCase, JUnitIssue, parse_junit
from qte.gui.test_views import _case_page


def test_valid_long_duration_is_omitted_not_cut_to_another_number():
    data = ('<testsuites><testsuite name="fixture" tests="1" failures="0" errors="0" skipped="0">'
            '<testcase name="case" classname="fixture" time="0.' + '0' * 1000000 +
            '"/></testsuite></testsuites>').encode()
    report = parse_junit(data, "pytest")
    page, truncated = _case_page(report.cases, 0, 25)
    assert page[0]["duration"] is None
    assert truncated is True
    assert len(json.dumps(page)) < 1000


def test_page_budget_bounds_all_text_and_events_without_dropping_case_rows():
    issue = JUnitIssue("error", "m" * 65536, None, "t" * 65536)
    cases = tuple(JUnitCase(index, "n" * 4096, "c" * 4096, "error", "0.1", (issue,) * 100, False)
                  for index in range(200))
    page, truncated = _case_page(cases, 0, 200)
    assert truncated is True and len(page) == 200
    assert sum(len(row["issues"]) for row in page) == 200
    assert sum(len(row["name"]) + len(row["classname"]) +
               sum(len(event["message"] or "") + len(event["text"] or "") for event in row["issues"])
               for row in page) == 65536
    assert [row["ordinal"] for row in page] == list(range(200))
