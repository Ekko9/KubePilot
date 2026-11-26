#!/usr/bin/env bash
set -eo pipefail

ROOT_DIR="$(cd "$(dirname "$0")/.." && pwd)"
OUTPUT="$ROOT_DIR/values-private.yaml"

if [[ -z "$DB_PASSWORD" ]]; then
  echo "请通过 DB_PASSWORD 环境变量传入数据库密码" >&2
  exit 1
fi

if [[ -z "$ADMIN_PASSWORD" ]]; then
  ADMIN_PASSWORD="$(openssl rand -base64 24 | tr -d '\n')"
fi
SECRET_KEY="$(openssl rand -hex 32)"
FERNET_KEY="$(openssl rand -base64 32 | tr '+/' '-_' | tr -d '\n')"

umask 077
DB_PASSWORD="$DB_PASSWORD" ADMIN_PASSWORD="$ADMIN_PASSWORD" SECRET_KEY="$SECRET_KEY" FERNET_KEY="$FERNET_KEY" \
python3 - "$OUTPUT" <<'PY'
import json
import os
import sys

output = sys.argv[1]
with open(output, "w", encoding="utf-8") as handle:
    handle.write("database:\n")
    handle.write("  password: " + json.dumps(os.environ["DB_PASSWORD"], ensure_ascii=False) + "\n\n")
    handle.write("security:\n")
    handle.write("  secretKey: " + json.dumps(os.environ["SECRET_KEY"]) + "\n")
    handle.write("  encryptionKey: " + json.dumps(os.environ["FERNET_KEY"]) + "\n")
    handle.write("  adminPassword: " + json.dumps(os.environ["ADMIN_PASSWORD"]) + "\n")
PY

echo "已生成 $OUTPUT（权限 600）"
echo "初始管理员：admin"
echo "初始密码已写入 values-private.yaml，请妥善保管。"

