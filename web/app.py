from __future__ import annotations

from contextlib import asynccontextmanager
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from typing import Any
from urllib.parse import urlencode
from zoneinfo import ZoneInfo

from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import HTMLResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates

from settings_store import CAT_IDS, SettingsConflictError, SettingsStore
from web.config import WebConfig
from web.data import InfluxRunRepository
from web.services import dashboard_payload, dashboard_payload_for_window, serialize_run


BASE_DIR = Path(__file__).resolve().parent
RUNS_PER_PAGE = 15
templates = Jinja2Templates(directory=str(BASE_DIR / "templates"))


def _parse_date(value: str | None, default: date) -> date:
    if not value:
        return default
    try:
        return date.fromisoformat(value)
    except ValueError as exc:
        raise HTTPException(status_code=422, detail="Dates must be YYYY-MM-DD") from exc


def _range_key(start_date: date, end_date: date) -> str:
    """Return the matching quick-range key, or ``custom`` for other spans."""
    inclusive_days = (end_date - start_date).days + 1
    return {1: "1d", 7: "7d", 30: "mo"}.get(inclusive_days, "custom")


def _trend_bucket_for_dates(start_date: date, end_date: date) -> str:
    inclusive_days = (end_date - start_date).days + 1
    if inclusive_days <= 2:
        return "hour"
    if inclusive_days <= 90:
        return "day"
    return "week"


