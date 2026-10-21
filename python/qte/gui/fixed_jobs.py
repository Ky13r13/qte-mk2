"""Shared scheduler hooks for the two implemented fixed local job families."""
from types import SimpleNamespace


KINDS = frozenset({"test_run", "benchmark"})


def support(kind):
    # Lazy imports preserve existing test monkeypatch seams and optional GUI use.
    if kind == "test_run":
        from . import test_preflight, test_snapshots, test_jobs
        def write(repository, job_id, prepared):
            return test_jobs.snapshot_value(test_snapshots.write_test_snapshot(repository, job_id, prepared))
        def recheck(repository, job_id, hashes):
            snapshot, prepared = test_snapshots.recheck_test_snapshot(repository, job_id, hashes)
            return test_jobs.snapshot_value(snapshot), prepared
        return SimpleNamespace(validate=test_preflight.validate_prepared, prepared_type=test_preflight.PreparedTestRun,
                               write=write, recheck=recheck, verify=test_jobs.verify_test_job_output)
    if kind == "benchmark":
        from . import benchmark_inputs, benchmark_evidence
        return SimpleNamespace(validate=benchmark_inputs.validate_prepared, prepared_type=benchmark_inputs.PreparedBenchmark,
                               write=benchmark_inputs.write_snapshot, recheck=benchmark_inputs.recheck_snapshot,
                               verify=benchmark_evidence.verify_job_output)
    raise ValueError("Unsupported fixed job.")
