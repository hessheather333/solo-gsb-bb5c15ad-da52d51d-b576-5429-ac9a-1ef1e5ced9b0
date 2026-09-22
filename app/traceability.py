"""溯源链重建与校验引擎。

给定仪器与查询时点 ``point_in_time``：

1. 取该仪器在该时点处于有效期内的全部证书作为链起点（可能有多个并行证书）；
2. 沿主父证书 + 备选父证书关系做深度优先遍历，枚举出全部简单路径；
3. 对每个节点校验：有效期覆盖、签名摘要（SHA-256 重算比对）；
4. 对每条父子边校验：
   - 父证书在时点是否有效；
   - 区间连续性（子证书生效日落在父证书有效期内）；
   - 测量不确定度沿溯源方向单调不减（子不确定度 ≥ 父不确定度）；
   - 计量单位一致；
5. 到达“国家基准”仪器证书且无父证书的路径为完整候选链；
6. 多条有效链 → MULTIPLE_CHAINS；断裂/死端/环 → BROKEN_CHAIN 并给出冲突节点。
"""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from .digest import recompute_digest
from .models import CertParentLink, Certificate, Instrument


# 问题严重级别
ERROR = "error"
WARNING = "warning"


@dataclass
class Issue:
    code: str
    severity: str
    message: str
    cert_no: str | None = None
    instrument_serial_no: str | None = None
    parent_cert_no: str | None = None
    details: dict[str, Any] = field(default_factory=dict)

    def key(self) -> tuple:
        import json

        return (
            self.code,
            self.cert_no,
            self.parent_cert_no,
            json.dumps(self.details, sort_keys=True, default=str),
        )

    def to_dict(self) -> dict[str, Any]:
        return {
            "code": self.code,
            "severity": self.severity,
            "message": self.message,
            "cert_no": self.cert_no,
            "instrument_serial_no": self.instrument_serial_no,
            "parent_cert_no": self.parent_cert_no,
            "details": self.details,
        }


@dataclass
class PathResult:
    """一条从仪器证书出发的溯源路径（完整或断裂）。"""

    start_cert: Certificate
    nodes: list[tuple[Certificate, dict[str, Any]]]
    edges: list[tuple[Certificate, Certificate, dict[str, Any]]]
    issues: list[Issue]
    reaches_national_standard: bool
    terminal_reason: str  # NATIONAL_ANCHOR / DEAD_END / EXPIRED_NODE / CYCLE

    @property
    def cert_ids(self) -> tuple[int, ...]:
        return tuple(c.id for c, _ in self.nodes)

    @property
    def valid(self) -> bool:
        return self.reaches_national_standard and all(
            i.severity != ERROR for i in self.issues
        )


@dataclass
class TraceResult:
    instrument: Instrument
    point_in_time: date
    start_certificates: list[Certificate]
    paths: list[PathResult]
    issues: list[Issue]
    status: str
    message: str


def _issue(
    code: str,
    severity: str,
    message: str,
    cert: Certificate | None = None,
    instruments: dict[int, Instrument] | None = None,
    parent: Certificate | None = None,
    details: dict[str, Any] | None = None,
) -> Issue:
    return Issue(
        code=code,
        severity=severity,
        message=message,
        cert_no=cert.cert_no if cert else None,
        instrument_serial_no=(
            instruments.get(cert.instrument_id).serial_no
            if cert and instruments and cert.instrument_id in instruments
            else None
        ),
        parent_cert_no=parent.cert_no if parent else None,
        details=details or {},
    )


# ---------------------------------------------------------------- 图构建
def _build_graph(db: Session) -> tuple[dict[int, Certificate], dict[int, set[int]], dict[int, Instrument]]:
    certs: dict[int, Certificate] = {c.id: c for c in db.scalars(select(Certificate)).all()}
    instruments = {i.id: i for i in db.scalars(select(Instrument)).all()}

    parents: dict[int, set[int]] = {cid: set() for cid in certs}
    for c in certs.values():
        if c.parent_id is not None and c.parent_id in certs:
            parents[c.id].add(c.parent_id)
    for link in db.scalars(select(CertParentLink)).all():
        if link.certificate_id in certs and link.parent_certificate_id in certs:
            parents[link.certificate_id].add(link.parent_certificate_id)
    return certs, parents, instruments


def _eligible_roots(certs: dict[int, Certificate], instruments: dict[int, Instrument], pit: date) -> set[int]:
    """国家基准锚点：仪器为国家基准、证书在时点有效、且未声明任何父证书。"""
    roots: set[int] = set()
    for cid, c in certs.items():
        inst = instruments.get(c.instrument_id)
        if (
            inst is not None
            and inst.kind == "NATIONAL_STANDARD"
            and c.valid_from <= pit <= c.valid_until
        ):
            roots.add(cid)
    return roots


