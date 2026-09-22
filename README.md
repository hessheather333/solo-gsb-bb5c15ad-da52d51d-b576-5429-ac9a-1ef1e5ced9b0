# 计量校准溯源 API

基于 **Python + FastAPI + SQLite** 的实验室计量校准溯源服务。

实验室提交仪器信息、校准证书、证书之间的父子溯源关系及有效区间后，服务可以在**任意指定时点**从被校仪器出发，向上重建到达**国家计量基准**的证书链，并对每条候选链执行：

- **有效区间校验**：节点证书在查询时点是否有效；
- **区间连续性校验**：子证书生效日是否落在父证书有效期内（断裂即报错）；
- **签名摘要校验**：对证书规范报文重算 SHA-256，与提交的 `signature_digest` 比对（识别篡改）；
- **测量不确定度校验**：沿溯源方向（仪器 → 国家基准）不确定度必须**单调不减**，并检查计量单位一致。

当同一时点存在**多条可到达国家基准的有效链**（多候选链）或出现区间断裂、死端、环、摘要不一致时，接口返回结构化的 `conflicts` 冲突节点，说明冲突发生在哪张证书、哪条父子边及原因。

## 目录结构

```
app/
├── main.py            # FastAPI 入口、建表、可选演示数据
├── database.py        # SQLite 引擎与会话
├── models.py          # 仪器 / 证书 / 备选父证书关系 表模型
├── schemas.py         # 请求与响应模型
├── digest.py          # 证书规范报文与 SHA-256 摘要
├── traceability.py    # 溯源链重建与校验引擎（DFS 枚举全部候选链）
├── serializers.py     # 链路响应 / 校验报告 / JSON 导出
├── seed.py            # 6 种冲突场景的演示数据
└── routers/
    ├── records.py     # 仪器、证书录入与查询
    └── trace.py       # 链路查询、校验报告、JSON 导出
Dockerfile
docker-compose.yml
```

## 一键启动（Docker Compose）

```bash
docker compose up -d --build
```

容器首次启动会自动建表并写入演示数据（`SEED_DEMO=true`），SQLite 数据文件保存在命名卷 `trace-data` 中。

服务地址：

| 用途 | 地址 |
| --- | --- |
| 服务根信息 | http://localhost:8000/ |
| 健康检查 | http://localhost:8000/health |
| Swagger 交互文档 | http://localhost:8000/docs |
| ReDoc 文档 | http://localhost:8000/redoc |
| OpenAPI Schema | http://localhost:8000/openapi.json |

停止 / 清理：

```bash
docker compose down            # 停止并删除容器（保留数据卷）
docker compose down -v         # 连同演示数据库一起删除
```

## 本地直接运行（可选）

```bash
python -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt

SEED_DEMO=true uvicorn app.main:app --reload
```

## API 一览

### 数据录入

| 方法 | 路径 | 说明 |
| --- | --- | --- |
| POST | `/instruments` | 登记仪器（`kind`: `NATIONAL_STANDARD` / `REFERENCE_STANDARD` / `WORKING`） |
| GET | `/instruments`、`/instruments/{serial_no}` | 仪器列表/详情 |
| POST | `/certificates` | 提交校准证书（`signature_digest` 留空时服务端自动计算） |
| GET | `/certificates`（可带 `?serial_no=`）、`/certificates/{cert_no}` | 证书列表/详情 |

### 溯源（核心接口，均以 `instrument_id` 或 `serial_no` + `point_in_time` 查询）

| 方法 | 路径 | 说明 |
| --- | --- | --- |
| GET | `/trace` | 重建溯源链：`valid_chains` / `candidate_chains` / `broken_branches` / `issues` / `conflicts` |
| GET | `/report` | 校验报告：节点级、边级逐项检查结果与错误/警告汇总 |
| GET | `/export` | JSON 导出：完整报告 + 涉及的仪器与证书全量数据 |

`status` 取值：

- `UNIQUE_CHAIN`：唯一有效链，校验通过；
- `MULTIPLE_CHAINS`：存在多条有效链，冲突节点给出分叉证书；
- `BROKEN_CHAIN`：无完整有效链（区间断裂/父证书过期/死端/环/签名错误/不确定度不合规）；
- `NO_CERTIFICATE`：该时点仪器没有有效证书；
- `CONFLICT`：有唯一有效链，但同时存在断裂的备选分支。

