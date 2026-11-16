"""Legacy test page, and the dashboard catch-all (must be registered last)."""

from __future__ import annotations

from pathlib import Path

from fastapi import APIRouter, FastAPI, Request
from fastapi.responses import FileResponse, HTMLResponse, JSONResponse, RedirectResponse, Response
from fastapi.staticfiles import StaticFiles

from api.deps import SettingsDep

DASHBOARD_NOT_BUILT_HTML = (
    "<!doctype html><meta charset=utf-8><title>QueueVision</title>"
    "<p>Dashboard not built. Run: cd dashboard &amp;&amp; npm ci &amp;&amp; npm run build "
    '&mdash; then reload. The old test page is at <a href="/legacy">/legacy</a>.</p>'
)
NO_CACHE = {"Cache-Control": "no-cache"}
router = APIRouter()


def mount_legacy(app: FastAPI) -> None:
    """Mount the legacy page; routers cannot carry mounts, so this runs just before ``router``."""
    static_dir = Path(__file__).parent.parent / "static"
    if static_dir.is_dir():
        app.mount("/legacy", StaticFiles(directory=static_dir, html=True), name="legacy")


def _safe_file(root: Path, relative: str) -> Path | None:
    """The regular file at ``root/relative``, or None if missing or resolving outside ``root``."""
    try:
        resolved = (root / relative.lstrip("/")).resolve()
        if resolved.is_relative_to(root) and resolved.is_file():
            return resolved
    except (OSError, ValueError):
        pass
    return None


def _media_type(path: Path) -> str | None:
    return "text/javascript" if path.suffix == ".js" else None


@router.get("/legacy", include_in_schema=False)
@router.head("/legacy", include_in_schema=False)
def legacy_redirect(request: Request) -> RedirectResponse:
    query = request.url.query
    return RedirectResponse("/legacy/" + (f"?{query}" if query else ""), status_code=307)


@router.api_route("/{path:path}", methods=["GET", "HEAD"], include_in_schema=False)
def dashboard(path: str, settings: SettingsDep) -> Response:
    dashboard_dir = settings.dashboard_dir
    if path in ("api", "ws") or path.startswith(("api/", "ws/")):
        return JSONResponse({"detail": "Not Found"}, status_code=404)
    if not dashboard_dir.is_dir():
        return HTMLResponse(DASHBOARD_NOT_BUILT_HTML, headers=NO_CACHE)
    root = dashboard_dir.resolve()
    candidates = (path, f"{path}.html", f"{path}/index.html") if path else ("index.html",)
    for candidate in candidates:
        found = _safe_file(root, candidate)
        if found is not None:
            headers = None if found.relative_to(root).parts[:2] == ("_next", "static") else NO_CACHE
            return FileResponse(found, media_type=_media_type(found), headers=headers)
    not_found = _safe_file(root, "404.html")
    if not_found is not None:
        return FileResponse(not_found, status_code=404, media_type="text/html", headers=NO_CACHE)
    return JSONResponse({"detail": "Not Found"}, status_code=404)
