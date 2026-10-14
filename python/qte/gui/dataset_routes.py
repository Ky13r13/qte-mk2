"""Read-only dataset projections; validation starts only through fixed jobs."""
import asyncio

from starlette.concurrency import run_in_threadpool
from starlette.responses import JSONResponse
from starlette.routing import Route

from .artifacts import ArtifactError
from .dataset_views import dataset_view
from .download_views import download_view
from .dto import artifact_value
from .security import SecurityError


def dataset_routes(catalog, authenticate):
    lock = asyncio.Lock()

    async def project(request, view, key):
        authenticate(request, token=True)
        if request.query_params:
            raise SecurityError("invalid_query", 400, "This route does not accept query parameters.")
        async with lock:
            try:
                value = await run_in_threadpool(view, catalog, request.path_params["artifact_id"])
            except ArtifactError as exc:
                raise SecurityError(exc.code, exc.status, exc.message) from None
        return JSONResponse({"schema_version": 1, key: artifact_value(value)},
                            headers={"Cache-Control": "no-store"})

    async def detail(request):
        return await project(request, dataset_view, "dataset")

    async def download_detail(request):
        return await project(request, download_view, "download")

    return [Route("/api/v1/datasets/{artifact_id:str}", detail, methods=["GET"]),
            Route("/api/v1/downloads/{artifact_id:str}", download_detail, methods=["GET"])]
