import asyncio
import logging
from contextlib import asynccontextmanager

from fastapi import FastAPI
from sqlalchemy import text

from app.api import auth, jobs, references
from app.config import get_settings
from app.db import engine
from app.events import bus
from app.logging_setup import configure_logging

settings = get_settings()
configure_logging(settings.log_level, settings.log_json)


@asynccontextmanager
async def lifespan(_: FastAPI):
    bus.start(asyncio.get_running_loop())
    logging.getLogger("api").info("api started")
    yield
    bus.stop()


app = FastAPI(title="Medication Doc Verifier API", lifespan=lifespan)
app.include_router(auth.router)
app.include_router(references.router)
app.include_router(jobs.router)


@app.get("/healthz")
@app.get("/api/healthz")
def healthz():
    with engine.connect() as conn:
        conn.execute(text("SELECT 1"))
    return {"status": "ok"}
