"""HTTP acceptance for schema-v2 owned-result research exports."""
from __future__ import annotations

import asyncio
import csv
import hashlib
import json
from pathlib import Path

import httpx
import pytest

from qte.cli import run_research
from qte.gui import create_app


ROOT = Path(__file__).parents[2]
BASE = "http://127.0.0.1:8765"
TABLE_FILES = {
    "orders": "order_snapshots.csv",
    "fills": "fill_events.csv",
    "equity": "equity_events.csv",
    "sampled_equity": "sampled_equity_events.csv",
    "positions": "positions.csv",
    "closed_trades": "closed_trades.csv",
    "open_trades": "open_trades.csv",
    "order_events": "order_events.csv",
}


def _rows(path: Path) -> list[dict[str, str]]:
    with path.open(newline="", encoding="utf-8") as stream:
        return list(csv.DictReader(stream))


def _sampled_config(repository: Path) -> Path:
    payload = json.loads((ROOT / "examples/research-run.json").read_text(encoding="utf-8"))
    payload["data"]["path"] = str((ROOT / "examples/research-bars.csv").resolve())
    payload["sampling"] = {
        "interval_ns": 1_800_000_000_000,
        "max_staleness_ns": 3_600_000_000_000,
        "periods_per_year": 17_532,
    }
    path = repository / "sampled-config.json"
    path.write_text(json.dumps(payload), encoding="utf-8")
    return path


def _rehash(output: Path) -> None:
    hashes = {path.name: hashlib.sha256(path.read_bytes()).hexdigest()
              for path in output.iterdir() if path.name != "complete.json"}
    (output / "complete.json").write_text(
        json.dumps({"schema_version": 1, "sha256": hashes}), encoding="utf-8")


def _exercise(repository: Path, scenario) -> None:
    async def run() -> None:
        codes: list[str] = []
        app = create_app(repository, code_sink=codes.append)
        async with app.router.lifespan_context(app):
            async with httpx.AsyncClient(
                transport=httpx.ASGITransport(app=app), base_url=BASE
            ) as client:
                login = await client.post(
                    "/api/v1/session", headers={"Origin": BASE},
                    json={"schema_version": 1, "code": codes[0]},
                )
                assert login.status_code == 200
                headers = {"Origin": BASE, "X-QTE-Token": login.json()["request_token"]}
                await scenario(client, headers)

    asyncio.run(run())


async def _register(client: httpx.AsyncClient, headers: dict[str, str], path: str) -> dict:
    response = await client.post(
        "/api/v1/catalog/register", headers=headers,
        json={"schema_version": 1, "path": path},
    )
    assert response.status_code == 200, response.text
    return response.json()["artifact"]


