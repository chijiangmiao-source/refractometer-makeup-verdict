# 磨削槽液浓度换算 API

折光仪只给出读数，本服务把读数换算成真实质量浓度，并告诉工艺员**该补原液、补水还是保持不动**，给出可复算的补液升数。

纯后端 HTTP API：Python 3.12 + FastAPI + Pydantic + pytest，全程 `Decimal` 精确计算。

## 计算公式（与运行方法）

入参（均为十进制字符串）：

| 字段 | 含义 | 约束 |
| --- | --- | --- |
| `R` | 折光读数 | `0 ≤ R ≤ 30` |
| `F` | 折光系数 | `0 < F ≤ 10` |
| `V` | 槽液体积（升） | `0 < V ≤ 50000` |
| `S` | 原液质量浓度百分数 | `0 < S ≤ 100` |
| `L` | 目标浓度区间下限 | `0 ≤ L ≤ U ≤ S` |
| `U` | 目标浓度区间上限 | 同上 |

且 `R×F ≤ S`（当前浓度不可能高于原液浓度）。

```
当前浓度      C = R × F
目标中点      T = (L + U) / 2

L ≤ C ≤ U  →  保持不动，补液量 0
C < L      →  补原液  x = V × (T - C) / (S - T)   （分母 S−T=0 时返回 422）
C > U      →  补水    y = V × (C - T) / T         （分母 T=0 时返回 422）
```

- 中间计算全程使用 `Decimal` **未舍入值**（上下文 60 位有效数字）。
- 响应中 `C`、`T`、补液后复核浓度 `verify_concentration` 保留 **4 位小数**；补液升数保留 **3 位小数**。
- 所有量化统一 `ROUND_HALF_UP`（四舍五入）。
- 复核浓度用**未舍入**补液量代回：补液后 `(V·C + x·S)/(V + x)`，补水后 `V·C/(V + y)`，恰好回到目标中点。

### 运行方法

```bash
# 1) 启动服务（宿主端口可用 API_PORT 覆盖，默认 8000）
docker compose up --build api
API_PORT=9090 docker compose up --build api      # 改用 9090 端口

# 2) 本地（无 Docker 时）
python3 -m venv .venv && . .venv/bin/activate
pip install -r requirements.txt
uvicorn app.main:app --host 0.0.0.0 --port 8000

# 3) 一次性验收（启动 api 健康检查通过后跑黑盒 HTTP 验收，跑完即退出）
docker compose build verify
docker compose run --rm verify
```

## 接口

`POST /api/concentration/adjust`

### 低浓度 → 补原液（可复算）

```bash
curl -s http://localhost:8000/api/concentration/adjust \
  -H 'Content-Type: application/json' \
  -d '{"R":"5","F":"1","V":"1000","S":"50","L":"8","U":"12"}'
```

手算：`C=5×1=5`，`T=(8+12)/2=10`，`x=1000×(10−5)/(50−10)=125.000` 升。

```json
{
  "action": "add_concentrate",
  "action_label": "补原液",
  "C": "5.0000",
  "T": "10.0000",
  "concentrate_liters": "125.000",
  "water_liters": "0.000",
  "verify_concentration": "10.0000",
  "message": "当前浓度 5.0000 低于区间下限 8.0000，需补入原液 125.000 升，补液后复核浓度 10.0000。"
}
```

### 高浓度 → 补水（可复算）

```bash
curl -s http://localhost:8000/api/concentration/adjust \
  -H 'Content-Type: application/json' \
  -d '{"R":"15","F":"1","V":"1000","S":"50","L":"8","U":"12"}'
```

手算：`C=15`，`y=1000×(15−10)/10=500.000` 升。

```json
{
  "action": "add_water",
  "action_label": "补水",
  "C": "15.0000",
  "T": "10.0000",
  "concentrate_liters": "0.000",
  "water_liters": "500.000",
  "verify_concentration": "10.0000",
  "message": "当前浓度 15.0000 高于区间上限 12.0000，需补入纯水 500.000 升，补水后复核浓度 10.0000。"
}
```

### 恰好压线 → 明确无需调整

`R=8` 时 `C=8` 恰为闭区间下限（区间端点同样视为合格）：

```json
{
  "action": "hold",
  "action_label": "保持不动",
  "C": "8.0000",
  "T": "10.0000",
  "concentrate_liters": "0.000",
  "water_liters": "0.000",
  "verify_concentration": "8.0000",
  "message": "当前浓度 8.0000 位于目标区间 [8.0000, 12.0000] 内（含边界），无需补原液或补水，保持不动，补液量为零。"
}
```

### 分母为零 → 422

目标区间为 `[0,0]` 且当前浓度大于 0 时，补水公式分母 `T=0`：

```bash
curl -i http://localhost:8000/api/concentration/adjust \
  -H 'Content-Type: application/json' \
  -d '{"R":"1","F":"1","V":"1000","S":"10","L":"0","U":"0"}'
# HTTP/1.1 422
# {"detail":"补水公式分母 T 为零（目标中点为 0），无法通过补入有限体积水达到目标，返回 422。"}
```

### 字段错误 → 422 且指出原因

```json
{
  "detail": [
    {
      "field": "F",
      "reason": "F（折光系数） 超出允许范围（必须满足 0 < F ≤ 10），当前为 0"
    }
  ]
}
```

跨字段错误（如 `R×F > S`、`L > U`、`U > S`）同样 422，并给出具体越界数值。

## 测试

```bash
pip install -r requirements.txt
python -m pytest                         # 31 项单元/HTTP 测试
BASE_URL=http://localhost:8000 python -m pytest tests/test_acceptance_http.py
```

`docker compose run --rm verify` 即等价于第二条（容器网络内 `BASE_URL=http://api:8000`），对真实运行中的服务做黑盒验收。

## 目录结构

```
app/main.py        FastAPI 路由与 422 错误处理
app/schemas.py     十进制字符串解析与全部约束校验
app/service.py     Decimal 计算（补液/补水/保持、复核、ROUND_HALF_UP）
tests/             pytest 单元测试与在线验收测试
Dockerfile         python:3.12-slim
docker-compose.yml api 服务（API_PORT 覆盖宿主端口）+ verify 一次性验收服务
```