# ---------------------------------------------------------------- 节点/边校验
def _node_checks(db: Session, cert: Certificate, pit: date) -> dict[str, Any]:
    in_interval = cert.valid_from <= pit <= cert.valid_until
    expected = recompute_digest(db, cert)
    signature_ok = expected.lower() == (cert.signature_digest or "").lower()
    return {
        "in_interval": in_interval,
        "valid_from": cert.valid_from.isoformat(),
        "valid_until": cert.valid_until.isoformat(),
        "signature_algorithm": cert.signature_algorithm,
        "signature_digest_ok": signature_ok,
        "stored_digest": cert.signature_digest,
        "expected_digest": expected,
    }


def _edge_checks(child: Certificate, parent: Certificate, pit: date) -> dict[str, Any]:
    parent_in_interval = parent.valid_from <= pit <= parent.valid_until
    interval_continuous = parent.valid_from <= child.valid_from <= parent.valid_until
    calibration_order_ok = child.calibration_date >= parent.calibration_date

    uncertainty_monotonic: bool | None = None
    if child.uncertainty is not None and parent.uncertainty is not None:
        uncertainty_monotonic = child.uncertainty >= parent.uncertainty

    units_compatible: bool | None = None
    if child.unit is not None and parent.unit is not None:
        units_compatible = child.unit == parent.unit

    return {
        "parent_in_interval": parent_in_interval,
        "interval_continuous": interval_continuous,
        "child_valid_from": child.valid_from.isoformat(),
        "parent_valid_from": parent.valid_from.isoformat(),
        "parent_valid_until": parent.valid_until.isoformat(),
        "uncertainty_monotonic": uncertainty_monotonic,
        "child_uncertainty": child.uncertainty,
        "parent_uncertainty": parent.uncertainty,
        "units_compatible": units_compatible,
        "child_unit": child.unit,
        "parent_unit": parent.unit,
        "calibration_order_ok": calibration_order_ok,
    }


def _edge_issues(
    child: Certificate, parent: Certificate, checks: dict[str, Any], instruments: dict[int, Instrument]
) -> list[Issue]:
    issues: list[Issue] = []
    if not checks["parent_in_interval"]:
        issues.append(
            _issue(
                "EDGE_PARENT_EXPIRED",
                ERROR,
                f"父证书 {parent.cert_no} 在查询时点不在有效期内",
                cert=child,
                instruments=instruments,
                parent=parent,
                details={
                    "point_in_time_required": True,
                    "parent_valid_from": checks["parent_valid_from"],
                    "parent_valid_until": checks["parent_valid_until"],
                },
            )
        )
    if not checks["interval_continuous"]:
        issues.append(
            _issue(
                "INTERVAL_GAP",
                ERROR,
                f"证书 {child.cert_no} 的生效日 {checks['child_valid_from']} 未落"
                f"在父证书 {parent.cert_no} 的有效期 "
                f"[{checks['parent_valid_from']}, {checks['parent_valid_until']}] 内，溯源区间断裂",
                cert=child,
                instruments=instruments,
                parent=parent,
                details=checks,
            )
        )
    if checks["uncertainty_monotonic"] is False:
        issues.append(
            _issue(
                "UNCERTAINTY_NOT_MONOTONIC",
                ERROR,
                f"证书 {child.cert_no} 的不确定度 {checks['child_uncertainty']} "
                f"小于父证书 {parent.cert_no} 的 {checks['parent_uncertainty']}，"
                "沿溯源方向不确定度必须单调不减",
                cert=child,
                instruments=instruments,
                parent=parent,
                details=checks,
            )
        )
    if checks["units_compatible"] is False:
        issues.append(
            _issue(
                "UNIT_MISMATCH",
                ERROR,
                f"证书 {child.cert_no} 单位 {checks['child_unit']} 与父证书 "
                f"{parent.cert_no} 单位 {checks['parent_unit']} 不一致",
                cert=child,
                instruments=instruments,
                parent=parent,
                details=checks,
            )
        )
    if not checks["calibration_order_ok"]:
        issues.append(
            _issue(
                "CALIBRATION_ORDER",
                WARNING,
                f"证书 {child.cert_no} 的校准日期早于父证书 {parent.cert_no}，请核查溯源次序",
                cert=child,
                instruments=instruments,
                parent=parent,
                details=checks,
            )
        )
    return issues


