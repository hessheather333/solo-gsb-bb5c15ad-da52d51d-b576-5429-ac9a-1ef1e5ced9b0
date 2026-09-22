"""把 TraceResult 序列化为 API 响应 / 导出 JSON 所需结构。"""
from __future__ import annotations

from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from .models import Instrument
from .traceability import PathResult, TraceResult


def _inst_map(db: Session) -> dict[int, Instrument]:
    return {i.id: i for i in db.scalars(select(Instrument)).all()}


def _node_dict(cert, checks: dict[str, Any], instruments: dict[int, Instrument], depth: int) -> dict[str, Any]:
    inst = instruments.get(cert.instrument_id)
    return {
        "depth": depth,
        "certificate_id": cert.id,
        "cert_no": cert.cert_no,
        "instrument_id": cert.instrument_id,
        "instrument_serial_no": inst.serial_no if inst else None,
        "instrument_name": inst.name if inst else None,
        "kind": inst.kind if inst else None,
        "calibration_date": cert.calibration_date.isoformat(),
        "valid_from": cert.valid_from.isoformat(),
        "valid_until": cert.valid_until.isoformat(),
        "measured_value": cert.measured_value,
        "uncertainty": cert.uncertainty,
        "unit": cert.unit,
        "issued_by": cert.issued_by,
        "is_national_anchor": bool(inst and inst.kind == "NATIONAL_STANDARD"),
        "node_checks": checks,
    }


def _edge_dict(child, parent, checks: dict[str, Any]) -> dict[str, Any]:
    return {
        "from_cert_no": child.cert_no,
        "to_cert_no": parent.cert_no,
        "edge_checks": checks,
    }


def _chain_dict(path: PathResult, instruments: dict[int, Instrument], index: int) -> dict[str, Any]:
    return {
        "chain_index": index,
        "node_count": len(path.nodes),
        "is_complete": path.reaches_national_standard,
        "reaches_national_standard": path.reaches_national_standard,
        "valid": path.valid,
        "terminal_reason": path.terminal_reason,
        "nodes": [
            _node_dict(cert, checks, instruments, depth)
            for depth, (cert, checks) in enumerate(path.nodes)
        ],
        "edges": [_edge_dict(c, p, chk) for c, p, chk in path.edges],
        "issues": [i.to_dict() for i in path.issues],
    }


def _broken_branch(path: PathResult, instruments: dict[int, Instrument]) -> dict[str, Any]:
    return {
        "start_cert_no": path.start_cert.cert_no,
        "terminal_reason": path.terminal_reason,
        "node_path": [cert.cert_no for cert, _ in path.nodes],
        "reaches_national_standard": path.reaches_national_standard,
        "terminal_cert_no": path.nodes[-1][0].cert_no if path.nodes else None,
        "terminal_instrument_serial_no": (
            instruments[path.nodes[-1][0].instrument_id].serial_no if path.nodes else None
        ),
        "issues": [i.to_dict() for i in path.issues if i.severity == "error"],
    }


def to_trace_response(db: Session, result: TraceResult) -> dict[str, Any]:
    from .traceability import _build_conflicts

    instruments = _inst_map(db)
    inst = result.instrument

    valid_paths = [p for p in result.paths if p.valid]
    candidate_paths = result.paths
    broken_paths = [p for p in result.paths if not p.valid]

    return {
        "instrument": {
            "id": inst.id,
            "serial_no": inst.serial_no,
            "name": inst.name,
            "manufacturer": inst.manufacturer,
            "model": inst.model,
            "kind": inst.kind,
        },
        "point_in_time": result.point_in_time.isoformat(),
        "status": result.status,
        "message": result.message,
        "valid_chains": [
            _chain_dict(p, instruments, i) for i, p in enumerate(valid_paths)
        ],
        "candidate_chains": [
            _chain_dict(p, instruments, i) for i, p in enumerate(candidate_paths)
        ],
        "broken_branches": [_broken_branch(p, instruments) for p in broken_paths],
        "issues": [i.to_dict() for i in result.issues],
        "conflicts": _build_conflicts(result.paths, instruments),
    }


