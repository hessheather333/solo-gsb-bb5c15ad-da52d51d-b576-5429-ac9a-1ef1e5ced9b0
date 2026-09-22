"""演示数据初始化：覆盖唯一链、多候选链、区间断裂、死端、签名篡改、不确定度不合规等场景。

可通过环境变量 SEED_DEMO=true 在容器启动时自动执行，或单独运行：

    python -m app.seed
"""
from __future__ import annotations

from datetime import date

from sqlalchemy import select

from .database import SessionLocal
from .digest import compute_digest
from .models import CertParentLink, Certificate, Instrument

SEED_MARKER_SERIAL = "MFR-2026-001"


def _instrument(db, serial_no: str, name: str, kind: str, manufacturer: str, model: str) -> Instrument:
    inst = db.scalar(select(Instrument).where(Instrument.serial_no == serial_no))
    if inst:
        return inst
    inst = Instrument(
        serial_no=serial_no, name=name, kind=kind, manufacturer=manufacturer, model=model
    )
    db.add(inst)
    db.flush()
    return inst


def _certificate(
    db,
    *,
    cert_no: str,
    instrument: Instrument,
    calibration_date: date,
    valid_from: date,
    valid_until: date,
    measured_value: float,
    uncertainty: float,
    unit: str = "V",
    parent: Certificate | None = None,
    alt_parents: list[Certificate] | None = None,
    issued_by: str = "国家计量院校准实验室",
    tampered: bool = False,
    note: str | None = None,
) -> Certificate:
    existing = db.scalar(select(Certificate).where(Certificate.cert_no == cert_no))
    if existing:
        return existing

    parent_no = parent.cert_no if parent else None
    alt_nos = [p.cert_no for p in (alt_parents or [])]
    digest = compute_digest(
        cert_no=cert_no,
        instrument_serial_no=instrument.serial_no,
        calibration_date=calibration_date.isoformat(),
        valid_from=valid_from.isoformat(),
        valid_until=valid_until.isoformat(),
        measured_value=measured_value,
        uncertainty=uncertainty,
        unit=unit,
        parent_cert_no=parent_no,
        alt_parent_cert_nos=alt_nos,
        issued_by=issued_by,
    )
    if tampered:
        # 模拟证书内容被篡改后与签名不一致
        digest = "0" * 64

    cert = Certificate(
        cert_no=cert_no,
        instrument_id=instrument.id,
        calibration_date=calibration_date,
        valid_from=valid_from,
        valid_until=valid_until,
        measured_value=measured_value,
        uncertainty=uncertainty,
        unit=unit,
        parent_id=parent.id if parent else None,
        signature_algorithm="SHA256",
        signature_digest=digest,
        issued_by=issued_by,
        note=note,
    )
    db.add(cert)
    db.flush()
    for p in alt_parents or []:
        db.add(CertParentLink(certificate_id=cert.id, parent_certificate_id=p.id))
    return cert


