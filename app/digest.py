"""证书规范报文与签名摘要计算。

服务端与实验室在提交前都应按同一规范报文计算 SHA-256：

    SHA256( canonical_payload(certificate) )

canonical payload 使用 UTF-8、字段固定顺序的 JSON
（无空白、保证 ASCII、日期 ISO 格式），避免键序/浮点格式歧义。
"""
from __future__ import annotations

import hashlib
import json
from typing import Iterable

from sqlalchemy.orm import Session

from .models import Certificate


def _num(v: float | None) -> float | None:
    # 统一以 float 表示，None 保留
    return None if v is None else float(v)


def canonical_payload(
    *,
    cert_no: str,
    instrument_serial_no: str,
    calibration_date: str,
    valid_from: str,
    valid_until: str,
    measured_value: float | None,
    uncertainty: float | None,
    unit: str | None,
    parent_cert_no: str | None,
    alt_parent_cert_nos: Iterable[str],
    issued_by: str | None,
) -> str:
    """构造参与签名的规范 JSON 字符串。备选父证书编号排序后入报，保证确定性。"""
    payload = {
        "cert_no": cert_no,
        "instrument_serial_no": instrument_serial_no,
        "calibration_date": calibration_date,
        "valid_from": valid_from,
        "valid_until": valid_until,
        "measured_value": _num(measured_value),
        "uncertainty": _num(uncertainty),
        "unit": unit,
        "parent_cert_no": parent_cert_no,
        "alt_parent_cert_nos": sorted(set(alt_parent_cert_nos)),
        "issued_by": issued_by,
    }
    return json.dumps(payload, ensure_ascii=True, sort_keys=False, separators=(",", ":"))


def compute_digest(*args, **kwargs) -> str:
    return hashlib.sha256(canonical_payload(*args, **kwargs).encode("utf-8")).hexdigest()


def build_payload_for_orm(db: Session, cert: Certificate) -> str:
    from .models import CertParentLink, Instrument

    instrument = db.get(Instrument, cert.instrument_id)
    parent_no = cert.parent.cert_no if cert.parent else None
    alt_nos = [
        row[0]
        for row in db.query(Certificate.cert_no)
        .join(CertParentLink, CertParentLink.parent_certificate_id == Certificate.id)
        .filter(CertParentLink.certificate_id == cert.id)
        .all()
    ]
    return canonical_payload(
        cert_no=cert.cert_no,
        instrument_serial_no=instrument.serial_no if instrument else "",
        calibration_date=cert.calibration_date.isoformat(),
        valid_from=cert.valid_from.isoformat(),
        valid_until=cert.valid_until.isoformat(),
        measured_value=cert.measured_value,
        uncertainty=cert.uncertainty,
        unit=cert.unit,
        parent_cert_no=parent_no,
        alt_parent_cert_nos=alt_nos,
        issued_by=cert.issued_by,
    )


def recompute_digest(db: Session, cert: Certificate) -> str:
    return hashlib.sha256(build_payload_for_orm(db, cert).encode("utf-8")).hexdigest()
