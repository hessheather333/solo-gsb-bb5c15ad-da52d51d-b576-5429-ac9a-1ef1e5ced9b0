from datetime import datetime
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException
from sqlite3 import Connection

from ..database import get_db
from ..engine import build_report
from ..schemas import TraceReport

router = APIRouter(prefix="/api", tags=["trace"])


def _report(db: Connection, instrument: str, at: Optional[datetime]) -> dict:
    try:
        return build_report(db, instrument, at)
    except KeyError:
        raise HTTPException(404, f"仪器不存在: {instrument}")


@router.get("/trace/{instrument}", response_model=TraceReport)
def trace_chain(
    instrument: str,
    at: Optional[datetime] = None,
    db: Connection = Depends(get_db),
):
    """链路查询：按指定时点重建该仪器到国家基准的证书链。"""
    return _report(db, instrument, at)


@router.get("/reports/{instrument}", response_model=TraceReport)
def validation_report(
    instrument: str,
    at: Optional[datetime] = None,
    db: Connection = Depends(get_db),
):
    """校验报告：重建结果 + 区间/签名/不确定度的可解释冲突明细。"""
    return _report(db, instrument, at)