def seed_demo() -> None:
    db = SessionLocal()
    try:
        if db.scalar(
            select(Instrument).where(Instrument.serial_no == SEED_MARKER_SERIAL)
        ):
            return

        # ---------- 国家基准（溯源链顶端） ----------
        nat1 = _instrument(
            db, "NAT-VOLT-001", "国家直流电压基准(组一)", "NATIONAL_STANDARD",
            "NIM", "NAT-JVS-1",
        )
        nat2 = _instrument(
            db, "NAT-VOLT-002", "国家直流电压基准(组二)", "NATIONAL_STANDARD",
            "NIM", "NAT-JVS-2",
        )

        nat_cert_1 = _certificate(
            db,
            cert_no="NAT-CERT-001",
            instrument=nat1,
            calibration_date=date(2024, 1, 10),
            valid_from=date(2024, 1, 10),
            valid_until=date(2029, 1, 10),
            measured_value=10.0,
            uncertainty=0.0000001,
            issued_by="国家计量基准保存机构",
            note="约瑟夫森量子电压基准",
        )
        nat_cert_2 = _certificate(
            db,
            cert_no="NAT-CERT-002",
            instrument=nat2,
            calibration_date=date(2024, 2, 10),
            valid_from=date(2024, 2, 10),
            valid_until=date(2029, 2, 10),
            measured_value=10.0,
            uncertainty=0.0000001,
            issued_by="国家计量基准保存机构",
            note="约瑟夫森量子电压基准(备份组)",
        )

        # ---------- 实验室标准器 ----------
        ref1 = _instrument(
            db, "LAB-REF-001", "实验室直流电压标准器(主)", "REFERENCE_STANDARD",
            "Fluke", "732B-A",
        )
        ref2 = _instrument(
            db, "LAB-REF-002", "实验室直流电压标准器(备)", "REFERENCE_STANDARD",
            "Fluke", "732B-B",
        )
        ref_cert_1 = _certificate(
            db,
            cert_no="C-REF-001",
            instrument=ref1,
            calibration_date=date(2025, 1, 15),
            valid_from=date(2025, 1, 15),
            valid_until=date(2027, 1, 15),
            measured_value=10.0,
            uncertainty=0.00001,
            parent=nat_cert_1,
            note="实验室一等标准器",
        )
        ref_cert_2 = _certificate(
            db,
            cert_no="C-REF-002",
            instrument=ref2,
            calibration_date=date(2025, 2, 20),
            valid_from=date(2025, 2, 20),
            valid_until=date(2027, 2, 20),
            measured_value=10.0,
            uncertainty=0.00001,
            parent=nat_cert_2,
            note="实验室一等标准器(独立备份链路)",
        )
        # 上一周期的标准器证书：2026-08-01 已到期（用于区间断裂场景）
        ref_cert_old = _certificate(
            db,
            cert_no="C-REF-001-OLD",
            instrument=ref1,
            calibration_date=date(2024, 12, 20),
            valid_from=date(2024, 12, 20),
            valid_until=date(2026, 8, 1),
            measured_value=10.0,
            uncertainty=0.00001,
            parent=nat_cert_1,
            note="上一校准周期标准器证书，已到期",
        )

        # ---------- 工作计量器具 ----------
        w1 = _instrument(db, "MFR-2026-001", "数字多用表(唯一链)", "WORKING", "Keysight", "3458A")
        _certificate(
            db,
            cert_no="C-W-001",
            instrument=w1,
            calibration_date=date(2026, 5, 20),
            valid_from=date(2026, 6, 1),
            valid_until=date(2027, 6, 1),
            measured_value=10.00002,
            uncertainty=0.0001,
            parent=ref_cert_1,
        )

        w2 = _instrument(db, "MFR-2026-002", "数字多用表(双溯源链)", "WORKING", "Keysight", "34465A")
        _certificate(
            db,
            cert_no="C-W-002",
            instrument=w2,
            calibration_date=date(2026, 5, 22),
            valid_from=date(2026, 6, 1),
            valid_until=date(2027, 6, 1),
            measured_value=10.00003,
            uncertainty=0.0001,
            parent=ref_cert_1,
            alt_parents=[ref_cert_2],
            note="同时声明主、备两条国家基准溯源路径",
        )

        w3 = _instrument(db, "MFR-2026-003", "直流电压源(区间断裂)", "WORKING", "Fluke", "5522A")
        _certificate(
            db,
            cert_no="C-W-003",
            instrument=w3,
            calibration_date=date(2026, 7, 25),
            valid_from=date(2026, 8, 2),
            valid_until=date(2027, 8, 2),
            measured_value=10.00005,
            uncertainty=0.0001,
            parent=ref_cert_old,
            note="子证书生效时引用的父标准器证书(C-REF-001-OLD)已过期，区间断裂",
        )

        # 无溯源父证书的非基准仪器（死端）
        orphan = _instrument(
            db, "UNK-REF-001", "来源不明标准器", "REFERENCE_STANDARD", "Unknown", "X-1"
        )
        orphan_cert = _certificate(
            db,
            cert_no="C-ORPHAN-001",
            instrument=orphan,
            calibration_date=date(2026, 1, 10),
            valid_from=date(2026, 1, 10),
            valid_until=date(2028, 1, 10),
            measured_value=10.0,
            uncertainty=0.00002,
            parent=None,
            issued_by="未知机构",
            note="未声明国家基准溯源关系",
        )
        w4 = _instrument(db, "MFR-2026-004", "多功能校准仪(溯源死端)", "WORKING", "Fluke", "9100")
        _certificate(
            db,
            cert_no="C-W-004",
            instrument=w4,
            calibration_date=date(2026, 4, 10),
            valid_from=date(2026, 4, 15),
            valid_until=date(2027, 4, 15),
            measured_value=10.0001,
            uncertainty=0.0002,
            parent=orphan_cert,
        )

        w5 = _instrument(db, "MFR-2026-005", "数字多用表(签名被篡改)", "WORKING", "RIGOL", "DM3068")
        _certificate(
            db,
            cert_no="C-W-005",
            instrument=w5,
            calibration_date=date(2026, 5, 25),
            valid_from=date(2026, 6, 1),
            valid_until=date(2027, 6, 1),
            measured_value=10.00008,
            uncertainty=0.0001,
            parent=ref_cert_1,
            tampered=True,
            note="签名摘要与重算结果不一致（篡改演示）",
        )

        w6 = _instrument(db, "MFR-2026-006", "数字多用表(不确定度不合规)", "WORKING", "Keithley", "DMM6500")
        _certificate(
            db,
            cert_no="C-W-006",
            instrument=w6,
            calibration_date=date(2026, 5, 28),
            valid_from=date(2026, 6, 1),
            valid_until=date(2027, 6, 1),
            measured_value=10.000001,
            uncertainty=0.0000005,  # 小于父证书 C-REF-001 的 0.00001
            parent=ref_cert_1,
            note="子证书不确定度小于父证书，违反单调不减原则",
        )

        db.commit()
    finally:
        db.close()


if __name__ == "__main__":
    seed_demo()
    print("演示数据已写入")
