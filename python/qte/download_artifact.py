"""Strict configuration and immutable artifacts for Alpaca historical downloads."""
from __future__ import annotations

import argparse
import hashlib
import json
import math
import os
from pathlib import Path, PurePosixPath
import re
import stat
import sys
from typing import Any, Mapping

from qte.adapters.alpaca import SCHEMA_ID, load_alpaca_fixture
from qte.adapters.alpaca_download import download_stock_bars, stock_bars_request
from qte.adapters.common import interval_for_timeframe, parse_rfc3339_utc_ns
from qte.provenance import source_identity

_MANIFEST = "download_artifact.json"
_BASE = frozenset({_MANIFEST, "configuration.json", "complete.json"})
_MAX_PAGES = 1000
_MAX_FILES = _MAX_PAGES + 5
_MAX_FILE_BYTES = 16 * 1024 * 1024
_MAX_TOTAL_BYTES = 256 * 1024 * 1024
_SYMBOL = re.compile(r"[A-Z][A-Z0-9.\-]{0,14}")
_HEX = re.compile(r"[0-9a-f]{64}")
_ASSUMPTIONS = ["raw", "declared_action_free", "asof_mapping_disabled",
                "historical_zero_publication_delay"]


def _canonical(value: object) -> bytes:
    return json.dumps(value, sort_keys=True, separators=(",", ":"),
                      ensure_ascii=True, allow_nan=False).encode("utf-8")


def _pretty(value: object) -> bytes:
    return (json.dumps(value, indent=2, sort_keys=True, allow_nan=False) + "\n").encode("utf-8")


def compile_download_configuration(wire: object) -> dict[str, Any]:
    """Compile the complete, credential-free download request contract."""
    allowed = {"symbols", "timeframe", "start", "end", "feed", "action_free"}
    if not isinstance(wire, dict) or any(type(key) is not str for key in wire) or set(wire) != allowed:
        raise ValueError("download configuration must contain exactly the supported fields")
    symbols = wire["symbols"]
    if (not isinstance(symbols, list) or not 1 <= len(symbols) <= 100
            or any(type(value) is not str or _SYMBOL.fullmatch(value) is None for value in symbols)
            or len(set(symbols)) != len(symbols)):
        raise ValueError("symbols must be 1 to 100 unique normalized stock symbols")
    timeframe, start, end, feed = (wire[name] for name in ("timeframe", "start", "end", "feed"))
    if any(type(value) is not str for value in (timeframe, start, end, feed)):
        raise ValueError("download text fields must be strings")
    interval_for_timeframe(timeframe)
    start_ns, end_ns = parse_rfc3339_utc_ns(start), parse_rfc3339_utc_ns(end)
    if start_ns >= end_ns:
        raise ValueError("start must precede end")
    if feed not in ("iex", "sip"):
        raise ValueError("feed must explicitly be iex or sip")
    if wire["action_free"] is not True:
        raise ValueError("action_free=True declaration required")
    return {"symbols": list(symbols), "timeframe": timeframe, "start": start, "end": end,
            "feed": feed, "action_free": True}


def _strict_json(raw: bytes) -> Any:
    def pairs(items):
        result = {}
        for key, value in items:
            if key in result:
                raise ValueError("download artifact JSON contains a duplicate key")
            result[key] = value
        return result
    try:
        value = json.loads(raw, object_pairs_hook=pairs,
                          parse_float=lambda text: _finite_float(text),
                          parse_constant=lambda _: (_ for _ in ()).throw(ValueError("nonfinite")))
    except (UnicodeDecodeError, json.JSONDecodeError, RecursionError) as error:
        raise ValueError("download artifact JSON is invalid") from error
    pending = [(value, 0)]
    while pending:
        item, depth = pending.pop()
        if depth > 64:
            raise ValueError("download artifact JSON exceeds the nesting limit")
        if isinstance(item, str):
            try: item.encode("utf-8")
            except UnicodeEncodeError as error: raise ValueError("download artifact JSON has invalid Unicode") from error
        elif isinstance(item, dict):
            pending.extend((part, depth + 1) for pair in item.items() for part in pair)
        elif isinstance(item, list):
            pending.extend((part, depth + 1) for part in item)
    return value


