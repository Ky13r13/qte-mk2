"""Deterministic display-only reduction for recorded equity points."""

from __future__ import annotations

from .artifacts import ArtifactError


def reduce_points(points: list[dict], max_points: int) -> list[dict]:
    """Preserve endpoints, gaps and bucket first/min/max/last in source order."""
    if len(points) <= max_points:
        return points
    if max_points < 4:
        raise ArtifactError("chart_range_too_wide", 422, "Request a narrower chart range.")
    required = {0, len(points) - 1}
    for index, point in enumerate(points):
        if point["gap_before"] and index:
            required.update((index - 1, index))
    if len(required) > max_points:
        raise ArtifactError("chart_range_too_wide", 422, "Request a narrower chart range.")

    best: set[int] | None = None
    for bucket_count in range(max(1, max_points // 4), 0, -1):
        selected = set(required)
        for bucket in range(bucket_count):
            start = bucket * len(points) // bucket_count
            end = (bucket + 1) * len(points) // bucket_count
            indices = range(start, end)
            if start == end:
                continue
            selected.update((start, end - 1,
                             min(indices, key=lambda i: (points[i]["equity"], i)),
                             max(indices, key=lambda i: (points[i]["equity"], -i))))
        if len(selected) <= max_points:
            best = selected
            break
    if best is None:
        raise ArtifactError("chart_range_too_wide", 422, "Request a narrower chart range.")
    return [points[index] for index in sorted(best)]
