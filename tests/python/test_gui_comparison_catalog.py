"""Real comparison exports keep raw holdout and derived content guarded."""
from __future__ import annotations

import asyncio
from hashlib import sha256
import json
from pathlib import Path
import shutil

import httpx
import pytest

from qte.comparison_export import run_comparison
from qte.gui import create_app
from qte.gui.artifacts import ArtifactError
from qte.gui.catalog import Catalog


ROOT = Path(__file__).parents[2]
BASE = "http://127.0.0.1:8765"


def export(repo, example="experiment"):
    return run_comparison(ROOT / f"examples/comparisons/{example}.json", repo / "build/comparison")


def read_json(path):
    return json.loads(path.read_text())


def rehash(root):
    files = {p.relative_to(root).as_posix(): sha256(p.read_bytes()).hexdigest()
             for p in root.rglob("*") if p.is_file() and p.name != "complete.json"}
    (root / "complete.json").write_text(json.dumps({"schema_version": 1, "sha256": files}))


def file_id(name):
    return sha256(name.encode()).hexdigest()


@pytest.mark.parametrize("example", ["experiment", "sma"])
def test_verified_roles_do_not_publish_report_facts_or_replay_engine(tmp_path, example, monkeypatch):
    root = export(tmp_path, example)
    import qte

    def forbidden(*args, **kwargs):
        pytest.fail("Catalog reads must never run strategies")

    monkeypatch.setattr(qte.BacktestEngine, "run", forbidden)
    catalog = Catalog(tmp_path)
    item = catalog.register("build/comparison")
    assert item["kind"] == "comparison_export_v1"
    assert item["status"] == {
        "export": "complete", "integrity": "verified", "evidence": "synthetic",
        "scenario_outcome": "unknown", "selection": "unknown", "holdout_evaluation": "unknown",
        "disclosure": "unknown", "code_match": "same", "combined_status": "complete; verified; synthetic",
    }
    manifest = read_json(root / "manifest.json")
    protected, mixed = set(manifest["holdout_files"]), set(manifest["mixed_role_files"]) | {"complete.json"}
    actual = catalog.inspect(item["id"])
    assert actual.protected == protected
    assert actual.mixed == mixed
    assert actual.known == set(actual.snapshots)
    for row in item["files"]:
        assert row["protection"] == ("protected" if row["name"] in protected else
                                     "mixed" if row["name"] in mixed else "unprotected")
        if row["name"] in protected | mixed:
            with pytest.raises(ArtifactError) as denied:
                catalog.read_file(item["id"], row["id"])
            assert denied.value.code == "holdout_protected"
        else:
            assert catalog.read_file(item["id"], row["id"])[0] == (root / row["name"]).read_bytes()
    assert not list(catalog.disclosures.root.glob("revealed_outside_protocol-*.json"))
    assert "files" not in catalog.list()[0][0]
    # A comparison is not a synthetic-lab projection, even after disclosure.
    with pytest.raises(ArtifactError, match="strategy-lab view unavailable"):
        catalog.experiment_view(item["id"])


@pytest.mark.parametrize("mutation", ["incomplete", "corrupt", "extra", "role_map", "future_version"])
def test_incomplete_and_invalid_artifacts_remain_visible_and_fully_guarded(tmp_path, mutation):
    root = export(tmp_path)
    if mutation == "incomplete":
        (root / "complete.json").unlink()
    elif mutation == "corrupt":
        (root / "summary.json").write_text("{}")
    elif mutation == "extra":
        (root / "unclassified.txt").write_text("this is not an authorized projection")
        rehash(root)
    elif mutation == "role_map":
        manifest = read_json(root / "manifest.json")
        manifest["holdout_files"] = []
        (root / "manifest.json").write_text(json.dumps(manifest))
        rehash(root)
    else:
        marker = read_json(root / "comparison_artifact.json")
        marker["schema_version"] = 99
        (root / "comparison_artifact.json").write_text(json.dumps(marker))
        rehash(root)
    catalog = Catalog(tmp_path)
    item = catalog.register("build/comparison")
    assert item["kind"] == "comparison_export_v1"
    assert item["status"]["integrity"] == ("unchecked" if mutation == "incomplete" else "invalid")
    assert item["status"]["evidence"] == "unknown"
    assert all(not row["available"] for row in item["files"])
    assert catalog.list()[1] == 1
    for row in item["files"]:
        with pytest.raises(ArtifactError) as denied:
            catalog.read_file(item["id"], row["id"])
        assert denied.value.code == "holdout_protected"


