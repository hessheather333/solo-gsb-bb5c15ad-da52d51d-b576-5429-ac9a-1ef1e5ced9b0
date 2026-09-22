"""请求/响应 Pydantic 模型。时间均按 UTC 解释。"""
from __future__ import annotations

from datetime import datetime
from typing import List, Optional

from pydantic import BaseModel, Field, field_validator

from .utils import SUPPORTED_ALGS, compute_digest, fmt


# ---------- 输入 ----------

class InstrumentIn(BaseModel):
    code: str = Field(..., min_length=1, description="仪器唯一编码")
    name: str
    manufacturer: Optional[str] = None
    model: Optional[str] = None
    serial_no: Optional[str] = None
    is_national_standard: bool = Field(
        False, description="是否为国家基准（溯源根节点）"
    )


class CertificateIn(BaseModel):
    id: str = Field(..., min_length=1)
    instrument_code: str
    cert_number: str
    issued_at: datetime
    valid_from: datetime
    valid_until: datetime
    uncertainty: float = Field(..., ge=0)
    unit: str = ""
    digest_alg: str = "sha256"
    signature_digest: Optional[str] = Field(
        None, description="证书签名摘要；留空则由服务端按规范化载荷重算"
    )
    signed_payload: Optional[str] = Field(
        None, description="被签名的原始载荷；留空则按证书字段规范化生成"
    )

    @field_validator("digest_alg")
    @classmethod
    def _check_alg(cls, v: str) -> str:
        v = v.lower()
        if v not in SUPPORTED_ALGS:
            raise ValueError(f"不支持的摘要算法: {v}")
        return v

    def normalized(self) -> dict:
        data = self.model_dump()
        data["issued_at"] = fmt(self.issued_at)
        data["valid_from"] = fmt(self.valid_from)
        data["valid_until"] = fmt(self.valid_until)
        data["digest_alg"] = self.digest_alg.lower()
        if not data["signed_payload"]:
            data["signed_payload"] = None
        if not data["signature_digest"]:
            data["signature_digest"] = None
        return data


class TraceEdgeIn(BaseModel):
    child_cert_id: str
    parent_cert_id: str
    valid_from: Optional[datetime] = Field(
        None, description="边有效起点；留空取子证书 valid_from"
    )
    valid_until: Optional[datetime] = Field(
        None, description="边有效终点；留空取子证书 valid_until"
    )
    note: str = ""


class SubmissionIn(BaseModel):
    """实验室一次提交：仪器 + 证书 + 父子溯源边（全部按编码 upsert）。"""
    instruments: List[InstrumentIn] = Field(default_factory=list)
    certificates: List[CertificateIn] = Field(default_factory=list)
    edges: List[TraceEdgeIn] = Field(default_factory=list)


# ---------- 输出 ----------

class InstrumentOut(InstrumentIn):
    created_at: datetime


class CertificateOut(BaseModel):
    id: str
    instrument_code: str
    cert_number: str
    issued_at: datetime
    valid_from: datetime
    valid_until: datetime
    uncertainty: float
    unit: str
    digest_alg: str
    signature_digest: str
    signed_payload: str
    created_at: datetime


class TraceEdgeOut(BaseModel):
    id: int
    child_cert_id: str
    parent_cert_id: str
    valid_from: datetime
    valid_until: datetime
    note: str


class SubmissionResult(BaseModel):
    instruments_upserted: List[str]
    certificates_upserted: List[str]
    edges_upserted: List[int]


class Conflict(BaseModel):
    code: str
    severity: str = Field(..., description="ERROR | WARN")
    node: Optional[str] = Field(None, description="冲突所在证书 id（若适用）")
    instrument: Optional[str] = Field(None, description="冲突所在仪器编码（若适用）")
    path_index: Optional[int] = Field(
        None, description="冲突所属候选链下标（节点级检查为 None）"
    )
    message: str
    detail: dict = Field(default_factory=dict)


class ChainNode(BaseModel):
    certificate_id: str
    cert_number: str
    instrument_code: str
    instrument_name: str
    is_national_standard: bool
    valid_from: datetime
    valid_until: datetime
    issued_at: datetime
    uncertainty: float
    unit: str


class ChainEdge(BaseModel):
    edge_id: int
    child_cert_id: str
    parent_cert_id: str
    valid_from: datetime
    valid_until: datetime
    note: str


class Chain(BaseModel):
    nodes: List[ChainNode]
    edges: List[ChainEdge]
    reaches_national_standard: bool
    cycle: bool
    errors: int = 0
    warnings: int = 0


class TraceReport(BaseModel):
    instrument: str
    at: datetime
    resolution: str = Field(
        ..., description="unique | ambiguous | broken（唯一/多候选/断裂）"
    )
    valid: bool = Field(..., description="唯一成链且无 ERROR 级问题时为 true")
    starts: List[str]
    active_chain: Optional[Chain] = None
    candidates: List[Chain] = Field(
        default_factory=list, description="到达国家基准的候选链（ambiguous 时 >=2）"
    )
    broken_chains: List[Chain] = Field(
        default_factory=list, description="未能到达国家基准的部分链"
    )
    conflicts: List[Conflict] = Field(default_factory=list)
    error_count: int = 0
    warning_count: int = 0
    statistics: dict = Field(default_factory=dict)
