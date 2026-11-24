"""Local Schwab token status, refresh and weekly collection for the loopback GUI.

Credential handling (same contract as ``python -m qte.brokers.schwab_auth refresh``):
- The app key and secret arrive in one authenticated POST to this local process, are used
  for a single OAuth refresh-token exchange, and are then dropped. They are never written,
  logged, echoed or included in errors.
- The key must match the stored app fingerprint, so a typo cannot overwrite the token store.
- Responses contain only expiry metadata, never token material.
"""
from __future__ import annotations

from pathlib import Path
import secrets
import threading
import time

from starlette.concurrency import run_in_threadpool
from starlette.responses import JSONResponse
from starlette.routing import Route

from qte.brokers.schwab_auth import AuthError, TokenStore, app_fingerprint, exchange

from .requests import json_request
from .security import SecurityError

NO_STORE = {"Cache-Control": "no-store"}


def _status(repo, clock):
    try:
        doc = TokenStore(repo).load()
    except FileNotFoundError:
        return dict(present=False, reason="No Schwab authorization saved. Run login once in a terminal.")
    except AuthError as exc:
        return dict(present=False, reason=str(exc))
    left = doc["expires_at"] - clock()
    return dict(present=True, expires_at=doc["expires_at"], seconds_left=max(0, int(left)), expired=left <= 0)


class CollectJob:
    """Single-flight background run of `qte truth collect` + `derive` (no credentials involved)."""

    def __init__(self, repo, runner=None):
        self.repo, self.lock, self.state = Path(repo), threading.Lock(), dict(state="idle")
        self.runner = runner or self._run_collect

    def _run_collect(self):
        from contextlib import closing
        from qte import truth_db
        stamp = time.strftime("%Y%m%dT%H%M%SZ", time.gmtime())
        with closing(truth_db.connect(self.repo / truth_db.DEFAULT_DB)) as db:
            results = truth_db.collect(db, self.repo, self.repo / truth_db.DEFAULT_CONTRACT,
                                       self.repo / truth_db.CAPTURES / "schwab" / stamp)
            derived = truth_db.derive_all(db)
        ok = sum(1 for r in results if r and r.get("status") in ("ingested", "already_ingested"))
        return dict(requests=len(results), ok=ok, errors=len(results) - ok, derived_series=len(derived),
                    capture_dir=f"data/truth/captures/schwab/{stamp}")

    def start(self):
        with self.lock:
            if self.state["state"] == "running":
                return False
            self.state = dict(state="running", started=time.time())
        threading.Thread(target=self._work, daemon=True).start()
        return True

    def _work(self):
        try:
            result = self.runner()
            state = dict(state="done", finished=time.time(), **result)
        except Exception as exc:  # report the type only; messages may embed provider details
            state = dict(state="failed", finished=time.time(), error=type(exc).__name__)
        with self.lock:
            self.state = state

    def snapshot(self):
        with self.lock:
            return dict(self.state)


def schwab_token_routes(repository, authenticate, *, transport=None, clock=time.time, collect_job=None):
    repo = Path(repository)
    job = collect_job or CollectJob(repo)

    def _no_query(request):
        if request.query_params:
            raise SecurityError("invalid_query", 400, "This route does not accept query parameters.")

    async def status(request):
        authenticate(request, token=True)
        _no_query(request)
        body = dict(schema_version=1, token=await run_in_threadpool(_status, repo, clock), collect=job.snapshot())
        return JSONResponse(body, headers=NO_STORE)

    async def refresh(request):
        authenticate(request, token=True)
        _no_query(request)
        payload = await json_request(request, {"app_key", "app_secret"}, limit=8192)
        key, secret = payload.pop("app_key"), payload.pop("app_secret")
        if type(key) is not str or type(secret) is not str or not key.strip() or not secret.strip():
            raise SecurityError("invalid_request", 400, "Enter both the app key and the app secret.")

        def work(app_key, app_secret):
            store = TokenStore(repo)
            with store.lock():
                try:
                    doc = store.load()
                except FileNotFoundError:
                    raise AuthError("No Schwab authorization saved. Run login once in a terminal.") from None
                if not secrets.compare_digest(doc["app_sha256"], app_fingerprint(app_key)):
                    raise AuthError("App key differs from the stored authorization.")
                updated = exchange(app_key, app_secret, refresh_token=doc["token"]["refresh_token"],
                                   transport=transport, clock=clock)
                store.save(updated)
                return updated["expires_at"]

        try:
            expires_at = await run_in_threadpool(work, key.strip(), secret.strip())
        except AuthError as exc:  # AuthError messages are fixed, credential-free strings
            raise SecurityError("schwab_refresh_failed", 409, str(exc)) from None
        except (ValueError, OSError):
            raise SecurityError("schwab_refresh_failed", 409, "Refresh failed; check the app key and secret.") from None
        finally:
            key = secret = None  # drop references promptly
        return JSONResponse(dict(schema_version=1, refreshed=True, expires_at=expires_at,
                                 seconds_left=max(0, int(expires_at - clock()))), headers=NO_STORE)

    async def collect(request):
        authenticate(request, token=True)
        _no_query(request)
        await json_request(request, set(), limit=64)
        token = await run_in_threadpool(_status, repo, clock)
        if not token.get("present") or token.get("expired"):
            raise SecurityError("schwab_token_expired", 409, "Refresh the Schwab token first.")
        started = job.start()
        return JSONResponse(dict(schema_version=1, started=started, collect=job.snapshot()), headers=NO_STORE)

    prefix = "/api/v1/schwab"
    return [
        Route(prefix + "/token", status, methods=["GET"]),
        Route(prefix + "/token/refresh", refresh, methods=["POST"]),
        Route(prefix + "/collect", collect, methods=["POST"]),
    ]