# ---------------------------------------------------------------- 路径枚举
def _enumerate_paths(
    db: Session,
    start: Certificate,
    certs: dict[int, Certificate],
    parents: dict[int, set[int]],
    instruments: dict[int, Instrument],
    roots: set[int],
    pit: date,
) -> list[PathResult]:
    found: list[PathResult] = []

    def walk(
        cert: Certificate,
        visited: set[int],
        nodes: list[tuple[Certificate, dict[str, Any]]],
        edges: list[tuple[Certificate, Certificate, dict[str, Any]]],
        issues: list[Issue],
    ) -> None:
        checks = _node_checks(db, cert, pit)
        nodes = nodes + [(cert, checks)]

        if not checks["in_interval"]:
            issues = issues + [
                _issue(
                    "NODE_NOT_IN_INTERVAL",
                    ERROR,
                    f"证书 {cert.cert_no} 在查询时点已失效或尚未生效 "
                    f"[{checks['valid_from']}, {checks['valid_until']}]",
                    cert=cert,
                    instruments=instruments,
                    details=checks,
                )
            ]
            found.append(PathResult(start, nodes, edges, issues, False, "EXPIRED_NODE"))
            return

        if not checks["signature_digest_ok"]:
            issues = issues + [
                _issue(
                    "SIGNATURE_MISMATCH",
                    ERROR,
                    f"证书 {cert.cert_no} 的签名摘要与规范报文重算结果不一致，证书内容可能被篡改",
                    cert=cert,
                    instruments=instruments,
                    details={
                        "stored_digest": checks["stored_digest"],
                        "expected_digest": checks["expected_digest"],
                    },
                )
            ]

        if cert.id in roots and not parents.get(cert.id):
            found.append(PathResult(start, nodes, edges, issues, True, "NATIONAL_ANCHOR"))
            return

        # 国家基准证书却仍声明父证书：继续向上遍历
        parent_ids = sorted(parents.get(cert.id, set()))
        if not parent_ids:
            inst = instruments.get(cert.instrument_id)
            issues = issues + [
                _issue(
                    "DEAD_END",
                    ERROR,
                    f"证书 {cert.cert_no}（{getattr(inst, 'kind', '?')}）未声明任何溯源"
                    "父证书且不属于国家基准，溯源链在该节点终止",
                    cert=cert,
                    instruments=instruments,
                    details={"terminal_kind": getattr(inst, "kind", None)},
                )
            ]
            found.append(PathResult(start, nodes, edges, issues, False, "DEAD_END"))
            return

        for pid in parent_ids:
            parent = certs[pid]
            edge_checks = _edge_checks(cert, parent, pit)
            edge_issues = _edge_issues(cert, parent, edge_checks, instruments)
            new_edges = edges + [(cert, parent, edge_checks)]

            if pid in visited:
                cyc_issue = _issue(
                    "CYCLE",
                    ERROR,
                    f"证书 {cert.cert_no} 向上溯源回到 {parent.cert_no}，溯源关系存在环",
                    cert=cert,
                    instruments=instruments,
                    parent=parent,
                    details={"cycle_cert_no": parent.cert_no},
                )
                found.append(
                    PathResult(
                        start,
                        nodes,
                        new_edges,
                        issues + edge_issues + [cyc_issue],
                        False,
                        "CYCLE",
                    )
                )
                continue

            walk(
                parent,
                visited | {pid},
                nodes,
                new_edges,
                issues + edge_issues,
            )

    walk(start, {start.id}, [], [], [])

    # 同一组证书可能经不同边顺序重复，按证书序列去重
    deduped: dict[tuple[int, ...], PathResult] = {}
    for p in found:
        deduped.setdefault(p.cert_ids, p)
    return list(deduped.values())


