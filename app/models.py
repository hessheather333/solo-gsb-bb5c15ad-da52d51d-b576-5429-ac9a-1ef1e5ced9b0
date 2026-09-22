"""ORM 模型：仪器、校准证书、证书间父子溯源关系。"""
from __future__ import annotations

from datetime import date

from sqlalchemy import (
    CheckConstraint,
    Date,
    Float,
    ForeignKey,
    Integer,
    String,
    Text,
    UniqueConstraint,
)
from sqlalchemy.orm import Mapped, mapped_column, relationship

from .database import Base


class Instrument(Base):
    __tablename__ = "instruments"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    serial_no: Mapped[str] = mapped_column(String(128), unique=True, nullable=False, index=True)
    name: Mapped[str] = mapped_column(String(255), nullable=False)
    manufacturer: Mapped[str | None] = mapped_column(String(255))
    model: Mapped[str | None] = mapped_column(String(128))
    # kind: NATIONAL_STANDARD（国家基准）/ REFERENCE_STANDARD（实验室标准器）/ WORKING（工作计量器具）
    kind: Mapped[str] = mapped_column(String(32), nullable=False, default="WORKING")

    certificates: Mapped[list["Certificate"]] = relationship(
        back_populates="instrument", cascade="all, delete-orphan"
    )


class Certificate(Base):
    __tablename__ = "certificates"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    cert_no: Mapped[str] = mapped_column(String(128), unique=True, nullable=False, index=True)
    instrument_id: Mapped[int] = mapped_column(
        ForeignKey("instruments.id", ondelete="CASCADE"), nullable=False, index=True
    )

    calibration_date: Mapped[date] = mapped_column(Date, nullable=False)
    valid_from: Mapped[date] = mapped_column(Date, nullable=False)
    valid_until: Mapped[date] = mapped_column(Date, nullable=False)

    # 测得值与测量不确定度（k=2 扩展不确定度）
    measured_value: Mapped[float | None] = mapped_column(Float)
    uncertainty: Mapped[float | None] = mapped_column(Float)
    unit: Mapped[str | None] = mapped_column(String(32))

    # 主要溯源父证书（可空：国家基准证书为溯源链顶端）
    parent_id: Mapped[int | None] = mapped_column(
        ForeignKey("certificates.id", ondelete="SET NULL"), nullable=True
    )
    # 签名算法与签名摘要（对规范报文的 SHA-256）
    signature_algorithm: Mapped[str] = mapped_column(String(32), nullable=False, default="SHA256")
    signature_digest: Mapped[str] = mapped_column(String(128), nullable=False)
    issued_by: Mapped[str | None] = mapped_column(String(255))
    note: Mapped[str | None] = mapped_column(Text)

    instrument: Mapped[Instrument] = relationship(back_populates="certificates")
    parent: Mapped["Certificate | None"] = relationship(remote_side="Certificate.id", foreign_keys=[parent_id])
    alt_parents: Mapped[list["Certificate"]] = relationship(
        secondary="cert_parent_links",
        primaryjoin="Certificate.id == CertParentLink.certificate_id",
        secondaryjoin="Certificate.id == CertParentLink.parent_certificate_id",
    )

    __table_args__ = (
        CheckConstraint("valid_from <= valid_until", name="ck_cert_interval_order"),
        CheckConstraint("uncertainty IS NULL OR uncertainty >= 0", name="ck_cert_uncertainty_nonneg"),
    )


class CertParentLink(Base):
    """备选/并行溯源关系：一张证书除主父证书外还可声明其他溯源父证书。"""

    __tablename__ = "cert_parent_links"

    certificate_id: Mapped[int] = mapped_column(
        ForeignKey("certificates.id", ondelete="CASCADE"), primary_key=True
    )
    parent_certificate_id: Mapped[int] = mapped_column(
        ForeignKey("certificates.id", ondelete="CASCADE"), primary_key=True
    )

    __table_args__ = (
        UniqueConstraint("certificate_id", "parent_certificate_id", name="uq_cert_parent_pair"),
        CheckConstraint("certificate_id <> parent_certificate_id", name="ck_no_self_parent"),
    )
