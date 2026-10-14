"""Offline regressions for strict download capture and acquisition limits."""
from hashlib import sha256
import json
import os

import pytest

import qte.download_artifact as artifact
from qte.adapters.alpaca_download import download_stock_bars
from test_adapters import ALPACA
from test_download_artifact import captured, config, create, rehash


def test_capture_rejects_symlinked_root(tmp_path):
    output, _ = create(tmp_path)
    link = tmp_path / "linked-output"
    link.symlink_to(output, target_is_directory=True)
    with pytest.raises(ValueError, match="symlink"):
        artifact.verify_download_output(link)


def test_capture_rejects_regular_file_replaced_by_fifo_before_open(monkeypatch, tmp_path):
    output, _ = create(tmp_path)
    real_open, real_read = os.open, os.read
    replaced, fifo_descriptors = [], set()

    def racing_open(path, flags, *args, **kwargs):
        if path == "configuration.json" and "dir_fd" in kwargs:
            # Fail safely instead of hanging the regression if nonblocking open
            # protection disappears in a later implementation.
            assert flags & os.O_NONBLOCK
            os.unlink(path, dir_fd=kwargs["dir_fd"])
            os.mkfifo(path, dir_fd=kwargs["dir_fd"])
            descriptor = real_open(path, flags, *args, **kwargs)
            fifo_descriptors.add(descriptor)
            replaced.append(True)
            return descriptor
        return real_open(path, flags, *args, **kwargs)

    def checked_read(descriptor, size):
        assert descriptor not in fifo_descriptors, "non-regular descriptor must be rejected before reading"
        return real_read(descriptor, size)

    monkeypatch.setattr(artifact.os, "open", racing_open)
    monkeypatch.setattr(artifact.os, "read", checked_read)
    with pytest.raises(ValueError, match="invalid file"):
        artifact.verify_download_output(output)
    assert replaced == [True]


def test_capture_rejects_unexpected_empty_directories_before_opening_them(monkeypatch, tmp_path):
    output, _ = create(tmp_path)
    (output / "unexpected").mkdir()
    real_open = os.open

    def checked_open(path, flags, *args, **kwargs):
        assert path != "unexpected", "unexpected directories must be rejected before traversal"
        return real_open(path, flags, *args, **kwargs)

    monkeypatch.setattr(artifact.os, "open", checked_open)
    with pytest.raises(ValueError, match="directory inventory"):
        artifact.verify_download_output(output)


def test_resealed_provider_metadata_cannot_contain_exponent_overflow(tmp_path):
    output, _ = create(tmp_path)
    files = captured(output)
    request_id = json.loads(files["download_artifact.json"])["request_id"]
    prefix = f"cache/{request_id}/"
    page_name = prefix + "page-000000.json"
    # Additional finite provider fields are allowed; an overflowed JSON float
    # in one must still fail, even with both checksum layers recomputed.
    files[page_name] = files[page_name][:-1] + b',"provider_extra":1e999}'
    metadata = json.loads(files[prefix + "download.json"])
    metadata["sha256"]["page-000000.json"] = sha256(files[page_name]).hexdigest()
    files[prefix + "download.json"] = json.dumps(metadata, sort_keys=True, separators=(",", ":")).encode()
    rehash(files)
    with pytest.raises(ValueError, match="nonfinite"):
        artifact.verify_download_files(files)


@pytest.mark.parametrize("budget", ["page", "total"])
def test_acquisition_stops_before_writing_over_budget_page(tmp_path, budget):
    rows = json.loads(ALPACA.read_text())["bars"]["SPY"]
    page = {"bars": {"SPY": rows}, "next_page_token": "more"}
    page_size = len(json.dumps(page, sort_keys=True, separators=(",", ":")).encode())
    calls = []

    def fetch(_params):
        calls.append(True)
        return page

    limits = ({"max_page_bytes": page_size - 1} if budget == "page"
              else {"max_total_bytes": page_size - 1})
    cache = tmp_path / "cache"
    with pytest.raises(ValueError, match="limit"):
        download_stock_bars(**config(), cache_dir=cache, page_fetcher=fetch, **limits)
    assert calls == [True]
    assert list(cache.iterdir()) == []


def test_aggregate_budget_includes_merged_bars_before_publication(tmp_path):
    rows = json.loads(ALPACA.read_text())["bars"]["SPY"]
    page = {"bars": {"SPY": rows}, "next_page_token": None}
    page_size = len(json.dumps(page, sort_keys=True, separators=(",", ":")).encode())
    cache = tmp_path / "cache"
    with pytest.raises(ValueError, match="download exceeds artifact byte limit"):
        download_stock_bars(**config(), cache_dir=cache, page_fetcher=lambda _: page,
                            max_total_bytes=page_size)
    assert list(cache.iterdir()) == []
