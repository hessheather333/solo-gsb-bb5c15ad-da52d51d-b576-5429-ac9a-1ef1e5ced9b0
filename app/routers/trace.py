"""溯源链查询、校验报告、JSON 导出接口。"""
from __future__ import annotations

from datetime import date
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy import select
from sqlalchemy.orm import Session

from ..database import get_db
from ..models import Instrument
from ..schemas import TraceResponse
from ..serializers import to_export, to_report, to_trace_response
from ..traceability import trace

router = APIRouter(tags=["溯源"])


def _resolve_instrument(
    db: Session, instrument_id: int | None, serial_no: str | None
) -> Instrument:
    if instrument_id is not None:
        inst = db.get(Instrument, instrument_id)
    elif serial_no:
        inst = db.scalar(select(Instrument).where(Instrument.serial_no == serial_no))
    else:
        raise HTTPException(status_code=422, detail="必须提供 instrument_id 或 serial_no")
    if not inst:
        raise HTTPException(status_code=404, detail="仪器不存在")
    return inst


@router.get("/trace", response_model=TraceResponse, summary="按指定时点重建溯源证书链")
def get_trace(
    instrument_id: int | None = Query(None),
    serial_no: str | None = Query(None, description="仪器出厂编号（与 instrument_id 二选一）"),
    point_in_time: date = Query(..., description="查询时点 YYYY-MM-DD"),
    db: Session = Depends(get_db),
) -> dict[str, Any]:
    inst = _resolve_instrument(db, instrument_id, serial_no)
    result = trace(db, inst, point_in_time)
    return to_trace_response(db, result)


@router.get("/report", summary="溯源校验报告（节点/边逐项检查）")
def get_report(
    instrument_id: int | None = Query(None),
    serial_no: str | None = Query(None),
    point_in_time: date = Query(..., description="查询时点 YYYY-MM-DD"),
    db: Session = Depends(get_db),
) -> dict[str, Any]:
    inst = _resolve_instrument(db, instrument_id, serial_no)
    result = trace(db, inst, point_in_time)
    return to_report(db, result)


@router.get("/export", summary="溯源结果与相关证书 JSON 导出")
def get_export(
    instrument_id: int | None = Query(None),
    serial_no: str | None = Query(None),
    point_in_time: date = Query(..., description="查询时点 YYYY-MM-DD"),
    db: Session = Depends(get_db),
) -> dict[str, Any]:
    inst = _resolve_instrument(db, instrument_id, serial_no)
    result = trace(db, inst, point_in_time)
    return to_export(db, result)