# ---------------------------------------------------------------- 冲突节点
def _build_conflicts(result_paths: list[PathResult], instruments: dict[int, Instrument]) -> list[dict[str, Any]]:
    conflicts: list[dict[str, Any]] = []
    valid_paths = [p for p in result_paths if p.valid]

    # 1) 多条有效链：定位分叉点
    if len(valid_paths) >= 2:
        chains = [p.cert_ids for p in valid_paths]
        # 起点不同：同一仪器在时点上存在多张有效证书
        start_groups: dict[int, list[PathResult]] = {}
        for p in valid_paths:
            start_groups.setdefault(p.start_cert.id, []).append(p)
        if len(start_groups) >= 2:
            conflicts.append(
                {
                    "type": "MULTIPLE_ACTIVE_CERTIFICATES",
                    "cert_no": None,
                    "instrument_serial_no": instruments[
                        valid_paths[0].start_cert.instrument_id
                    ].serial_no,
                    "message": "该仪器在查询时点存在多张同时有效的校准证书，无法确定唯一溯源链",
                    "details": {
                        "start_cert_nos": [
                            p.start_cert.cert_no for p in valid_paths
                        ],
                    },
                }
            )

        # 组内分叉：找最长公共前缀后的那张分叉证书
        for _, group in start_groups.items():
            if len(group) < 2:
                continue
            id_chains = [p.cert_ids for p in group]
            depth = 0
            while True:
                col = {c[depth] for c in id_chains if depth < len(c)}
                if len(col) != 1:
                    break
                depth += 1
                if any(depth >= len(c) for c in id_chains):
                    break
            if depth >= 1:
                fork = group[0].nodes[depth - 1][0]
                conflicts.append(
                    {
                        "type": "FORK_MULTIPLE_PARENTS",
                        "cert_no": fork.cert_no,
                        "instrument_serial_no": instruments[fork.instrument_id].serial_no,
                        "message": f"证书 {fork.cert_no} 在该时点存在多条可同时到达国家基准的溯源路径",
                        "details": {
                            "fork_depth": depth - 1,
                            "branch_head_cert_nos": [
                                p.nodes[depth][0].cert_no if depth < len(p.nodes) else None
                                for p in group
                            ],
                            "candidate_chain_count": len(group),
                        },
                    }
                )

    # 2) 断裂类冲突：直接映射问题节点
    seen = set()
    for p in result_paths:
        for iss in p.issues:
            if iss.severity != ERROR:
                continue
            if iss.code in {
                "INTERVAL_GAP",
                "NODE_NOT_IN_INTERVAL",
                "EDGE_PARENT_EXPIRED",
                "DEAD_END",
                "CYCLE",
                "SIGNATURE_MISMATCH",
                "UNCERTAINTY_NOT_MONOTONIC",
                "UNIT_MISMATCH",
            }:
                k = (iss.code, iss.cert_no, iss.parent_cert_no)
                if k in seen:
                    continue
                seen.add(k)
                conflicts.append(
                    {
                        "type": iss.code,
                        "cert_no": iss.cert_no,
                        "instrument_serial_no": iss.instrument_serial_no,
                        "message": iss.message,
                        "details": {
                            **iss.details,
                            "parent_cert_no": iss.parent_cert_no,
                        },
                    }
                )
    return conflicts


# ---------------------------------------------------------------- 入口
def trace(db: Session, instrument: Instrument, pit: date) -> TraceResult:
    certs, parents, instruments = _build_graph(db)
    roots = _eligible_roots(certs, instruments, pit)

    start_certs = sorted(
        (
            c
            for c in certs.values()
            if c.instrument_id == instrument.id and c.valid_from <= pit <= c.valid_until
        ),
        key=lambda c: (c.calibration_date, c.cert_no),
        reverse=True,
    )

    paths: list[PathResult] = []
    for start in start_certs:
        paths.extend(_enumerate_paths(db, start, certs, parents, instruments, roots, pit))

    # 全局问题去重
    issue_map: dict[tuple, Issue] = {}
    for p in paths:
        for i in p.issues:
            issue_map.setdefault(i.key(), i)
    issues = list(issue_map.values())

    valid_paths = [p for p in paths if p.valid]

    if not start_certs:
        status, message = (
            "NO_CERTIFICATE",
            f"仪器 {instrument.serial_no} 在 {pit.isoformat()} 没有处于有效期内的校准证书",
        )
    elif len(valid_paths) >= 2:
        status, message = (
            "MULTIPLE_CHAINS",
            f"仪器 {instrument.serial_no} 在 {pit.isoformat()} 存在 {len(valid_paths)} 条"
            "可到达国家基准的有效溯源链，无法重建唯一证书链",
        )
    elif len(valid_paths) == 1:
        if any(not p.valid for p in paths):
            status = "CONFLICT"
            message = (
                f"仪器 {instrument.serial_no} 在 {pit.isoformat()} 存在 1 条有效溯源链，"
                "但同时存在断裂的备选溯源分支，请核查冲突节点"
            )
        else:
            status = "UNIQUE_CHAIN"
            message = (
                f"仪器 {instrument.serial_no} 在 {pit.isoformat()} 的溯源链唯一且校验通过"
            )
    else:
        status, message = (
            "BROKEN_CHAIN",
            f"仪器 {instrument.serial_no} 在 {pit.isoformat()} 不存在可到达国家基准的"
            "完整有效溯源链",
        )

    return TraceResult(
        instrument=instrument,
        point_in_time=pit,
        start_certificates=start_certs,
        paths=paths,
        issues=issues,
        status=status,
        message=message,
    )


def conflicts_of(result: TraceResult, instruments: dict[int, Instrument] | None = None, db: Session | None = None) -> list[dict[str, Any]]:
    if instruments is None:
        if db is None:
            raise ValueError("instruments 或 db 至少提供一个")
        instruments = {i.id: i for i in db.scalars(select(Instrument)).all()}
    return _build_conflicts(result.paths, instruments)
