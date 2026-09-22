"""仪器与证书录入接口。"""
from __future__ import annotations

from datetime import date

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from ..database import get_db
from ..digest import compute_digest
from ..models import CertParentLink, Certificate, Instrument
from ..schemas import CertificateCreate, CertificateOut, InstrumentCreate, InstrumentOut

router = APIRouter(tags=["录入"])


def _cert_out(db: Session, cert: Certificate) -> CertificateOut:
    alt_nos = [
        row[0]
        for row in db.query(Certificate.cert_no)
        .join(CertParentLink, CertParentLink.parent_certificate_id == Certificate.id)
        .filter(CertParentLink.certificate_id == cert.id)
        .all()
    ]
    return CertificateOut(
        id=cert.id,
        cert_no=cert.cert_no,
        instrument_id=cert.instrument_id,
        calibration_date=cert.calibration_date,
        valid_from=cert.valid_from,
        valid_until=cert.valid_until,
        measured_value=cert.measured_value,
        uncertainty=cert.uncertainty,
        unit=cert.unit,
        parent_cert_no=cert.parent.cert_no if cert.parent else None,
        alt_parent_cert_nos=sorted(alt_nos),
        signature_algorithm=cert.signature_algorithm,
        signature_digest=cert.signature_digest,
        issued_by=cert.issued_by,
        note=cert.note,
    )


@router.post("/instruments", response_model=InstrumentOut, status_code=status.HTTP_201_CREATED)
def create_instrument(payload: InstrumentCreate, db: Session = Depends(get_db)) -> Instrument:
    exists = db.scalar(select(Instrument).where(Instrument.serial_no == payload.serial_no))
    if exists:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=f"仪器编号 {payload.serial_no} 已存在",
        )
    inst = Instrument(**payload.model_dump())
    db.add(inst)
    try:
        db.commit()
    except IntegrityError:
        db.rollback()
        raise HTTPException(status_code=409, detail="仪器录入冲突")
    db.refresh(inst)
    return inst


@router.get("/instruments", response_model=list[InstrumentOut])
def list_instruments(db: Session = Depends(get_db)) -> list[Instrument]:
    return list(db.scalars(select(Instrument).order_by(Instrument.id)).all())


@router.get("/instruments/{serial_no}", response_model=InstrumentOut)
def get_instrument(serial_no: str, db: Session = Depends(get_db)) -> Instrument:
    inst = db.scalar(select(Instrument).where(Instrument.serial_no == serial_no))
    if not inst:
        raise HTTPException(status_code=404, detail=f"仪器 {serial_no} 不存在")
    return inst


@router.post("/certificates", response_model=CertificateOut, status_code=status.HTTP_201_CREATED)
def create_certificate(payload: CertificateCreate, db: Session = Depends(get_db)) -> CertificateOut:
    if payload.valid_from > payload.valid_until:
        raise HTTPException(status_code=422, detail="valid_from 不能晚于 valid_until")

    instrument = db.scalar(
        select(Instrument).where(Instrument.serial_no == payload.serial_no)
    )
    if not instrument:
        raise HTTPException(
            status_code=404, detail=f"仪器 {payload.serial_no} 不存在，请先登记仪器"
        )

    if db.scalar(select(Certificate).where(Certificate.cert_no == payload.cert_no)):
        raise HTTPException(status_code=409, detail=f"证书编号 {payload.cert_no} 已存在")

    parent: Certificate | None = None
    if payload.parent_cert_no:
        parent = db.scalar(
            select(Certificate).where(Certificate.cert_no == payload.parent_cert_no)
        )
        if not parent:
            raise HTTPException(
                status_code=404,
                detail=f"主溯源父证书 {payload.parent_cert_no} 不存在",
            )

    alt_parents: list[Certificate] = []
    for no in payload.alt_parent_cert_nos:
        p = db.scalar(select(Certificate).where(Certificate.cert_no == no))
        if not p:
            raise HTTPException(status_code=404, detail=f"备选父证书 {no} 不存在")
        alt_parents.append(p)

    # 签名摘要：客户端可显式提交；留空时由服务端按规范报文计算
    digest = payload.signature_digest
    if digest is None:
        digest = compute_digest(
            cert_no=payload.cert_no,
            instrument_serial_no=instrument.serial_no,
            calibration_date=payload.calibration_date.isoformat(),
            valid_from=payload.valid_from.isoformat(),
            valid_until=payload.valid_until.isoformat(),
            measured_value=payload.measured_value,
            uncertainty=payload.uncertainty,
            unit=payload.unit,
            parent_cert_no=payload.parent_cert_no,
            alt_parent_cert_nos=payload.alt_parent_cert_nos,
            issued_by=payload.issued_by,
        )

    cert = Certificate(
        cert_no=payload.cert_no,
        instrument_id=instrument.id,
        calibration_date=payload.calibration_date,
        valid_from=payload.valid_from,
        valid_until=payload.valid_until,
        measured_value=payload.measured_value,
        uncertainty=payload.uncertainty,
        unit=payload.unit,
        parent_id=parent.id if parent else None,
        signature_algorithm=payload.signature_algorithm,
        signature_digest=digest,
        issued_by=payload.issued_by,
        note=payload.note,
    )
    db.add(cert)
    db.flush()

    for p in alt_parents:
        if p.id == cert.id:
            raise HTTPException(status_code=422, detail="证书不能以自身为溯源父证书")
        db.add(CertParentLink(certificate_id=cert.id, parent_certificate_id=p.id))

    try:
        db.commit()
    except IntegrityError:
        db.rollback()
        raise HTTPException(status_code=409, detail="证书/溯源关系录入冲突")
    db.refresh(cert)
    return _cert_out(db, cert)


@router.get("/certificates", response_model=list[CertificateOut])
def list_certificates(serial_no: str | None = None, db: Session = Depends(get_db)) -> list[CertificateOut]:
    stmt = select(Certificate).order_by(Certificate.id)
    if serial_no:
        inst = db.scalar(select(Instrument).where(Instrument.serial_no == serial_no))
        if not inst:
            raise HTTPException(status_code=404, detail=f"仪器 {serial_no} 不存在")
        stmt = stmt.where(Certificate.instrument_id == inst.id)
    return [_cert_out(db, c) for c in db.scalars(stmt).all()]


@router.get("/certificates/{cert_no}", response_model=CertificateOut)
def get_certificate(cert_no: str, db: Session = Depends(get_db)) -> CertificateOut:
    cert = db.scalar(select(Certificate).where(Certificate.cert_no == cert_no))
    if not cert:
        raise HTTPException(status_code=404, detail=f"证书 {cert_no} 不存在")
    return _cert_out(db, cert)
