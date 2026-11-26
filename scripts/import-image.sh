#!/usr/bin/env bash
set -eo pipefail

ROOT_DIR="$(cd "$(dirname "$0")/.." && pwd)"
ARCHIVE="$1"
if [[ -z "$ARCHIVE" ]]; then ARCHIVE="$ROOT_DIR/dist/kubepilot-devops-v1.0.0.tar.gz"; fi

if [[ ! -f "$ARCHIVE" ]]; then
  echo "镜像归档不存在：$ARCHIVE" >&2
  exit 1
fi

if command -v nerdctl >/dev/null 2>&1; then
  gzip -dc "$ARCHIVE" | nerdctl --namespace k8s.io load
elif command -v ctr >/dev/null 2>&1; then
  gzip -dc "$ARCHIVE" | ctr --namespace k8s.io images import -
elif command -v docker >/dev/null 2>&1; then
  gzip -dc "$ARCHIVE" | docker load
else
  echo "未找到 nerdctl、ctr 或 docker" >&2
  exit 1
fi