def _finite_float(text: str) -> float:
    value = float(text)
    if not math.isfinite(value):
        raise ValueError("nonfinite")
    return value


def _request(config: dict[str, Any]):
    return stock_bars_request(**config)


def _manifest(config: dict[str, Any], build_identity: str) -> dict[str, Any]:
    request, request_id, _ = _request(config)
    return {"schema_version": 1, "artifact_kind": "alpaca_download_v1",
            "source_identity": build_identity, "request_id": request_id}


def write_download_output(config_path: Path, output: Path, *, page_fetcher=None) -> Path:
    config = compile_download_configuration(_strict_json(Path(config_path).read_bytes()))
    _, request_id, _ = _request(config)
    output = Path(output)
    output.mkdir(parents=True, exist_ok=False)
    cache = output / "cache"
    path = download_stock_bars(**config, cache_dir=cache, page_fetcher=page_fetcher,
                               max_pages=_MAX_PAGES, max_page_bytes=_MAX_FILE_BYTES,
                               max_total_bytes=_MAX_TOTAL_BYTES - 2 * 1024 * 1024)
    if path != cache / request_id / "bars.json":
        raise ValueError("download cache identity mismatch")
    files = {"configuration.json": _pretty(config),
             _MANIFEST: _pretty(_manifest(config, source_identity()))}
    for name, content in files.items():
        with (output / name).open("xb") as stream:
            stream.write(content)
    captured = _capture_output(output, include_complete=False)
    # Full semantic verification occurs before the completion marker publishes the artifact.
    _verify_payload(captured, require_complete=False)
    complete = {"schema_version": 1, "sha256": {
        name: hashlib.sha256(content).hexdigest() for name, content in sorted(captured.items())}}
    with (output / "complete.json").open("xb") as stream:
        stream.write(_pretty(complete))
    return output


