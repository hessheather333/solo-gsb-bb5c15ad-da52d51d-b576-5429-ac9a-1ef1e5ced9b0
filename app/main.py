"""FastAPI 应用入口。"""
from __future__ import annotations

import os
from contextlib import asynccontextmanager

from fastapi import FastAPI

from .database import Base, engine
from .routers import records, trace


@asynccontextmanager
async def lifespan(app: FastAPI):
    # 确保 SQLite 文件目录存在，然后建表
    db_url = os.getenv("DATABASE_URL", "sqlite:///./data/traceability.db")
    if db_url.startswith("sqlite:///./"):
        os.makedirs(db_url.removeprefix("sqlite:///./").rsplit("/", 1)[0], exist_ok=True)
    Base.metadata.create_all(bind=engine)

    if os.getenv("SEED_DEMO", "false").lower() in {"1", "true", "yes"}:
        from .seed import seed_demo

        seed_demo()
    yield


app = FastAPI(
    title="计量校准溯源 API",
    description=(
        "实验室仪器校准证书溯源服务：按指定时点从仪器向国家基准重建唯一证书链，"
        "校验证书有效区间连续性、SHA-256 签名摘要与测量不确定度，"
        "并在多候选链或区间断裂时返回可解释的冲突节点。"
    ),
    version="1.0.0",
    lifespan=lifespan,
)

app.include_router(records.router)
app.include_router(trace.router)


@app.get("/", tags=["系统"])
def root() -> dict[str, str]:
    return {
        "service": "计量校准溯源 API",
        "docs": "/docs",
        "openapi": "/openapi.json",
        "trace_example": "/trace?serial_no=MFR-2026-001&point_in_time=2026-09-01",
    }


@app.get("/health", tags=["系统"])
def health() -> dict[str, str]:
    return {"status": "ok"}
