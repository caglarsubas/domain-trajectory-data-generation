from __future__ import annotations

from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from sqlalchemy import select

from app.db import SessionLocal, init_db
from app.models import Account
from app.routes import router
from app.security import hash_password
from app.settings import load_settings


@asynccontextmanager
async def lifespan(app: FastAPI):
    cfg = load_settings()
    init_db(cfg.database_url)
    if cfg.admin_email and cfg.admin_password:
        db = SessionLocal()
        try:
            existing = db.scalar(select(Account).where(Account.email == cfg.admin_email.strip().lower()))
            if existing is None:
                db.add(
                    Account(
                        email=cfg.admin_email.strip().lower(),
                        password_hash=hash_password(cfg.admin_password),
                        kind="admin",
                    )
                )
                db.commit()
        finally:
            db.close()
    yield


app = FastAPI(title="Trajectory studio", lifespan=lifespan)
app.add_middleware(
    CORSMiddleware,
    allow_origins=["http://localhost:3000", "http://127.0.0.1:3000"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)
app.include_router(router)


@app.get("/health")
def health() -> dict:
    return {"ok": True, "sector": "banking"}