def test_actual_engine_v2_http_preserves_owned_rows_sequences_and_pagination(tmp_path):
    output = run_research(_sampled_config(tmp_path), tmp_path / "build/v2", export_version=2)

    async def scenario(client, headers):
        artifact = await _register(client, headers, "build/v2")
        base = f"/api/v1/runs/{artifact['id']}"

        detail_response = await client.get(base, headers=headers)
        assert detail_response.status_code == 200, detail_response.text
        detail = detail_response.json()["run"]
        assert detail["kind"] == "research_export_v2"
        assert detail["recordings"] == {"event_equity": "recorded", "sampled_equity": "recorded"}
        assert detail["capabilities"] == json.loads(
            (output / "research_export.json").read_text(encoding="utf-8")
        )["capabilities"]
        assert detail["capabilities"]["sampled_event_sequence"] == "recorded"

        for sampling, filename, status in (
            ("event", "equity_events.csv", "recorded"),
            ("sampled", "sampled_equity_events.csv", "recorded_source_event"),
        ):
            response = await client.get(f"{base}/series?sampling={sampling}", headers=headers)
            assert response.status_code == 200, response.text
            series = response.json()["series"]
            source = _rows(output / filename)
            assert series["sequence_status"] == status
            assert [point["timestamp_ns"] for point in series["points"]] == [row["timestamp_ns"] for row in source]
            assert [point["sequence"] for point in series["points"]] == [row["sequence"] for row in source]
            assert all(isinstance(point["timestamp_ns"], str) and isinstance(point["sequence"], str)
                       for point in series["points"])
            assert int(series["points"][0]["timestamp_ns"]) > 2**53
        sampled = (await client.get(f"{base}/series?sampling=sampled", headers=headers)).json()["series"]
        assert any(left["sequence"] == right["sequence"]
                   for left, right in zip(sampled["points"], sampled["points"][1:]))

        for name, filename in TABLE_FILES.items():
            source = _rows(output / filename)
            response = await client.get(f"{base}/tables/{name}?offset=0&limit=1", headers=headers)
            assert response.status_code == 200, (name, response.text)
            table = response.json()
            assert table["table"] == name
            assert table["rows"] == source[:1]
            assert table["total"] == len(source) and table["offset"] == 0 and table["limit"] == 1
            if len(source) > 1:
                page = await client.get(f"{base}/tables/{name}?offset=1&limit=1", headers=headers)
                assert page.json()["rows"] == source[1:2]

        order = (await client.get(f"{base}/tables/orders?limit=1", headers=headers)).json()["rows"][0]
        assert order["order_id"] == "1" and order["submitted_ns"] == "1704214800000000000"
        assert order["submission_sequence"] == order["eligible_after_sequence"] == "11"
        assert order["limit_price"] == order["stop_price"] == ""
        assert order["rejection_reason"] == order["cancellation_reason"] == ""
        fill = (await client.get(f"{base}/tables/fills?limit=1", headers=headers)).json()["rows"][0]
        assert (fill["fill_id"], fill["order_id"], fill["timestamp_ns"], fill["sequence"]) == (
            "1", "1", "1704214800000000000", "13")

    _exercise(tmp_path, scenario)


def test_v1_richer_tables_are_explicitly_not_recorded_and_reads_require_auth(tmp_path):
    run_research(ROOT / "examples/research-run.json", tmp_path / "build/v1")

    async def scenario(client, headers):
        artifact = await _register(client, headers, "build/v1")
        base = f"/api/v1/runs/{artifact['id']}"
        for route in (base, f"{base}/series", f"{base}/tables/orders", f"{base}/tables/positions"):
            response = await client.get(route)
            assert response.status_code == 403, (route, response.text)
        for name in ("positions", "closed_trades", "open_trades", "order_events"):
            response = await client.get(f"{base}/tables/{name}", headers=headers)
            assert response.status_code == 404
            assert response.json()["error"]["code"] == "not_recorded"

    _exercise(tmp_path, scenario)


def test_corrupt_tagged_v2_is_not_served_as_legacy(tmp_path):
    output = run_research(ROOT / "examples/research-run.json", tmp_path / "build/corrupt", export_version=2)
    wrapper = json.loads((output / "research_export.json").read_text(encoding="utf-8"))
    wrapper["files"].pop("positions")
    (output / "research_export.json").write_text(json.dumps(wrapper), encoding="utf-8")
    _rehash(output)

    async def scenario(client, headers):
        artifact = await _register(client, headers, "build/corrupt")
        assert artifact["kind"] == "research_export_v2"
        assert artifact["status"]["integrity"] != "verified"
        response = await client.get(f"/api/v1/runs/{artifact['id']}", headers=headers)
        assert response.status_code == 409
        assert response.json()["error"]["code"] == "run_view_unavailable"

    _exercise(tmp_path, scenario)


def test_copied_owned_table_remains_protected_through_http_view(tmp_path):
    output = run_research(ROOT / "examples/research-run.json", tmp_path / "build/v2", export_version=2)
    unknown = tmp_path / "build/protected"
    unknown.mkdir()
    (unknown / "attachment.bin").write_bytes((output / "positions.csv").read_bytes())

    async def scenario(client, headers):
        await _register(client, headers, "build/protected")
        artifact = await _register(client, headers, "build/v2")
        response = await client.get(
            f"/api/v1/runs/{artifact['id']}/tables/positions", headers=headers)
        assert response.status_code == 403, response.text
        assert response.json()["error"]["code"] == "holdout_protected"
        assert not list((tmp_path / "build/gui/disclosures").glob("revealed_outside_protocol-*.json"))

    _exercise(tmp_path, scenario)


