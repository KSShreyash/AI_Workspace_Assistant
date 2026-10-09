import logging

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from app.api.v1 import (
    action_items,
    auth,
    calendar,
    contacts,
    dashboard,
    extraction,
    insights,
    me,
    scheduling,
    search,
    sync,
)
from app.core.config import settings

logging.basicConfig(level=logging.INFO)

app = FastAPI(title="AI Assistant API")

app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.cors_allow_origins.split(","),
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

app.include_router(action_items.router)
app.include_router(auth.router)
app.include_router(contacts.router)
app.include_router(dashboard.router)
app.include_router(me.router)
app.include_router(scheduling.router)
app.include_router(search.router)
app.include_router(sync.router)
app.include_router(extraction.router)
app.include_router(insights.router)
app.include_router(calendar.router)


@app.get("/health")
async def health():
    return {"status": "ok"}
