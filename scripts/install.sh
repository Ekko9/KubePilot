#!/usr/bin/env bash
set -eo pipefail

ROOT_DIR="$(cd "$(dirname "$0")/.." && pwd)"
if [[ -z "$NAMESPACE" ]]; then NAMESPACE="devops-platform"; fi
if [[ -z "$RELEASE" ]]; then RELEASE="devops-platform"; fi
if [[ -z "$VALUES_FILE" ]]; then VALUES_FILE="$ROOT_DIR/values-private.yaml"; fi

if [[ ! -f "$VALUES_FILE" ]]; then
  echo "缺少私密配置文件：$VALUES_FILE，请先运行 scripts/generate-values.sh" >&2
  exit 1
fi

if ! command -v kubectl >/dev/null 2>&1; then
  echo "未找到 kubectl，请先安装并配置集群访问权限" >&2
  exit 1
fi

HELM_BIN="$(command -v helm 2>/dev/null || true)"
if [[ -z "$HELM_BIN" && -x "$ROOT_DIR/bin/helm" ]]; then HELM_BIN="$ROOT_DIR/bin/helm"; fi
if [[ -z "$HELM_BIN" ]]; then
  echo "未找到 helm，请安装 Helm 3 或将二进制放到 bin/helm" >&2
  exit 1
fi

"$HELM_BIN" upgrade --install "$RELEASE" "$ROOT_DIR/chart/devops-platform" \
  --namespace "$NAMESPACE" \
  --create-namespace \
  --values "$VALUES_FILE" \
  --wait --timeout 10m

kubectl -n "$NAMESPACE" get deployment,pods,svc,daemonset
echo "验证命令：kubectl -n $NAMESPACE get pods"
echo "默认 NodePort：30088（以 Helm values 配置为准）"

