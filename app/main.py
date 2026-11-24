"""JSON-only local API for Naukri Resdex search jobs."""
import hmac
import logging
import os
import threading
import time

from fastapi import APIRouter, Depends, FastAPI, Header, HTTPException, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse, Response
from pydantic import AliasChoices, BaseModel, Field

from . import config

config.require_api_key()

from . import db, scraper  # noqa: E402
from .export import XLSX_MIME_TYPE, build_candidate_workbook  # noqa: E402

logger = logging.getLogger(__name__)
db.init_db()

app = FastAPI(
    title="Naukri Local API",
    docs_url=None,
    redoc_url=None,
    openapi_url=None,
)


def verify_api_key(
    x_api_key: str | None = Header(default=None, alias="X-API-KEY"),
) -> None:
    expected_key = os.getenv("API_KEY", "")
    if (
        not expected_key
        or not x_api_key
        or not hmac.compare_digest(x_api_key, expected_key)
    ):
        raise HTTPException(status_code=401, detail="Unauthorized")


api = APIRouter(prefix="/api", dependencies=[Depends(verify_api_key)])
_SEARCH_LOCK = threading.Lock()


class SearchRequest(BaseModel):
    keyword: str = Field(
        min_length=1,
        max_length=200,
        validation_alias=AliasChoices("keyword", "role", "query"),
    )
    max_pages: int = Field(default=10, ge=1, le=100)
    location: str | None = Field(default=None, max_length=100)


@app.get("/health")
def health() -> dict:
    return {"status": "ok"}


@api.get("/health")
def api_health() -> dict:
    return {"status": "ok"}


@api.post("/launcher/connect")
def connect_launcher() -> dict:
    scraper.launch_launcher()
    time.sleep(5)
    launcher_status = scraper.check_launcher()
    return {"status": launcher_status if launcher_status else "not_running"}


@api.get("/launcher/status")
def launcher_status() -> dict:
    status = scraper.check_launcher()
    return {"status": status if status else "not_running"}


@api.post("/search", status_code=202)
def start_search(body: SearchRequest) -> JSONResponse:
    keyword = body.keyword.strip()
    if not keyword:
        raise HTTPException(status_code=422, detail="keyword is required")
    location = body.location.strip() if body.location and body.location.strip() else None

    with _SEARCH_LOCK:
        if db.get_active_job():
            raise HTTPException(status_code=409, detail="A search is already running")

        job_id = db.create_job(keyword, location, body.max_pages)
        worker = threading.Thread(
            target=scraper.run_search,
            kwargs={
                "job_id": job_id,
                "keyword": keyword,
                "max_pages": body.max_pages,
                "location": location,
            },
            daemon=True,
        )
        try:
            worker.start()
        except RuntimeError as exc:
            logger.error(
                "Could not start search job %s (%s).",
                job_id,
                type(exc).__name__,
            )
            db.fail_job(job_id, "Could not start search worker.")
            raise HTTPException(
                status_code=500,
                detail="Could not start search worker.",
            ) from exc

    return JSONResponse(
        status_code=202,
        content={"job_id": job_id, "status": "pending"},
    )


@api.get("/search/{job_id}")
def get_search(job_id: str) -> dict:
    job = db.get_job(job_id)
    if job is None:
        raise HTTPException(status_code=404, detail="Search job not found.")

    results = job["results"]
    response = {
        "job_id": job["id"],
        "status": job["status"],
        "progress": job["progress"],
        "count": len(results),
        "results": results,
    }
    if job["status"] == "error":
        response["error"] = job["error_message"] or "Search failed."
    return response


@api.get("/search/{job_id}/export.xlsx")
def export_search_xlsx(job_id: str) -> Response:
    job = db.get_job(job_id)
    if job is None:
        raise HTTPException(status_code=404, detail="Search job not found.")

    results = job["results"]
    if not results:
        raise HTTPException(
            status_code=409,
            detail="Search job has no results to export.",
        )

    workbook = build_candidate_workbook(job)
    return Response(
        content=workbook,
        media_type=XLSX_MIME_TYPE,
        headers={
            "Content-Disposition": 'attachment; filename="candidates.xlsx"',
        },
    )


@app.exception_handler(HTTPException)
async def http_exception_json(request: Request, exc: HTTPException) -> JSONResponse:
    message = exc.detail if isinstance(exc.detail, str) else "Request failed"
    logger.debug("Returning HTTP %s for %s", exc.status_code, request.url.path)
    return JSONResponse(
        status_code=exc.status_code,
        content={"error": message},
        headers=exc.headers,
    )


@app.exception_handler(RequestValidationError)
async def validation_exception_json(
    request: Request,
    exc: RequestValidationError,
) -> JSONResponse:
    logger.debug("Invalid request payload for %s", request.url.path)
    details = [
        {
            "field": ".".join(str(part) for part in error["loc"]),
            "message": error["msg"],
        }
        for error in exc.errors()
    ]
    return JSONResponse(
        status_code=422,
        content={"error": "Invalid request payload", "details": details},
    )


@app.exception_handler(Exception)
async def unexpected_exception_json(request: Request, exc: Exception) -> JSONResponse:
    logger.error(
        "Unhandled API exception (%s) for %s",
        type(exc).__name__,
        request.url.path,
    )
    return JSONResponse(
        status_code=500,
        content={"error": "Internal server error"},
    )


app.include_router(api)


if __name__ == "__main__":
    import uvicorn

    uvicorn.run(app, host="127.0.0.1", port=8000)
