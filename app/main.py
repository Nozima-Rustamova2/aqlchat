from fastapi import FastAPI

from app.instagram.router import router as instagram_router
from app.onboarding.router import router as platform_router
from app.telegram.webhook import router as telegram_router

app = FastAPI(title="aqlchat")

app.include_router(telegram_router)
app.include_router(platform_router)
app.include_router(instagram_router)


@app.get("/health")
def health():
    return {"status": "ok"}
