from __future__ import annotations

import pytest

from qte.gui.artifacts import ArtifactError
from qte.gui.charts import reduce_points


def _points(values, gaps=()):
    return [{"timestamp_ns": index, "row_ordinal": index, "sequence": None, "equity": value,
             "gross_exposure": 0.0, "gap_before": index in gaps} for index, value in enumerate(values)]


def test_reduction_preserves_endpoints_extrema_order_and_duplicates():
    points = _points([10, 8, 12, 9, 7, 15, 11, 13])
    points[3]["timestamp_ns"] = points[2]["timestamp_ns"]
    reduced = reduce_points(points, 6)
    assert reduced[0] is points[0] and reduced[-1] is points[-1]
    assert points[4] in reduced and points[5] in reduced
    assert [point["row_ordinal"] for point in reduced] == sorted(point["row_ordinal"] for point in reduced)


def test_reduction_preserves_both_sides_of_gaps_or_requires_narrower_range():
    points = _points(list(range(20)), gaps=(4, 8, 12, 16))
    reduced = reduce_points(points, 12)
    for index in (4, 8, 12, 16):
        assert points[index - 1] in reduced and points[index] in reduced
    with pytest.raises(ArtifactError) as narrow:
        reduce_points(points, 6)
    assert narrow.value.code == "chart_range_too_wide"


def test_small_series_is_not_modified():
    points = _points([1, 2, 3])
    assert reduce_points(points, 3) is points
