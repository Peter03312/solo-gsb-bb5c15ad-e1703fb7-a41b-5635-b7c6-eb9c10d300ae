# 演出无线频点协调服务

为临时演出的无线电设备组协调频点。录入场次可用频点、设备组使用时段与组间干扰关系，
服务在满足全部约束的前提下求解频点分配，并以不可变版本的形式发布、查询与对比。

## 功能与规则

- **录入**：每个场次维护一份可用整数频点列表、设备组使用时段（左闭右开 `[start, end)`）、
  组间干扰关系（每条给出正整数最小频点间距）。
- **约束**：时段相交的干扰组，其频点间距不得小于给定值；同一设备组全程使用同一频点。
- **锁定**：发布时可锁定部分组的频点（必须引用已知组且在可用频点内）。
- **优化目标**：先满足全部约束；再最小化新旧版共有组的改频数（首次规划计零）；
  仍并列时按组编号升序比较频点序列，取字典序最小方案。
- **无解**：返回 422，说明涉及的锁定组与致冲突的干扰关系，当前发布版不变。
- **版本**：发布须提交所见版本号 `base_version`；并发发布只有一个成功，
  旧版本请求返回 409。各发布版不可变，可读取分配结果及与前版的差异。
- **校验**：时段起点不早于终点、引用未知组、锁定不可用频点、间距非正整数等，
  均整体拒绝该次操作（400）。

## 启动

```bash
docker compose up --build
```

- 服务地址：http://localhost:8000
- 交互式接口文档：http://localhost:8000/docs
- PostgreSQL：容器内 `db:5432`（数据保存在 `pgdata` 卷中）

Compose 会启动 PostgreSQL 并等待健康检查通过后启动应用；应用启动时自动建表。

## 配置

| 环境变量 | 默认值 | 说明 |
|---|---|---|
| `DATABASE_URL` | `postgresql+psycopg2://postgres:postgres@localhost:5432/freqcoord` | SQLAlchemy 连接串；Compose 中已指向 `db` 服务 |

本地开发（无 Docker）：

```bash
pip install -r requirements.txt
DATABASE_URL=sqlite:///./dev.db uvicorn app.main:app --reload --port 8000
```

运行测试：

```bash
pip install pytest httpx
DATABASE_URL=sqlite:////tmp/test.db python -m pytest tests/ -q
```

## API 一览

| 方法 | 路径 | 说明 |
|---|---|---|
| POST | `/sessions` | 创建场次，录入可用频点 |
| GET | `/sessions/{id}` | 查看场次当前状态与版本号 |
| PUT | `/sessions/{id}/groups` | 整体替换设备组及时段 |
| PUT | `/sessions/{id}/interferences` | 整体替换干扰关系 |
| POST | `/sessions/{id}/publish` | 求解并发布新版本（带 `base_version` 与可选 `locks`） |
| GET | `/sessions/{id}/versions` | 版本列表 |
| GET | `/sessions/{id}/versions/{v}` | 读取某版分配结果与锁定时快照 |
| GET | `/sessions/{id}/versions/{v}/diff` | 该版与前一版的差异 |

### 请求示例

```bash
# 1. 创建场次
curl -X POST http://localhost:8000/sessions -H 'Content-Type: application/json' -d '{
  "name": "周五露天晚会",
  "frequencies": [101, 102, 103, 104, 105, 106, 107, 108]
}'

# 2. 录入设备组时段（左闭右开）
curl -X PUT http://localhost:8000/sessions/1/groups -H 'Content-Type: application/json' -d '{
  "groups": [
    {"id": "stage", "start": 0, "end": 120},
    {"id": "host",  "start": 30, "end": 90}
  ]
}'

# 3. 录入干扰关系
curl -X PUT http://localhost:8000/sessions/1/interferences -H 'Content-Type: application/json' -d '{
  "interferences": [{"a": "stage", "b": "host", "min_separation": 3}]
}'

# 4. 发布（base_version 为当前所见版本，首次为 0；locks 可选）
curl -X POST http://localhost:8000/sessions/1/publish -H 'Content-Type: application/json' -d '{
  "base_version": 0,
  "locks": {"stage": 105}
}'
# → 201 {"version": 1, "assignments": {"stage": 105, "host": 101}, "changes": 0}

# 5. 读取版本与差异
curl http://localhost:8000/sessions/1/versions/1
curl http://localhost:8000/sessions/1/versions/1/diff

# 6. 用过期版本号发布 → 409
curl -X POST http://localhost:8000/sessions/1/publish -H 'Content-Type: application/json' -d '{
  "base_version": 0
}'
```

更多示例见 `examples/requests.sh`（`bash examples/requests.sh` 一键跑通全流程）。

### 错误码

| 状态码 | 含义 |
|---|---|
| 400 | 校验失败：起点不早于终点、引用未知组、锁定不可用频点、间距非正整数等，整次操作被拒绝 |
| 404 | 场次或版本不存在 |
| 409 | 发布版本冲突：`base_version` 已过期，响应中给出 `current_version` |
| 422 | 无解：响应中列出涉及的锁定组 `locked_groups` 与致冲突的干扰关系 `interferences`，当前发布版不变 |

## 实现说明

- **求解器**（`app/solver.py`）：纯 Python 精确求解。第一阶段按改频预算递增做
  带前向检查与分支限界的搜索，得到最少改频数；第二阶段在该预算下按组编号升序
  逐组固定最小可行频点，得到字典序最小方案。无解时通过贪心删除提取一组
  致冲突的干扰关系用于说明。
- **并发发布**：发布请求对场次行加 `SELECT ... FOR UPDATE` 写锁串行化，
  再比对 `base_version`；`(session_id, version)` 唯一约束作为最后防线，
  保证并发发布只有一个成功。
- **不可变性**：发布版本写入 `plan_versions` 表后只读，差异由相邻两版分配结果计算得出。
