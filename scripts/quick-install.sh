#!/usr/bin/env bash
set -Eeuo pipefail

# One-command installer for first-time Kubernetes users.
ROOT_DIR="$(cd "$(dirname "$0")/.." && pwd)"
CHART_DIR="$ROOT_DIR/chart/devops-platform"
VALUES_FILE="${VALUES_FILE:-$ROOT_DIR/values-private.yaml}"
NAMESPACE="${NAMESPACE:-devops-platform}"
RELEASE="${RELEASE:-devops-platform}"
FORCE_CONFIG="${FORCE_CONFIG:-0}"

die() { echo "错误：$*" >&2; exit 1; }
say() { echo; echo "==> $*"; }

usage() {
  cat <<'EOF'
KubePilot DevOps 一键部署

用法：
  ./scripts/quick-install.sh                  交互式首次安装或升级
  ./scripts/quick-install.sh --help           显示帮助
  ./scripts/quick-install.sh --force-config   忽略已有 values，重新生成配置

可选环境变量：
  NAMESPACE=devops-platform RELEASE=devops-platform
  VALUES_FILE=/path/to/values-private.yaml
EOF
}

for argument in "$@"; do
  case "$argument" in
    --help|-h) usage; exit 0 ;;
    --force-config) FORCE_CONFIG=1 ;;
    *) die "不支持的参数：$argument。使用 --help 查看用法。" ;;
  esac
done

command -v kubectl >/dev/null 2>&1 || die "未找到 kubectl。请先安装 kubectl，并确认 kubectl get nodes 可以正常返回。"
command -v helm >/dev/null 2>&1 || die "未找到 Helm 3。请先安装 Helm；本脚本不会自动安装 Helm。"
command -v python3 >/dev/null 2>&1 || die "未找到 Python 3。它用于安全生成配置文件，请先安装 Python 3。"
command -v openssl >/dev/null 2>&1 || die "未找到 OpenSSL。它用于生成平台加密密钥，请先安装 OpenSSL。"
kubectl cluster-info >/dev/null 2>&1 || die "kubectl 无法连接 Kubernetes 集群，请先配置 kubeconfig。"
helm version >/dev/null 2>&1 || die "Helm 无法正常运行，请检查 Helm 安装。"

strong_password() {
  local value="$1"
  [[ "${#value}" -ge 8 ]] || return 1
  [[ "$value" =~ [A-Z] ]] || return 1
  [[ "$value" =~ [a-z] ]] || return 1
  [[ "$value" =~ [0-9] ]] || return 1
  [[ "$value" =~ [^[:alnum:][:space:]] ]] || return 1
}

prompt() {
  local label="$1" default="${2:-}" answer
  if [[ -n "$default" ]]; then
    read -r -p "$label [$default]: " answer
    printf '%s' "${answer:-$default}"
  else
    read -r -p "$label: " answer
    printf '%s' "$answer"
  fi
}

secret_prompt() {
  local label="$1" answer
  read -r -s -p "$label: " answer
  echo >&2
  printf '%s' "$answer"
}

write_values() {
  local db_host="$1" db_port="$2" db_user="$3" db_name="$4" db_password="$5"
  local admin_username="$6" admin_email="$7" admin_password="$8"
  local image_repository="$9" image_tag="${10}" pull_secret="${11}"
  local secret_key encryption_key
  secret_key="$(openssl rand -hex 32 2>/dev/null || true)"
  encryption_key="$(openssl rand -base64 32 2>/dev/null | tr '+/' '-_' | tr -d '\n' || true)"
  [[ -n "$secret_key" && -n "$encryption_key" ]] || die "未找到 openssl，无法安全生成平台密钥。"

  umask 077
  DB_HOST="$db_host" DB_PORT="$db_port" DB_USER="$db_user" DB_NAME="$db_name" \
  DB_PASSWORD="$db_password" ADMIN_USERNAME="$admin_username" ADMIN_EMAIL="$admin_email" \
  ADMIN_PASSWORD="$admin_password" IMAGE_REPOSITORY="$image_repository" IMAGE_TAG="$image_tag" \
  SECRET_KEY="$secret_key" ENCRYPTION_KEY="$encryption_key" PULL_SECRET="$pull_secret" \
  python3 - "$VALUES_FILE" <<'PY'
import json
import os
import sys

output = sys.argv[1]
quote = lambda value: json.dumps(value, ensure_ascii=False)
with open(output, "w", encoding="utf-8") as handle:
    handle.write("database:\n")
    handle.write(f"  host: {quote(os.environ['DB_HOST'])}\n")
    handle.write(f"  port: {int(os.environ['DB_PORT'])}\n")
    handle.write(f"  user: {quote(os.environ['DB_USER'])}\n")
    handle.write(f"  name: {quote(os.environ['DB_NAME'])}\n")
    handle.write(f"  password: {quote(os.environ['DB_PASSWORD'])}\n\n")
    handle.write("security:\n")
    handle.write(f"  secretKey: {quote(os.environ['SECRET_KEY'])}\n")
    handle.write(f"  encryptionKey: {quote(os.environ['ENCRYPTION_KEY'])}\n")
    handle.write(f"  adminUsername: {quote(os.environ['ADMIN_USERNAME'])}\n")
    handle.write(f"  adminPassword: {quote(os.environ['ADMIN_PASSWORD'])}\n")
    handle.write(f"  adminEmail: {quote(os.environ['ADMIN_EMAIL'])}\n\n")
    handle.write("image:\n")
    handle.write(f"  repository: {quote(os.environ['IMAGE_REPOSITORY'])}\n")
    handle.write(f"  tag: {quote(os.environ['IMAGE_TAG'])}\n")
    handle.write("  pullPolicy: IfNotPresent\n")
    if os.environ["PULL_SECRET"]:
        handle.write("\nimagePullSecrets:\n  - name: " + quote(os.environ["PULL_SECRET"]) + "\n")
PY
  chmod 600 "$VALUES_FILE"
}