def _paginate_recent_runs(request: Request, dashboard: dict) -> dict:
    """Add page-local recent-run data while leaving aggregate data untouched."""
    try:
        requested_page = int(request.query_params.get("page", "1"))
    except ValueError as exc:
        raise HTTPException(status_code=422, detail="page must be an integer") from exc
    if requested_page < 1:
        raise HTTPException(status_code=422, detail="page must be at least 1")

    total = len(dashboard["runs"])
    pages = max(1, (total + RUNS_PER_PAGE - 1) // RUNS_PER_PAGE)
    page = min(requested_page, pages)
    first = (page - 1) * RUNS_PER_PAGE
    parameters = [(key, value) for key, value in request.query_params.multi_items() if key != "page"]

    def page_url(target_page: int) -> str:
        return f"/?{urlencode([*parameters, ('page', target_page)])}"

    dashboard["display_runs"] = dashboard["runs"][first : first + RUNS_PER_PAGE]
    dashboard["pagination"] = {
        "page": page,
        "pages": pages,
        "total": total,
        "first": first + 1 if total else 0,
        "last": min(first + RUNS_PER_PAGE, total),
        "previous_url": page_url(page - 1) if page > 1 else None,
        "next_url": page_url(page + 1) if page < pages else None,
    }
    return dashboard


def create_app(config: WebConfig | None = None, repository=None, store=None) -> FastAPI:
    config = config or WebConfig.from_environment()
    repository = repository or InfluxRunRepository(
        config.influx_url, config.influx_token, config.influx_org, config.influx_bucket
    )
    store = store or SettingsStore(config.state_db)

    @asynccontextmanager
    async def lifespan(_app: FastAPI):
        try:
            yield
        finally:
            close = getattr(repository, "close", None)
            if close:
                close()

    app = FastAPI(title="Catwheel Dashboard", docs_url=None, redoc_url=None, lifespan=lifespan)
    app.mount("/static", StaticFiles(directory=str(BASE_DIR / "static")), name="static")
    app.state.config = config
    app.state.repository = repository
    app.state.store = store

    def page_context(request: Request, **context: Any) -> dict[str, Any]:
        return {"request": request, **context}

    def requested_dashboard(request: Request) -> dict:
        requested_range = request.query_params.get("range") or None
        cat_id = request.query_params.get("cat") or None
        if cat_id and cat_id not in CAT_IDS:
            raise HTTPException(status_code=422, detail="Unknown cat filter")
        if requested_range == "12h":
            end_at = datetime.now(ZoneInfo(config.timezone))
            dashboard = dashboard_payload_for_window(
                repository, store, end_at - timedelta(hours=12), end_at, cat_id, config.timezone, trend_bucket="half_hour"
            )
            dashboard["range_key"] = "12h"
            return dashboard
        if requested_range:
            raise HTTPException(status_code=422, detail="Unknown quick range")
        today = datetime.now().date()
        end_date = _parse_date(request.query_params.get("end"), today)
        start_date = _parse_date(request.query_params.get("start"), end_date - timedelta(days=29))
        if start_date > end_date:
            raise HTTPException(status_code=422, detail="Start date must not be after end date")
        dashboard = dashboard_payload(
            repository,
            store,
            start_date,
            end_date,
            cat_id,
            config.timezone,
            trend_bucket=_trend_bucket_for_dates(start_date, end_date),
        )
        dashboard["range_key"] = _range_key(start_date, end_date)
        return dashboard

    def find_run(run_id: str) -> dict:
        start = datetime(2020, 1, 1, tzinfo=timezone.utc)
        stop = datetime.now(timezone.utc) + timedelta(days=1)
        for run in repository.completed_runs(start, stop):
            if run["run_id"] == run_id:
                return run
        raise HTTPException(status_code=404, detail="Run not found")

    @app.get("/", response_class=HTMLResponse)
    async def dashboard_page(request: Request) -> HTMLResponse:
        dashboard = _paginate_recent_runs(request, requested_dashboard(request))
        return templates.TemplateResponse(request, "dashboard.html", page_context(request, dashboard=dashboard, cat_ids=CAT_IDS))

    @app.get("/runs/{run_id}", response_class=HTMLResponse)
    async def run_page(run_id: str, request: Request) -> HTMLResponse:
        run = find_run(run_id)
        run_view = serialize_run(run, store.get_label(run_id), config.timezone)
        timestamp = run["timestamp"]
        start = timestamp - timedelta(seconds=run["run_duration_seconds"] + 60)
        stop = timestamp + timedelta(minutes=1)
        samples = repository.speed_samples(run_id, start, stop)
        sample_view = [
            {"timestamp": sample["timestamp"].astimezone(timezone.utc).isoformat(), "speed_mph": sample["speed_mph"]}
            for sample in samples
        ]
        return templates.TemplateResponse(
            request, "run_detail.html", page_context(request, run=run_view, samples=sample_view, cat_ids=CAT_IDS)
        )

    @app.get("/admin/settings", response_class=HTMLResponse)
    async def settings_page(request: Request) -> HTMLResponse:
        return templates.TemplateResponse(request, "settings.html", page_context(request, settings=store.get_settings().to_dict()))

    @app.get("/api/v1/dashboard")
    async def get_dashboard(request: Request) -> JSONResponse:
        return JSONResponse(requested_dashboard(request))

    @app.get("/api/v1/runs")
    async def get_runs(request: Request) -> JSONResponse:
        payload = requested_dashboard(request)
        try:
            offset = max(0, int(request.query_params.get("offset", "0")))
            limit = min(100, max(1, int(request.query_params.get("limit", "50"))))
        except ValueError as exc:
            raise HTTPException(status_code=422, detail="offset and limit must be integers") from exc
        return JSONResponse({"total": len(payload["runs"]), "runs": payload["runs"][offset : offset + limit]})

    @app.get("/api/v1/runs/{run_id}")
    async def get_run(run_id: str, request: Request) -> JSONResponse:
        return JSONResponse(serialize_run(find_run(run_id), store.get_label(run_id), config.timezone))

    @app.get("/api/v1/runs/{run_id}/speed-samples")
    async def get_speed_samples(run_id: str, request: Request) -> JSONResponse:
        run = find_run(run_id)
        samples = repository.speed_samples(
            run_id,
            run["timestamp"] - timedelta(seconds=run["run_duration_seconds"] + 60),
            run["timestamp"] + timedelta(minutes=1),
        )
        return JSONResponse(
            {
                "run_id": run_id,
                "samples": [
                    {"timestamp": sample["timestamp"].astimezone(timezone.utc).isoformat(), "speed_mph": sample["speed_mph"]}
                    for sample in samples
                ],
            }
        )

    @app.post("/api/v1/runs/{run_id}/label")
    async def set_run_label(run_id: str, request: Request) -> JSONResponse:
        find_run(run_id)
        body = await request.json()
        cat_id = body.get("cat_id") if isinstance(body, dict) else None
        try:
            store.set_label(run_id, cat_id)
        except ValueError as exc:
            raise HTTPException(status_code=422, detail=str(exc)) from exc
        return JSONResponse({"run_id": run_id, "cat_id": cat_id})

    @app.get("/api/v1/admin/detection-settings")
    async def get_settings(request: Request) -> JSONResponse:
        return JSONResponse(store.get_settings().to_dict())

    @app.patch("/api/v1/admin/detection-settings")
    async def update_settings(request: Request) -> JSONResponse:
        body = await request.json()
        if not isinstance(body, dict) or "expected_revision" not in body:
            raise HTTPException(status_code=422, detail="expected_revision is required")
        try:
            expected_revision = int(body.pop("expected_revision"))
            settings = store.update_settings(body, expected_revision)
        except SettingsConflictError as exc:
            raise HTTPException(status_code=409, detail=str(exc)) from exc
        except (TypeError, ValueError) as exc:
            raise HTTPException(status_code=422, detail=str(exc)) from exc
        return JSONResponse(settings.to_dict())

    return app