## 快速体验（演示数据）

容器启动后，以下示例对应 `app/seed.py` 中预置的 6 种场景，统一查询时点 `2026-09-01`：

```bash
# 1) 唯一溯源链（数字多用表 → 实验室标准器 → 国家直流电压基准）
curl "http://localhost:8000/trace?serial_no=MFR-2026-001&point_in_time=2026-09-01"

# 2) 多候选链：证书同时声明主/备两个父标准器，定位 FORK_MULTIPLE_PARENTS 分叉点
curl "http://localhost:8000/trace?serial_no=MFR-2026-002&point_in_time=2026-09-01"

# 3) 区间断裂：子证书生效时父证书已过期（INTERVAL_GAP / EDGE_PARENT_EXPIRED）
curl "http://localhost:8000/trace?serial_no=MFR-2026-003&point_in_time=2026-09-01"

# 4) 溯源死端：父级不是国家基准且未继续溯源（DEAD_END）
curl "http://localhost:8000/trace?serial_no=MFR-2026-004&point_in_time=2026-09-01"

# 5) 签名摘要被篡改（SIGNATURE_MISMATCH，含 stored/expected 摘要）
curl "http://localhost:8000/report?serial_no=MFR-2026-005&point_in_time=2026-09-01"

# 6) 子证书不确定度小于父证书（UNCERTAINTY_NOT_MONOTONIC）
curl "http://localhost:8000/report?serial_no=MFR-2026-006&point_in_time=2026-09-01"

# 7) 导出完整 JSON（含涉及仪器、证书及校验报告）
curl "http://localhost:8000/export?serial_no=MFR-2026-001&point_in_time=2026-09-01"
```

## 自定义数据提交流程

1. 登记国家基准仪器（`kind=NATIONAL_STANDARD`）与各级标准器、工作器具；
2. 先提交基准证书（无 `parent_cert_no`），再自上而下提交子证书；
3. `signature_digest` 留空即可由服务端计算；实验室线下签名时，应对同一**规范报文**取 SHA-256：

   ```
   SHA256( canonical JSON {
     cert_no, instrument_serial_no, calibration_date, valid_from, valid_until,
     measured_value, uncertainty, unit, parent_cert_no,
     alt_parent_cert_nos(排序), issued_by
   } )
   ```

   规则见 `app/digest.py`（固定键序、无空白、日期 ISO 格式、备选父证书编号排序）；
4. 一张证书除主父证书外，可用 `alt_parent_cert_nos` 声明备选溯源关系（用于并行比对/多链冲突演示）；
5. 调用 `/trace`、`/report`、`/export` 获取链路、报告与导出。

### 提交证书示例

```bash
curl -X POST "http://localhost:8000/certificates" \
  -H "Content-Type: application/json" \
  -d '{
    "cert_no": "CAL-2026-0100",
    "serial_no": "MFR-2026-001",
    "calibration_date": "2026-09-10",
    "valid_from": "2026-09-15",
    "valid_until": "2027-09-15",
    "measured_value": 10.00002,
    "uncertainty": 0.0001,
    "unit": "V",
    "parent_cert_no": "C-REF-001",
    "alt_parent_cert_nos": [],
    "issued_by": "某某校准实验室"
  }'
```

## 校验规则说明

- **节点检查 `node_checks`**：`in_interval`（时点在有效期内）、`signature_digest_ok`（摘要一致）；
- **父子边检查 `edge_checks`**：
  - `parent_in_interval`：父证书时点有效；
  - `interval_continuous`：子证书 `valid_from` ∈ 父证书 `[valid_from, valid_until]`；
  - `uncertainty_monotonic`：子不确定度 ≥ 父不确定度（溯源计量学传递要求）；
  - `units_compatible`：计量单位一致；
  - `calibration_order_ok`：校准日期先后次序（警告级）；
- 问题分为 `error` / `warning` 两级；任一 error 存在时该候选链标记为无效；
- 多候选链时 `conflicts` 返回 `FORK_MULTIPLE_PARENTS`（含分叉深度与各分支头部证书）或 `MULTIPLE_ACTIVE_CERTIFICATES`（同一时点多张并行证书）。