def _verify_payload(files: Mapping[str, bytes], *, require_complete: bool) -> dict[str, Any]:
    names = set(files)
    required = _BASE if require_complete else _BASE - {"complete.json"}
    if not required.issubset(names):
        raise ValueError("download artifact is incomplete")
    configuration = _strict_json(files["configuration.json"])
    config = compile_download_configuration(configuration)
    if files["configuration.json"] != _pretty(config):
        raise ValueError("download configuration is not canonical")
    request, request_id, interval = _request(config)
    prefix = f"cache/{request_id}/"
    cache_names = names - required
    if require_complete:
        cache_names.discard("complete.json")
    metadata_name, bars_name = prefix + "download.json", prefix + "bars.json"
    if metadata_name not in cache_names or bars_name not in cache_names:
        raise ValueError("download cache inventory is incomplete")
    page_names = sorted(cache_names - {metadata_name, bars_name})
    if (not 1 <= len(page_names) <= _MAX_PAGES or page_names != [
            prefix + f"page-{index:06d}.json" for index in range(len(page_names))]):
        raise ValueError("download page inventory is invalid")
    metadata = _strict_json(files[metadata_name])
    expected_cache_names = {PurePosixPath(name).name for name in page_names + [bars_name]}
    if (not isinstance(metadata, dict)
            or set(metadata) != {"version", "request", "sha256", "page_count", "fetched_at_ns", "assumptions"}
            or type(metadata["version"]) is not int or metadata["version"] != 1
            or metadata["request"] != request or type(metadata["page_count"]) is not int
            or metadata["page_count"] != len(page_names) or type(metadata["fetched_at_ns"]) is not int
            or metadata["fetched_at_ns"] < 0 or metadata["assumptions"] != _ASSUMPTIONS
            or not isinstance(metadata["sha256"], dict) or set(metadata["sha256"]) != expected_cache_names):
        raise ValueError("download cache metadata is invalid")
    if files[metadata_name] != _canonical(metadata):
        raise ValueError("download cache metadata is not canonical")
    for name in page_names + [bars_name]:
        leaf = PurePosixPath(name).name
        if metadata["sha256"].get(leaf) != hashlib.sha256(files[name]).hexdigest():
            raise ValueError("download cache checksum mismatch")
    combined = {symbol: [] for symbol in sorted(config["symbols"])}
    expected_token = None
    seen = set()
    start_ns, end_ns = parse_rfc3339_utc_ns(config["start"]), parse_rfc3339_utc_ns(config["end"])
    for index, name in enumerate(page_names):
        page = _strict_json(files[name])
        if (not isinstance(page, dict) or "next_page_token" not in page
                or not isinstance(page.get("bars"), dict)):
            raise ValueError("download page is malformed")
        if files[name] != _canonical(page):
            raise ValueError("download page is not canonical")
        if index and expected_token is None:
            raise ValueError("download page lineage is invalid")
        if set(page["bars"]) - set(combined):
            raise ValueError("download page contains an unexpected symbol")
        for symbol, rows in page["bars"].items():
            if not isinstance(rows, list):
                raise ValueError("download page bar list is invalid")
            for row in rows:
                if not isinstance(row, dict):
                    raise ValueError("download page bar is invalid")
                timestamp = parse_rfc3339_utc_ns(row.get("t"))
                if not start_ns <= timestamp <= end_ns:
                    raise ValueError("download page bar is outside the request")
            combined[symbol].extend(rows)
        token = page["next_page_token"]
        if token is not None and (not isinstance(token, str) or not token or token in seen):
            raise ValueError("download page token is invalid")
        if token is not None:
            seen.add(token)
        expected_token = token
    if expected_token is not None:
        raise ValueError("download page set is incomplete")
    fixture = {"_qte": {"schema": SCHEMA_ID, "feed": config["feed"],
                         "timeframe": config["timeframe"], "adjustment": "raw",
                         "action_free": True}, "bars": combined, "next_page_token": None}
    if files[bars_name] != _canonical(fixture):
        raise ValueError("download merged bars do not match page lineage")
    loaded = load_alpaca_fixture_bytes(files[bars_name], interval)
    manifest = _strict_json(files[_MANIFEST])
    if (not isinstance(manifest, dict) or set(manifest) != {
            "schema_version", "artifact_kind", "source_identity", "request_id"}
            or type(manifest["schema_version"]) is not int or manifest["schema_version"] != 1
            or manifest["artifact_kind"] != "alpaca_download_v1" or manifest["request_id"] != request_id
            or not isinstance(manifest["source_identity"], str)
            or re.fullmatch(r"sha256:[0-9a-f]{64}", manifest["source_identity"]) is None
            or files[_MANIFEST] != _pretty(_manifest(config, manifest["source_identity"]))):
        raise ValueError("download manifest is invalid")
    summary = {"request": request, "request_id": request_id, "page_count": len(page_names),
               "bars_sha256": hashlib.sha256(files[bars_name]).hexdigest(),
               "dataset_hash": loaded.dataset.hash, "assumptions": list(_ASSUMPTIONS)}
    if require_complete:
        complete = _strict_json(files["complete.json"])
        expected = names - {"complete.json"}
        if (not isinstance(complete, dict) or set(complete) != {"schema_version", "sha256"}
                or type(complete["schema_version"]) is not int or complete["schema_version"] != 1
                or not isinstance(complete["sha256"], dict) or set(complete["sha256"]) != expected):
            raise ValueError("download completion inventory is invalid")
        for name in expected:
            if complete["sha256"].get(name) != hashlib.sha256(files[name]).hexdigest():
                raise ValueError("download completion checksum mismatch")
    return {"manifest": manifest, "configuration": config, "summary": summary}


def load_alpaca_fixture_bytes(raw: bytes, interval: int):
    # Reuse the public adapter while keeping verification independent of artifact paths.
    from qte.adapters.alpaca import _load_alpaca_bytes
    return _load_alpaca_bytes(raw, interval_ns=interval)


def verify_download_files(files: Mapping[str, bytes]) -> dict[str, Any]:
    if (not isinstance(files, Mapping) or any(type(name) is not str for name in files)
            or any(type(content) is not bytes for content in files.values())
            or len(files) > _MAX_FILES or sum(len(value) for value in files.values()) > _MAX_TOTAL_BYTES):
        raise ValueError("download artifact files are invalid or exceed limits")
    for name, content in files.items():
        path = PurePosixPath(name)
        if (path.is_absolute() or not path.parts or any(part in ("", ".", "..") for part in path.parts)
                or len(content) > _MAX_FILE_BYTES):
            raise ValueError("download artifact inventory is invalid")
    return _verify_payload(files, require_complete=True)


