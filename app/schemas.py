"""Pydantic 请求/响应模型。"""
from __future__ import annotations

from datetime import date
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator

InstrumentKind = Literal["NATIONAL_STANDARD", "REFERENCE_STANDARD", "WORKING"]


# ---------------- 仪器 ----------------
class InstrumentCreate(BaseModel):
    serial_no: str = Field(..., max_length=128, examples=["MFR-2026-001"])
    name: str = Field(..., max_length=255, examples=["数字多用表"])
    manufacturer: str | None = Field(None, max_length=255)
    model: str | None = Field(None, max_length=128)
    kind: InstrumentKind = "WORKING"


class InstrumentOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    serial_no: str
    name: str
    manufacturer: str | None
    model: str | None
    kind: str


# ---------------- 证书 ----------------
class CertificateCreate(BaseModel):
    cert_no: str = Field(..., max_length=128, examples=["CAL-2026-0001"])
    serial_no: str = Field(..., description="被校准仪器出厂编号")

    calibration_date: date
    valid_from: date
    valid_until: date

    measured_value: float | None = None
    uncertainty: float | None = Field(None, ge=0)
    unit: str | None = Field(None, max_length=32)

    # 溯源父证书编号；国家基准证书留空
    parent_cert_no: str | None = None
    # 额外备选父证书（用于并行溯源/多候选链场景）
    alt_parent_cert_nos: list[str] = Field(default_factory=list)

    signature_algorithm: Literal["SHA256"] = "SHA256"
    # 留空时服务端按规范报文自动计算摘要
    signature_digest: str | None = Field(None, max_length=128)
    issued_by: str | None = Field(None, max_length=255)
    note: str | None = None

    @field_validator("valid_from", "valid_until", "calibration_date")
    @classmethod
    def check_dates(cls, v: date) -> date:
        return v


class CertificateOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    cert_no: str
    instrument_id: int
    calibration_date: date
    valid_from: date
    valid_until: date
    measured_value: float | None
    uncertainty: float | None
    unit: str | None
    parent_cert_no: str | None
    alt_parent_cert_nos: list[str]
    signature_algorithm: str
    signature_digest: str
    issued_by: str | None
    note: str | None


# ---------------- 溯源结果（通用结构） ----------------
class TraceNode(BaseModel):
    depth: int
    certificate_id: int
    cert_no: str
    instrument_id: int
    instrument_serial_no: str
    instrument_name: str
    kind: str
    calibration_date: date
    valid_from: date
    valid_until: date
    measured_value: float | None
    uncertainty: float | None
    unit: str | None
    issued_by: str | None
    is_national_anchor: bool
    node_checks: dict[str, Any] = Field(default_factory=dict)


class TraceEdge(BaseModel):
    from_cert_no: str
    to_cert_no: str
    edge_checks: dict[str, Any] = Field(default_factory=dict)


class TraceChain(BaseModel):
    chain_index: int
    node_count: int
    is_complete: bool
    reaches_national_standard: bool
    valid: bool
    nodes: list[TraceNode]
    edges: list[TraceEdge]
    issues: list[dict[str, Any]] = Field(default_factory=list)


class ConflictNode(BaseModel):
    type: str
    cert_no: str
    instrument_serial_no: str
    message: str
    details: dict[str, Any] = Field(default_factory=dict)


class TraceResponse(BaseModel):
    instrument: InstrumentOut
    point_in_time: date
    status: Literal["UNIQUE_CHAIN", "MULTIPLE_CHAINS", "BROKEN_CHAIN", "NO_CERTIFICATE", "CONFLICT"]
    message: str
    valid_chains: list[TraceChain]
    candidate_chains: list[TraceChain]
    broken_branches: list[dict[str, Any]]
    issues: list[dict[str, Any]]
    conflicts: list[ConflictNode]
