from fastapi import FastAPI

from app.telegram.webhook import router as telegram_router

app = FastAPI(title="aqlchat")

app.include_router(telegram_router)


@app.get("/health")
def health():
    return {"status": "ok"}