def to_report(db: Session, result: TraceResult) -> dict[str, Any]:
    """校验报告：按节点/边汇总检查项与结论。"""
    base = to_trace_response(db, result)
    node_rows: list[dict[str, Any]] = []
    edge_rows: list[dict[str, Any]] = []
    seen_nodes: set[int] = set()
    seen_edges: set[tuple[int, int]] = set()

    for path in result.paths:
        for depth, (cert, checks) in enumerate(path.nodes):
            if cert.id in seen_nodes:
                continue
            seen_nodes.add(cert.id)
            inst = instruments_lookup(db, cert.instrument_id)
            node_rows.append(
                {
                    "depth_first_seen": depth,
                    "cert_no": cert.cert_no,
                    "instrument_serial_no": inst.serial_no if inst else None,
                    "checks": checks,
                    "passed": checks["in_interval"] and checks["signature_digest_ok"],
                }
            )
        for child, parent, checks in path.edges:
            key = (child.id, parent.id)
            if key in seen_edges:
                continue
            seen_edges.add(key)
            edge_rows.append(
                {
                    "from_cert_no": child.cert_no,
                    "to_cert_no": parent.cert_no,
                    "checks": checks,
                    "passed": all(
                        v is not False
                        for k, v in checks.items()
                        if k
                        in {
                            "parent_in_interval",
                            "interval_continuous",
                            "uncertainty_monotonic",
                            "units_compatible",
                            "calibration_order_ok",
                        }
                    ),
                }
            )

    errors = [i for i in result.issues if i.severity == "error"]
    warnings = [i for i in result.issues if i.severity == "warning"]
    base["report"] = {
        "summary": {
            "status": result.status,
            "valid_chain_count": len(base["valid_chains"]),
            "candidate_chain_count": len(base["candidate_chains"]),
            "broken_branch_count": len(base["broken_branches"]),
            "node_count_checked": len(node_rows),
            "edge_count_checked": len(edge_rows),
            "error_count": len(errors),
            "warning_count": len(warnings),
            "overall_passed": result.status == "UNIQUE_CHAIN",
        },
        "node_checks": node_rows,
        "edge_checks": edge_rows,
        "errors": [i.to_dict() for i in errors],
        "warnings": [i.to_dict() for i in warnings],
    }
    return base


def instruments_lookup(db: Session, instrument_id: int) -> Instrument | None:
    return db.get(Instrument, instrument_id)


def to_export(db: Session, result: TraceResult) -> dict[str, Any]:
    """JSON 导出：报告 + 涉及的仪器/证书全量数据。"""
    from sqlalchemy import select as _select

    from .models import CertParentLink, Certificate

    base = to_report(db, result)

    cert_ids = {c.id for path in result.paths for c, _ in path.nodes}
    cert_ids.update(c.id for c in result.start_certificates)
    involved_certs = db.scalars(
        _select(Certificate).where(Certificate.id.in_(cert_ids))
    ).all() if cert_ids else []
    inst_ids = {c.instrument_id for c in involved_certs}
    involved_insts = db.scalars(
        _select(Instrument).where(Instrument.id.in_(inst_ids))
    ).all() if inst_ids else []

    alt_map: dict[int, list[str]] = {}
    if cert_ids:
        rows = db.execute(
            _select(CertParentLink.certificate_id, Certificate.cert_no)
            .join(Certificate, Certificate.id == CertParentLink.parent_certificate_id)
            .where(CertParentLink.certificate_id.in_(cert_ids))
        ).all()
        for cert_id, parent_no in rows:
            alt_map.setdefault(cert_id, []).append(parent_no)

    cert_dicts = []
    for c in involved_certs:
        cert_dicts.append(
            {
                "id": c.id,
                "cert_no": c.cert_no,
                "instrument_id": c.instrument_id,
                "calibration_date": c.calibration_date.isoformat(),
                "valid_from": c.valid_from.isoformat(),
                "valid_until": c.valid_until.isoformat(),
                "measured_value": c.measured_value,
                "uncertainty": c.uncertainty,
                "unit": c.unit,
                "parent_cert_no": c.parent.cert_no if c.parent else None,
                "alt_parent_cert_nos": sorted(alt_map.get(c.id, [])),
                "signature_algorithm": c.signature_algorithm,
                "signature_digest": c.signature_digest,
                "issued_by": c.issued_by,
                "note": c.note,
            }
        )

    return {
        "export_version": "1.0",
        "trace": base,
        "instruments": [
            {
                "id": i.id,
                "serial_no": i.serial_no,
                "name": i.name,
                "manufacturer": i.manufacturer,
                "model": i.model,
                "kind": i.kind,
            }
            for i in involved_insts
        ],
        "certificates": cert_dicts,
    }
