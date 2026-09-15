# Chip Tray Inspection API

封装线芯片托盘错位检测后端。视觉设备上传一批芯片中心检测点后，服务在容差
`t` 内求解**总曼哈顿距离最小的一对一完美匹配**，避免相邻穴位容差区重叠时
按上传顺序就近配对造成的误判。

- 纯后端：Python 3.11+ / FastAPI / Pydantic v2
- 匹配引擎为仓库内自实现的匈牙利算法（O(n³)），不依赖任何科学计算库
- 结果完全确定：只取决于批次内容，与文件内数组顺序无关

## 目录结构

```
app/
  main.py        # FastAPI 入口、路由、统一错误封装、1 MiB 限制
  models.py      # Pydantic 请求模型（严格校验）
  matching.py    # 匈牙利算法 + 并列裁决编码
tests/
  test_matching.py  # 匹配最优性 / 并列裁决 / 容差边界
  test_api.py       # 接口契约、校验错误、字节级重排一致性
verify/
  verify.py      # 一次性验收服务（对运行中的 API 做黑盒检查）
Dockerfile
docker-compose.yml
requirements.txt      # 运行依赖
requirements-dev.txt  # 测试依赖
```

## 快速开始

### 本地运行

```bash
pip install -r requirements-dev.txt
uvicorn app.main:app --host 0.0.0.0 --port 8000
```

### Docker Compose（仅运行 API）

```bash
docker compose up --build          # 默认监听宿主机 8000 端口
API_PORT=9000 docker compose up    # 通过 API_PORT 覆盖宿主机端口
```

## 上传约束与调用示例

- 接口：`POST /api/v1/inspect`，`multipart/form-data`，单个文件字段名 `file`
- 文件必须是 UTF-8 编码的 JSON，**大小不得超过 1 MiB（1048576 字节）**
- 坐标 `x`、`y`：JSON 整数，范围 `0..10000`（浮点、数字字符串、布尔值一律拒绝）
- `tolerance`：JSON 整数，范围 `0..500`，配对要求曼哈顿距离 **≤ t**
- 每个点的 `id` 为非空字符串，**整个批次内唯一**（两类点合并计算）
- 每类点最多 **80** 个；不允许任何未定义字段（顶层或点对象内）
- 违反任一约束时整个请求被拒绝（见下文错误表），绝不返回部分结果

调用示例：

```bash
cat > payload.json <<'JSON'
{
  "batch_id": "BATCH-20260915-001",
  "tolerance": 10,
  "sockets": [
    {"id": "S1", "x": 0,  "y": 0},
    {"id": "S2", "x": 10, "y": 0}
  ],
  "detections": [
    {"id": "D1", "x": 9, "y": 0},
    {"id": "D2", "x": 0, "y": 0}
  ]
}
JSON

curl -sS -F "file=@payload.json;type=application/json" \
  http://localhost:8000/api/v1/inspect
```

### 请求字段

| 字段 | 类型 | 约束 | 说明 |
| --- | --- | --- | --- |
| `batch_id` | string | 1–128 字符 | 唯一批次号（由上传方保证唯一性） |
| `tolerance` | int | 0–500 | 容差 t，距离 ≤ t 才允许配对 |
| `sockets` | array | ≤ 80 个 | 期望穴位点 |
| `detections` | array | ≤ 80 个 | 视觉检测点 |
| 点对象 `id` | string | 1–64 字符，批内唯一 | 点编号 |
| 点对象 `x` / `y` | int | 0–10000 | 整数坐标 |

## 响应

### 成功（HTTP 200）

```json
{
  "status": "PASS",
  "batch_id": "BATCH-20260915-001",
  "min_total_cost": 1,
  "pairs": [
    {"socket_id": "S1", "detection_id": "D2"},
    {"socket_id": "S2", "detection_id": "D1"}
  ]
}
```

`pairs` 为完整配对，**按穴位编号升序排列**；将文件内数组重新排序后重新
上传，响应体字节级一致。

### 业务失败（HTTP 200，不泄露任何部分匹配）

```json
{"status": "COUNT_MISMATCH", "batch_id": "...", "socket_count": 2, "detection_count": 3}
```

```json
{"status": "POSITION_MISMATCH", "batch_id": "..."}
```

- `COUNT_MISMATCH`：两类点数量不同，直接返回，不做匹配。
- `POSITION_MISMATCH`：数量相等但不存在满足容差的一对一完美匹配。

### 请求错误（整体拒绝）

统一错误信封：

```json
{"error": {"code": "VALIDATION_ERROR", "message": "...", "details": [{"loc": ["sockets", 0, "x"], "type": "...", "msg": "..."}]}}
```

| HTTP | `error.code` | 触发条件 |
| --- | --- | --- |
| 400 | `VALIDATION_ERROR` | 重复编号、非整数坐标、越界值、未知字段、缺字段、超过 80 个点等 |
| 400 | `INVALID_JSON` | 文件不是合法的 UTF-8 JSON |
| 413 | `FILE_TOO_LARGE` | 文件超过 1 MiB |
| 500 | `INTERNAL_ERROR` | 未预期的服务端错误 |

## 匹配与并列裁决规则

1. 距离一律为曼哈顿距离 `|x1-x2| + |y1-y2|`，仅当距离 ≤ `tolerance` 时
   两点允许配对。
2. 在所有满足容差的一对一完美匹配中，取**总距离最小**者。
3. 若最小总代价存在多个完美匹配：先将穴位按编号升序排列，再取对应
   **检测点编号序列字典序最小**的匹配（编号按字符串字典序比较）。
4. 求解前对两类点分别按编号排序，因此结果不依赖上传顺序。

实现上，裁决规则被精确编码进每条边的整数代价（总距离为主键、检测点编号
序列按位编码为次键），用一次匈牙利算法即可同时得到最优性与唯一裁决，
无需枚举并列解。

## 测试

```bash
pip install -r requirements-dev.txt
pytest
```

`tests/test_matching.py` 分别覆盖：

- **匹配最优性**：与全排列暴力解在数百个随机实例上对拍，并包含
  “按上传顺序贪心病判”的重叠容差区用例；
- **并列裁决**：等代价多解时选取字典序最小的检测点序列，且打乱输入
  顺序结果不变；
- **容差边界**：距离恰好等于 `t` 可配对、大 1 即失败、`t = 0` 时要求
  坐标完全重合。

`tests/test_api.py` 覆盖接口契约、各类校验错误、1 MiB 边界以及重排后
响应字节级一致。

## 一次性验收服务 verify

Compose 默认只启动 API。`verify` 服务等待 API 健康后执行一组黑盒验收
（最优匹配、并列裁决、容差边界、数量不匹配、各类非法输入、超限文件、
重排字节级一致），全部通过则以退出码 0 结束：

```bash
docker compose --profile verify up --build --abort-on-container-exit --exit-code-from verify
```

也可对任意运行中的实例直接执行：

```bash
API_BASE_URL=http://localhost:8000 python -m verify.verify
```

## 环境变量

| 变量 | 默认 | 说明 |
| --- | --- | --- |
| `API_PORT` | `8000` | 宿主机暴露端口（`docker compose` 端口映射左侧） |
| `API_BASE_URL` | `http://localhost:8000` | verify 脚本直连模式下的 API 地址 |