def _capture_output(root: Path, *, include_complete: bool = True) -> dict[str, bytes]:
    root = Path(root)
    if stat.S_ISLNK(root.lstat().st_mode):
        raise ValueError("download artifact contains a symlink")
    captured: dict[str, bytes] = {}
    total = 0
    directory_flags = (os.O_RDONLY | getattr(os, "O_CLOEXEC", 0)
                       | getattr(os, "O_NOFOLLOW", 0) | getattr(os, "O_DIRECTORY", 0))
    file_flags = (os.O_RDONLY | getattr(os, "O_CLOEXEC", 0) | getattr(os, "O_NOFOLLOW", 0)
                  | getattr(os, "O_NONBLOCK", 0))
    root_fd = os.open(root, directory_flags)
    pending = [(root_fd, PurePosixPath())]
    directories_seen = set()
    entries_seen = 0
    try:
        while pending:
            directory_fd, prefix = pending.pop()
            try:
                names = sorted(os.listdir(directory_fd))
                for name in names:
                    entries_seen += 1
                    if entries_seen > _MAX_FILES + 2:
                        raise ValueError("download artifact inventory exceeds limits")
                    info = os.stat(name, dir_fd=directory_fd, follow_symlinks=False)
                    relative_path = prefix / name
                    relative = relative_path.as_posix()
                    if stat.S_ISLNK(info.st_mode):
                        raise ValueError("download artifact contains a symlink")
                    if stat.S_ISDIR(info.st_mode):
                        if relative != "cache" and re.fullmatch(r"cache/[0-9a-f]{64}", relative) is None:
                            raise ValueError("download artifact directory inventory is invalid")
                        child = os.open(name, directory_flags, dir_fd=directory_fd)
                        directories_seen.add(relative)
                        pending.append((child, relative_path))
                        continue
                    if not stat.S_ISREG(info.st_mode) or info.st_size > _MAX_FILE_BYTES:
                        raise ValueError("download artifact contains an invalid file")
                    if not include_complete and relative == "complete.json":
                        continue
                    descriptor = os.open(name, file_flags, dir_fd=directory_fd)
                    try:
                        opened = os.fstat(descriptor)
                        if not stat.S_ISREG(opened.st_mode) or opened.st_size > _MAX_FILE_BYTES:
                            raise ValueError("download artifact contains an invalid file")
                        chunks, remaining = [], _MAX_FILE_BYTES + 1
                        while remaining:
                            chunk = os.read(descriptor, min(65536, remaining))
                            if not chunk: break
                            chunks.append(chunk); remaining -= len(chunk)
                        content = b"".join(chunks)
                        after = os.fstat(descriptor)
                    finally:
                        os.close(descriptor)
                    identity = lambda value: (value.st_dev, value.st_ino, value.st_size,
                                              value.st_mtime_ns, value.st_ctime_ns)
                    if len(content) != opened.st_size or identity(info) != identity(opened) or identity(opened) != identity(after):
                        raise ValueError("download artifact changed during capture")
                    captured[relative] = content
                    total += len(content)
                    if len(captured) > _MAX_FILES or total > _MAX_TOTAL_BYTES:
                        raise ValueError("download artifact exceeds limits")
            finally:
                os.close(directory_fd)
    finally:
        for descriptor, _ in pending:
            try: os.close(descriptor)
            except OSError: pass
    manifest = captured.get(_MANIFEST)
    request_id = None
    if manifest is not None:
        try: request_id = _strict_json(manifest).get("request_id")
        except (ValueError, AttributeError): pass
    allowed_directories = {"cache", f"cache/{request_id}"} if isinstance(request_id, str) else set()
    if directories_seen != allowed_directories:
        raise ValueError("download artifact directory inventory is invalid")
    return captured


def verify_download_output(root: Path) -> dict[str, Any]:
    return verify_download_files(_capture_output(root))


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(prog="python -m qte.download_artifact")
    parser.add_argument("--config", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args(argv)
    try:
        output = write_download_output(args.config, args.output)
    except (ValueError, TypeError, OSError):
        print("qte: Alpaca download failed", file=sys.stderr)
        return 2
    print(f"Output: {output}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