create_pull_secret() {
  local secret_name="$1" registry="$2" username password
  [[ -n "$secret_name" ]] || return 0
  username="$(prompt "Harbor 用户名")"
  password="$(secret_prompt "Harbor 密码或机器人令牌")"
  [[ -n "$username" && -n "$password" ]] || die "Harbor 用户名和密码不能为空。"
  kubectl -n "$NAMESPACE" create secret docker-registry "$secret_name" \
    --docker-server="$registry" --docker-username="$username" --docker-password="$password" \
    --dry-run=client -o yaml | kubectl apply -f - >/dev/null
  echo "已创建/更新镜像拉取 Secret：$secret_name"
}

say "KubePilot DevOps 一键部署"
echo "集群：$(kubectl config current-context 2>/dev/null || echo 未知)"
echo "命名空间：$NAMESPACE    Helm Release：$RELEASE"

if [[ -f "$VALUES_FILE" && "${FORCE_CONFIG:-0}" != "1" ]]; then
  echo "发现已有配置：$VALUES_FILE"
  read -r -p "直接复用该配置并升级？[Y/n]: " reuse
  reuse="${reuse:-Y}"
  if [[ "$reuse" =~ ^[Yy]$ ]]; then
    say "复用现有配置"
  else
    die "已取消。需要重新生成配置时，请先备份并删除 values-private.yaml，或设置 FORCE_CONFIG=1。"
  fi
else
  say "填写数据库和管理员配置"
  DB_HOST="$(prompt "数据库地址" "127.0.0.1")"
  DB_PORT="$(prompt "数据库端口" "3306")"
  DB_USER="$(prompt "数据库用户名" "kubepilot")"
  DB_NAME="$(prompt "数据库名" "kubepilot_devops")"
  [[ "$DB_PORT" =~ ^[0-9]+$ ]] || die "数据库端口必须是数字。"
  DB_PASSWORD="$(secret_prompt "数据库密码")"
  [[ -n "$DB_PASSWORD" ]] || die "数据库密码不能为空。"
  ADMIN_USERNAME="$(prompt "管理员用户名" "admin")"
  ADMIN_EMAIL="$(prompt "管理员邮箱" "admin@example.com")"
  while true; do
    ADMIN_PASSWORD="$(secret_prompt "管理员初始密码（至少 8 位，含大小写、数字、特殊字符；直接回车自动生成）")"
    if [[ -z "$ADMIN_PASSWORD" ]]; then
      ADMIN_PASSWORD="$(openssl rand -hex 16)Aa1!"
      echo "已自动生成管理员初始密码，请妥善保存：$ADMIN_PASSWORD"
    fi
    strong_password "$ADMIN_PASSWORD" && break
    echo "密码不符合强密码要求，请重新输入。" >&2
  done
  IMAGE_REPOSITORY="$(prompt "平台镜像地址" "registry.example.com/team/kubepilot-devops")"
  IMAGE_TAG="$(prompt "平台镜像标签" "v1.0.0")"
  REGISTRY_HOST="${IMAGE_REPOSITORY%%/*}"
  PULL_SECRET=""
  read -r -p "是否创建 Harbor 镜像拉取 Secret？[y/N]: " create_secret
  if [[ "$create_secret" =~ ^[Yy]$ ]]; then
    PULL_SECRET="$(prompt "Secret 名称" "harbor-pull-secret")"
  fi
  write_values "$DB_HOST" "$DB_PORT" "$DB_USER" "$DB_NAME" "$DB_PASSWORD" \
    "$ADMIN_USERNAME" "$ADMIN_EMAIL" "$ADMIN_PASSWORD" "$IMAGE_REPOSITORY" "$IMAGE_TAG" "$PULL_SECRET"
  echo "私密配置已保存：$VALUES_FILE（权限 600）"
fi

if [[ -n "${PULL_SECRET:-}" ]]; then
  kubectl create namespace "$NAMESPACE" --dry-run=client -o yaml | kubectl apply -f - >/dev/null
  create_pull_secret "$PULL_SECRET" "$REGISTRY_HOST"
fi

say "检查并部署"
helm lint "$CHART_DIR" -f "$VALUES_FILE" >/dev/null || die "Helm 配置检查失败，请根据上面的提示修正配置。"
helm upgrade --install "$RELEASE" "$CHART_DIR" \
  --namespace "$NAMESPACE" --create-namespace --values "$VALUES_FILE" \
  --wait --timeout 10m

say "部署结果"
kubectl -n "$NAMESPACE" get deployment,pods,svc,daemonset
echo
echo "部署完成。访问方式："
echo "  http://<任一可达节点IP>:$(helm get values "$RELEASE" -n "$NAMESPACE" -o json | python3 -c 'import json,sys; print(json.load(sys.stdin).get("service",{}).get("nodePort",30088))')"
echo "查看状态：kubectl -n $NAMESPACE get pods"
echo "查看日志：kubectl -n $NAMESPACE logs deployment/$RELEASE --tail=200"