def test_reveal_and_holdout_hash_protection_survive_renames_and_registry_rebuild(tmp_path):
    root = export(tmp_path)
    catalog = Catalog(tmp_path)
    first = catalog.register("build/comparison")
    test_name = read_json(root / "manifest.json")["holdout_files"][0]
    raw = (root / test_name).read_bytes()
    copy = tmp_path / "build/raw-copy"
    copy.mkdir()
    (copy / "ordinary.csv").write_bytes(raw)
    copied = catalog.register("build/raw-copy")
    with pytest.raises(ArtifactError) as denied:
        catalog.read_file(copied["id"], file_id("ordinary.csv"))
    assert denied.value.code == "holdout_protected"
    revealed = catalog.reveal(first["id"], "reveal outside protocol")
    assert revealed["status"]["disclosure"] == "revealed_outside_protocol"
    assert catalog.read_file(copied["id"], file_id("ordinary.csv"))[0] == raw
    assert catalog.read_file(first["id"], file_id("report.json"))[0] == (root / "report.json").read_bytes()
    catalog.database.unlink()  # Rebuild only the disposable test registry, not its journal.
    shutil.copytree(root, tmp_path / "build/renamed")
    rebuilt = Catalog(tmp_path)
    again = rebuilt.register("build/renamed")
    assert again["status"]["disclosure"] == "revealed_outside_protocol"
    assert not again["needs_disclosure"]
    assert rebuilt.read_file(again["id"], file_id(test_name))[0] == raw


@pytest.mark.parametrize("protect_after_registration", [False, True])
def test_known_protected_training_input_guards_all_derivatives(tmp_path, protect_after_registration):
    root = export(tmp_path)
    catalog = Catalog(tmp_path)
    if protect_after_registration:
        item = catalog.register("build/comparison")
        assert catalog.read_file(item["id"], file_id("inputs/window-000.csv"))[0]
    secret = tmp_path / "build/earlier-protected-data"
    secret.mkdir()
    (secret / "data.bin").write_bytes((root / "inputs/window-000.csv").read_bytes())
    catalog.register("build/earlier-protected-data")
    if not protect_after_registration:
        item = catalog.register("build/comparison")
    item = catalog.get(item["id"])
    assert item["status"]["integrity"] == "verified"
    assert item["status"]["selection"] == "unknown"
    assert item["status"]["holdout_evaluation"] == "unknown"
    assert all(not row["available"] for row in item["files"])
    safe_run = read_json(root / "manifest.json")["scenario_files"][0]["files"][0]
    with pytest.raises(ArtifactError) as denied:
        catalog.read_file(item["id"], file_id(safe_run))
    assert denied.value.code == "holdout_protected"
    # Derivative protection is journaled, not only held in a live cache.
    derivative = tmp_path / "build/derivative-copy"
    derivative.mkdir()
    (derivative / "data.csv").write_bytes((root / safe_run).read_bytes())
    copied = Catalog(tmp_path).register("build/derivative-copy")
    assert not copied["files"][0]["available"]


def test_verification_detects_mutation_after_capture(tmp_path, monkeypatch):
    root = export(tmp_path)
    import qte.gui.comparison_artifacts as reader
    actual = reader.verify_comparison_files

    def mutate(files):
        result = actual(files)
        (root / "summary.json").write_text("{}")
        return result

    monkeypatch.setattr(reader, "verify_comparison_files", mutate)
    with pytest.raises(ArtifactError) as changed:
        Catalog(tmp_path).register("build/comparison")
    assert changed.value.code == "artifact_changed"


def test_existing_authenticated_routes_enforce_comparison_disclosure(tmp_path):
    root = export(tmp_path, "sma")
    manifest = read_json(root / "manifest.json")
    holdout = manifest["holdout_files"][0]

    async def scenario():
        codes = []
        app = create_app(tmp_path, code_sink=codes.append)
        async with app.router.lifespan_context(app):
            async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url=BASE) as client:
                assert (await client.get("/api/v1/artifacts")).status_code == 401
                login = await client.post("/api/v1/session", headers={"Origin": BASE},
                                          json={"schema_version": 1, "code": codes[0]})
                headers = {"X-QTE-Token": login.json()["request_token"], "Origin": BASE}
                registered = await client.post("/api/v1/catalog/register", headers=headers,
                    json={"schema_version": 1, "path": "build/comparison"})
                assert registered.status_code == 200, registered.text
                item = registered.json()["artifact"]
                assert item["status"]["integrity"] == "verified"
                assert item["status"]["holdout_evaluation"] == "unknown"
                item_id = item["id"]
                async def download(name):
                    return await client.get(f"/api/v1/artifacts/{item_id}/files/{file_id(name)}/download", headers=headers)
                assert (await download("inputs/window-000.csv")).content == (root / "inputs/window-000.csv").read_bytes()
                for name in (holdout, "report.json", "summary.json", "comparison.csv"):
                    assert (await download(name)).status_code == 403
                reveal = f"/api/v1/artifacts/{item_id}/reveal-holdout"
                assert (await client.post(reveal, headers=headers,
                    json={"schema_version": 1, "confirmation": "yes"})).status_code == 400
                response = await client.post(reveal, headers=headers,
                    json={"schema_version": 1, "confirmation": "reveal outside protocol"})
                assert response.status_code == 200, response.text
                assert response.json()["artifact"]["status"]["disclosure"] == "revealed_outside_protocol"
                assert (await download(holdout)).content == (root / holdout).read_bytes()
    asyncio.run(scenario())
