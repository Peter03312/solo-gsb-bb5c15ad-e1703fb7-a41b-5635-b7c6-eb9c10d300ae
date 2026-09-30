#!/usr/bin/env bash
# 频点协调服务请求示例（先启动：docker compose up --build）
set -e
BASE=${BASE:-http://localhost:8000}

echo "== 1. 创建场次，录入可用频点 =="
curl -s -X POST $BASE/sessions -H 'Content-Type: application/json' -d '{
  "name": "周五露天晚会",
  "frequencies": [101, 102, 103, 104, 105, 106, 107, 108]
}'
echo

SID=1  # 上一步返回的场次 id

echo "== 2. 录入设备组使用时段（左闭右开，单位自定，如分钟） =="
curl -s -X PUT $BASE/sessions/$SID/groups -H 'Content-Type: application/json' -d '{
  "groups": [
    {"id": "stage",   "start": 0,   "end": 120},
    {"id": "host",    "start": 30,  "end": 90},
    {"id": "lighting","start": 60,  "end": 150},
    {"id": "press",   "start": 150, "end": 180}
  ]
}'
echo

echo "== 3. 录入干扰关系（正整数最小频点间距） =="
curl -s -X PUT $BASE/sessions/$SID/interferences -H 'Content-Type: application/json' -d '{
  "interferences": [
    {"a": "stage", "b": "host",    "min_separation": 3},
    {"a": "stage", "b": "lighting","min_separation": 2},
    {"a": "host",  "b": "press",   "min_separation": 4}
  ]
}'
echo

echo "== 4. 发布第 1 版（base_version 为当前所见版本；可锁定部分组） =="
curl -s -X POST $BASE/sessions/$SID/publish -H 'Content-Type: application/json' -d '{
  "base_version": 0,
  "locks": {"stage": 105}
}'
echo

echo "== 5. 读取发布版本与差异 =="
curl -s $BASE/sessions/$SID/versions/1
echo
curl -s $BASE/sessions/$SID/versions/1/diff
echo

echo "== 6. 基于旧版本号再次发布 → 409 冲突 =="
curl -s -X POST $BASE/sessions/$SID/publish -H 'Content-Type: application/json' -d '{
  "base_version": 0
}'
echo

echo "== 7. 无解示例：锁定与干扰关系冲突 → 422，当前发布版不变 =="
curl -s -X POST $BASE/sessions/$SID/publish -H 'Content-Type: application/json' -d '{
  "base_version": 1,
  "locks": {"stage": 101, "host": 102}
}'
echo
