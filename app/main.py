from pathlib import Path

from fastapi import FastAPI
from fastapi.staticfiles import StaticFiles

from app.auth.router import router as auth_router
from app.automations.router import router as automations_router
from app.instagram.router import router as instagram_router
from app.onboarding.router import router as platform_router
from app.telegram.webhook import router as telegram_router
from app.web.router import router as web_router

app = FastAPI(title="aqlchat")

app.include_router(telegram_router)
app.include_router(platform_router)
app.include_router(instagram_router)
app.include_router(auth_router)
app.include_router(automations_router)
app.include_router(web_router)

# The website's shared client-side i18n (app/web/static/i18n.js) - one
# file included by every page in app/web/templates/, rather than
# duplicating the uz/ru/en dictionary per page.
app.mount("/static", StaticFiles(directory=Path(__file__).parent / "web" / "static"), name="static")


@app.get("/health")
def health():
    return {"status": "ok"}
