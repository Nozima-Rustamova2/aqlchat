"""Serves the website's static-ish pages (currently just /signup) - plain
HTML/CSS/JS, no templating engine, matching the design_handoff_dukan_ai_landing
approach. The page itself talks to app/auth/ via fetch()."""

from pathlib import Path

from fastapi import APIRouter
from fastapi.responses import HTMLResponse

router = APIRouter(tags=["web"])

_TEMPLATES_DIR = Path(__file__).parent / "templates"


@router.get("/signup", response_class=HTMLResponse)
def signup_page() -> str:
    return (_TEMPLATES_DIR / "signup.html").read_text(encoding="utf-8")


@router.get("/dashboard", response_class=HTMLResponse)
def dashboard_page() -> str:
    # No server-side auth gate here - the page itself calls /auth/me on
    # load and redirects to /signup if that 401s, same client-driven
    # pattern as the rest of this static-page approach.
    return (_TEMPLATES_DIR / "dashboard.html").read_text(encoding="utf-8")


@router.get("/dashboard/settings", response_class=HTMLResponse)
def dashboard_settings_page() -> str:
    return (_TEMPLATES_DIR / "dashboard_settings.html").read_text(encoding="utf-8")


@router.get("/dashboard/automations", response_class=HTMLResponse)
def dashboard_automations_page() -> str:
    return (_TEMPLATES_DIR / "dashboard_automations.html").read_text(encoding="utf-8")


@router.get("/dashboard/agents", response_class=HTMLResponse)
def dashboard_agents_page() -> str:
    return (_TEMPLATES_DIR / "dashboard_agents.html").read_text(encoding="utf-8")


@router.get("/dashboard/agents/{agent_id}", response_class=HTMLResponse)
def dashboard_agent_workspace_page(agent_id: str) -> str:
    # agent_id is unused server-side - the page re-derives it client-side
    # from window.location.pathname for its own /agents/{id} API calls,
    # same client-driven pattern as dashboard_page()'s auth gate above.
    return (_TEMPLATES_DIR / "dashboard_agent_workspace.html").read_text(encoding="utf-8")