@pytest.mark.parametrize(
    ("legacy_name", "owned_name", "view_route"),
    [
        ("equity.csv", "equity_events.csv", "/series?sampling=event"),
        ("sampled_equity.csv", "sampled_equity_events.csv", "/series?sampling=sampled"),
        ("fills.csv", "fill_events.csv", "/tables/fills"),
        ("orders.csv", "order_snapshots.csv", "/tables/orders"),
    ],
)
@pytest.mark.parametrize("protected_side", ["legacy", "owned"])
@pytest.mark.parametrize("protected_first", [False, True])
def test_v2_projection_equivalents_share_protection_in_both_directions(
    tmp_path, legacy_name, owned_name, view_route, protected_side, protected_first
):
    output = run_research(
        ROOT / "examples/research-run.json", tmp_path / "build/v2", export_version=2)
    protected_name = legacy_name if protected_side == "legacy" else owned_name
    raw_target = owned_name if protected_side == "legacy" else legacy_name
    unknown = tmp_path / "build/protected"
    unknown.mkdir()
    (unknown / "attachment.bin").write_bytes((output / protected_name).read_bytes())

    async def scenario(client, headers):
        if protected_first:
            await _register(client, headers, "build/protected")
        artifact = await _register(client, headers, "build/v2")
        if not protected_first:
            await _register(client, headers, "build/protected")

        detail_response = await client.get(
            f"/api/v1/artifacts/{artifact['id']}/files?limit=200", headers=headers)
        assert detail_response.status_code == 200, detail_response.text
        files = {row["name"]: row for row in detail_response.json()["items"]}
        assert files[legacy_name]["available"] is False
        assert files[owned_name]["available"] is False

        download = await client.get(
            f"/api/v1/artifacts/{artifact['id']}/files/{files[raw_target]['id']}/download",
            headers=headers,
        )
        assert download.status_code == 403, download.text
        assert download.json()["error"]["code"] == "holdout_protected"
        view = await client.get(f"/api/v1/runs/{artifact['id']}{view_route}", headers=headers)
        assert view.status_code == 403, view.text
        assert view.json()["error"]["code"] == "holdout_protected"

        disclosure_root = tmp_path / "build/gui/disclosures"
        protected_records = list(disclosure_root.glob("protected_content-*.json"))
        assert protected_records
        reveal = await client.post(
            f"/api/v1/artifacts/{artifact['id']}/reveal-holdout", headers=headers,
            json={"schema_version": 1, "confirmation": "reveal outside protocol"},
        )
        assert reveal.status_code == 200, reveal.text
        for name in (legacy_name, owned_name):
            identity = "file-sha256:" + hashlib.sha256((output / name).read_bytes()).hexdigest()
            key = hashlib.sha256(identity.encode("utf-8")).hexdigest()
            record = disclosure_root / f"revealed_outside_protocol-{key}.json"
            assert json.loads(record.read_text(encoding="utf-8"))["identity"] == identity
        assert (await client.get(
            f"/api/v1/artifacts/{artifact['id']}/files/{files[raw_target]['id']}/download",
            headers=headers,
        )).status_code == 200

    _exercise(tmp_path, scenario)


def test_projection_protection_propagates_to_later_registered_legacy_copy(tmp_path):
    owned = run_research(
        ROOT / "examples/research-run.json", tmp_path / "build/v2", export_version=2)
    unknown = tmp_path / "build/protected"
    unknown.mkdir()
    (unknown / "attachment.bin").write_bytes((owned / "equity_events.csv").read_bytes())
    legacy = run_research(ROOT / "examples/research-run.json", tmp_path / "build/v1")
    assert (legacy / "equity.csv").read_bytes() != (owned / "equity_events.csv").read_bytes()

    async def scenario(client, headers):
        await _register(client, headers, "build/protected")
        await _register(client, headers, "build/v2")
        artifact = await _register(client, headers, "build/v1")
        response = await client.get(
            f"/api/v1/runs/{artifact['id']}/series?sampling=event", headers=headers)
        assert response.status_code == 403, response.text
        assert response.json()["error"]["code"] == "holdout_protected"

    _exercise(tmp_path, scenario)
