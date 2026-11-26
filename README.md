# KubePilot DevOps v1.0.0

KubePilot DevOps 是面向 Kubernetes 集群的轻量化自动发布与运维平台，提供资源监控、Kubernetes 资源管理、镜像构建、滚动发布与回滚、告警通知、人员和值班管理、操作审计等能力。平台通过 Helm 部署到 Kubernetes，后端使用 Python/FastAPI，持久化数据存储在 MySQL。

人员资料支持维护中文名、登录用户名、邮箱和手机号；用户使用用户名登录，页面、操作记录和值班信息优先显示中文名，并保留登录用户名便于审计追溯。值班日程按月展示轮班覆盖，并在当天详情中提供值班人员手机号和直接拨号入口。

详细业务能力请阅读 [docs/FUNCTIONS.md](docs/FUNCTIONS.md)。

## 1. 交付物

```text
devops/
├── app/                         # Python 后端与前端静态资源
├── chart/devops-platform/       # Helm Chart 源码
├── dist/                        # 本地离线交付物（不提交 Git）
│   ├── kubepilot-devops-v1.0.0.tar.gz
│   └── devops-platform-1.0.0.tgz
├── docs/FUNCTIONS.md            # 详细功能说明
├── scripts/                     # 配置生成、安装、导入镜像等脚本
├── sql/init.sql                 # MySQL 5.7.22+/8.0 初始化结构
├── tests/                       # 自动化测试
├── Dockerfile
├── requirements.txt
├── values-private.example.yaml  # 私密配置模板，不含真实密码
└── README.md
```

平台显示版本和镜像标签为 `v1.0.0`；Helm Chart 包版本遵循 SemVer，使用 `1.0.0`。

## 1.1 新手一键部署

如果你不熟悉 Kubernetes、Helm 或 YAML，推荐使用交互式向导。它会检查集群连接，询问数据库和管理员信息，自动生成随机密钥，检查 Chart 并完成安装或升级：

```bash
export PROJECT_DIR=/path/to/kubepilot
cd "$PROJECT_DIR"
chmod +x scripts/quick-install.sh
bash scripts/quick-install.sh
```

需要重新生成配置时使用 `./scripts/quick-install.sh --force-config`；已有配置默认只复用，不会覆盖。

按提示填写即可：

1. 数据库地址、端口、用户名、数据库名和密码。
2. 管理员邮箱及初始密码。密码至少 8 位，必须包含大写字母、小写字母、数字和特殊字符；直接回车可自动生成。
3. 平台镜像地址和标签。在线集群直接使用默认值；私有 Harbor 按实际地址修改。
4. 如果集群不能匿名拉取 Harbor 镜像，选择创建拉取 Secret，并填写 Harbor 账号。

部署完成后脚本会显示 Pod 状态和访问地址。默认访问地址为 `http://<任一节点IP>:30088`。以后再次执行脚本会复用已有的 `values-private.yaml` 并执行升级，不会覆盖数据库密码、加密密钥或管理员密码。

脚本只负责平台部署，不会安装或替换 `kubectl`、Helm、Docker，也不会自动创建 MySQL 数据库账号。首次部署前请确认 `kubectl get nodes` 正常，数据库已创建且平台 Pod 能访问数据库。

## 2. 系统架构

```text
浏览器 -> Service/Ingress -> KubePilot Deployment -> MySQL 5.7.22+/8.0
                              │
                              ├─ Kubernetes API：资源、发布、构建、监控
                              └─ 通知网关：邮件/飞书/钉钉/企业微信/电话

每个 Kubernetes 节点：devops-platform-monitor DaemonSet
                      └─ CPU、内存、指定挂载路径磁盘使用率
```

平台 Pod 使用 ServiceAccount 访问 Kubernetes API。节点监控使用 DaemonSet，不依赖 metrics-server；若集群具备 metrics-server，Pod 资源指标也可由 Kubernetes Metrics API 提供。

## 3. 环境要求

- Kubernetes 1.24 或更高版本。
- `kubectl` 已配置并能访问目标集群。
- Helm 3 已安装；本项目不会安装或替换 Helm。
- MySQL 5.7.22+ 或 8.0，平台 Pod 到数据库地址和端口网络可达。初始化 SQL 使用两者通用的 `utf8mb4_unicode_ci` 排序规则。
- 构建镜像时，构建 Pod 能访问 Git、基础镜像仓库和目标私有镜像仓库。
- 在线部署需要集群能拉取你自己的私有仓库镜像（例如 `registry.example.com/team/kubepilot-devops:v1.0.0`），或提前创建私有仓库 Secret。
- 离线部署需要将镜像导入每个可能调度平台 Deployment/DaemonSet 的节点。
- 建议至少预留 2 CPU、2 GiB 内存；实际 requests/limits 可在 values 中调整。

## 4. 数据库初始化

`sql/init.sql` 与当前数据模型一致，兼容 MySQL 5.7.22+/8.0 和 UTF8MB4。使用具备建库建表权限的账号执行：

```bash
mysql -h <数据库地址> -P <端口> -u <用户名> -p < sql/init.sql
```

脚本默认创建 `kubepilot_devops`。平台启动时仍会执行幂等的建表检查；生产环境建议由 DBA 预先执行 SQL 并留档。初始化 SQL 不含真实密码、管理员明文密码或通知密钥；平台首次启动会根据 Helm 私密配置创建管理员和内置通知模板。管理员后续创建的新用户会标记为“待首次改密”，用户完成改密前不能访问业务接口。初始密码、管理员重置密码和用户首次登录设置的新密码均须至少 8 位，并同时包含大写字母、小写字母、数字和特殊字符（空格不算特殊字符）。已有数据库升级时，平台会自动增加首次改密状态字段，历史账号保持可正常登录。

首次初始化的 `admin` 是系统保护管理员：允许更新邮箱和密码，但不能删除、停用或取消管理员权限。平台提供测试、研发、运维、管理员四种角色；管理员可在“系统管理 → 权限管理”按用户覆盖角色默认菜单。权限管理页面、权限目录和权限修改接口仅管理员可访问，管理员账号固定拥有全部权限。操作审计仅管理员可见，并支持单条删除、勾选批量删除和清空全部；所有清理操作本身也会继续留下一条审计记录。

建议创建独立应用账号而非长期使用 root：

```sql
CREATE USER 'kubepilot'@'%' IDENTIFIED BY '<强密码>';
GRANT SELECT, INSERT, UPDATE, DELETE, CREATE, ALTER, INDEX
  ON kubepilot_devops.* TO 'kubepilot'@'%';
FLUSH PRIVILEGES;
```

## 5. 生成私密配置

不要把密码和密钥写入公开 values，也不要提交 `values-private.yaml`：

```bash
export PROJECT_DIR=/path/to/kubepilot
cd "$PROJECT_DIR"
export DB_PASSWORD='<数据库密码>'
export ADMIN_PASSWORD='<初始管理员密码>'   # 省略时自动生成
./scripts/generate-values.sh
chmod 600 values-private.yaml
```

根据环境补充完整配置：

```yaml
database:
  host: db.example.internal
  port: 3306
  user: kubepilot
  name: kubepilot_devops
  password: "REPLACE_ME"

security:
  secretKey: "REPLACE_WITH_RANDOM_SECRET"
  encryptionKey: "REPLACE_WITH_FERNET_KEY"
  adminUsername: admin
  adminPassword: "REPLACE_ME"
  adminEmail: admin@example.com

image:
  repository: registry.example.com/team/kubepilot-devops
  tag: v1.0.0
  pullPolicy: IfNotPresent

imagePullSecrets:
  - name: registry-pull-secret

app:
  displayTimezone: Asia/Shanghai
```

`encryptionKey` 用于加密 Git、Harbor、邮件和 Webhook 敏感配置，投入使用后不能随意更换。数据库备份与该密钥必须配套保管。

创建私有仓库拉取 Secret：

```bash
kubectl -n devops-platform create secret docker-registry registry-pull-secret \
  --docker-server=registry.example.com \
  --docker-username='<Registry 用户>' \
  --docker-password='<Registry 密码或令牌>'
```

## 6. 在线部署

部署前检查：

```bash
helm lint chart/devops-platform -f values-private.yaml
helm template devops-platform chart/devops-platform \
  -n devops-platform -f values-private.yaml > /tmp/devops-rendered.yaml
```

安装或升级：

```bash
export PROJECT_DIR=/path/to/kubepilot
cd "$PROJECT_DIR"
NAMESPACE=devops-platform \
RELEASE=devops-platform \
VALUES_FILE="$PWD/values-private.yaml" \
./scripts/install.sh
```

也可使用打包 Chart：

```bash
helm upgrade --install devops-platform ./dist/devops-platform-1.0.0.tgz \
  --namespace devops-platform --create-namespace \
  --values ./values-private.yaml --wait --timeout 10m
```

检查状态：

```bash
kubectl -n devops-platform get deployment,pods,svc,daemonset
kubectl -n devops-platform rollout status deployment/devops-platform --timeout=5m
kubectl -n devops-platform logs deployment/devops-platform --tail=200
```

## 7. 离线镜像部署

离线镜像位于 `dist/kubepilot-devops-v1.0.0.tar.gz`。复制到所有可能运行平台 Pod 和监控 DaemonSet 的 Kubernetes 节点，然后执行：

```bash
./scripts/import-image.sh ./dist/kubepilot-devops-v1.0.0.tar.gz
```

脚本按 nerdctl、ctr、Docker 的顺序自动选择可用运行时，并将 containerd 镜像导入 `k8s.io` namespace。也可以手工执行：

```bash
gzip -dc kubepilot-devops-v1.0.0.tar.gz | ctr -n k8s.io images import -
```

离线 values 建议使用 `IfNotPresent`。导入后的镜像名称必须与 `image.repository:image.tag` 完全一致。

## 8. 服务访问与 Ingress

默认 Service 是 NodePort `30088`，访问 `http://<可达节点IP>:30088`。平台容器监听 8080 不会修改任何业务应用端口；发布功能只更新业务 Deployment 镜像。

生产建议启用 TLS Ingress：

```yaml
ingress:
  enabled: true
  className: nginx
  hosts:
    - host: devops.example.com
      paths:
        - path: /
          pathType: Prefix
  tls:
    - secretName: devops-platform-tls
      hosts: [devops.example.com]
```

## 9. 关键 Helm 参数

| 参数 | 默认值 | 说明 |
|---|---:|---|
| `replicaCount` | `1` | 平台副本数 |
| `image.repository` | `kubepilot-devops` | 平台镜像仓库 |
| `image.tag` | `v1.0.0` | 平台镜像版本 |
| `imagePullSecrets` | `[]` | 私有仓库拉取 Secret |
| `service.nodePort` | `30088` | 默认节点端口 |
| `database.*` | - | MySQL 连接参数 |
| `security.*` | - | 登录、签名、敏感信息加密配置 |
| `app.pollIntervalSeconds` | `30` | 监控/告警轮询间隔 |
| `app.corsOrigins` | `*` | CORS 来源，生产应收敛 |
| `diskMonitor.enabled` | `true` | 节点监控 DaemonSet 开关 |
| `diskMonitor.paths` | `/` | 节点磁盘采集路径 |
| `kaniko.image` | Kaniko v1.24 | 镜像构建器 |
| `kaniko.registryInsecure` | `true` | 是否允许不安全 Registry；生产建议关闭 |
| `resources` | 见 values | 平台 Pod requests/limits |

## 10. 镜像构建配置

每次执行创建独立 Kubernetes Job：init container 拉取代码，Kaniko 根据 Dockerfile 构建、缓存并推送镜像，平台实时采集状态和日志。

- Git 支持 HTTP/HTTPS 账号密码，也支持 SSH Secret。
- Harbor 账号密码用于推送构建产物；平台 Pod 拉取自身镜像使用 `imagePullSecrets`，两者不同。
- `Git ref` 可以是分支、Tag 或可检出的提交引用。
- 构建目录相对仓库根目录；Dockerfile 路径相对构建目录。
- 若文件是 `repo/devops/Dockerfile`，常规配置为构建目录 `devops`、Dockerfile `Dockerfile`。
- 若构建上下文为仓库根目录，则配置 `.` 和 `devops/Dockerfile`。
- 构建参数对应 Dockerfile `ARG`，不是运行时环境变量。
- 目标镜像需包含仓库、项目、名称和 Tag。

每次构建会新建 Pod；合理的 Dockerfile 分层和 Kaniko 远端缓存可避免完整重复下载依赖。超过保留数量或手工删除历史时，会同步清理关联 Job/Pod。

## 11. 发布与资源管理

发布管理只修改 Deployment 指定容器的镜像，不修改端口、资源限制、探针、环境变量或卷。这些配置应先在资源管理中编辑。

发布时选择命名空间、Deployment 和镜像，实时查看 rollout、事件和 Pod 日志。发布历史记录操作人、镜像、状态和 Revision。回滚使用下拉框选择可用 Revision，并同时显示镜像和创建时间。

资源管理支持 Pod、Deployment、StatefulSet、DaemonSet、Service、Ingress、ConfigMap、Secret、PVC、Job、CronJob。保存前记录旧 YAML，可比较差异和回退历史版本；Pod 额外支持实时日志。

## 12. 服务器监控、告警和通知

节点 DaemonSet 读取宿主机 `/proc` 和挂载路径，直接采集 CPU、内存和磁盘，不依赖 metrics-server。使用者可在服务器监控页面维护挂载目录。

告警支持 Pod/节点 CPU、内存、磁盘、Pod 重启和状态等指标。节点规则不关联命名空间；Pod 非 Running 会排除 Job 正常完成的 Succeeded/Completed Pod。持续告警按 `repeat_interval_minutes` 重复通知。

通知支持邮件、飞书、钉钉、企业微信和电话网关。邮件收件人、电话号码均可按界面提示使用逗号、分号或换行分隔。推荐配置顺序：人员 → 通知模板 → 通知渠道 → 规则模板 → 告警规则/订阅 → 测试发送。

## 13. 升级、回滚和卸载

升级前备份数据库和私密配置：

```bash
mysqldump -h <host> -P <port> -u <user> -p \
  --single-transaction kubepilot_devops > kubepilot_devops-backup.sql
cp values-private.yaml values-private.yaml.backup
```

```bash
helm upgrade devops-platform ./dist/devops-platform-1.0.0.tgz \
  -n devops-platform -f values-private.yaml --wait --timeout 10m
helm history devops-platform -n devops-platform
helm rollback devops-platform <REVISION> -n devops-platform --wait
helm uninstall devops-platform -n devops-platform
```

卸载不会删除外部 MySQL 数据库。删除 namespace 会删除平台 Secret、ConfigMap 和构建资源，执行前必须确认影响。

## 14. 部署验证

```bash
kubectl -n devops-platform get pods -o wide
kubectl -n devops-platform get svc devops-platform
kubectl -n devops-platform get ds devops-platform-monitor
curl -fsS http://<节点IP>:30088/healthz
curl -fsS http://<节点IP>:30088/readyz
```

`healthz` 验证进程存活，`readyz` 同时验证数据库和 Kubernetes API 依赖。上线后按 `docs/FUNCTIONS.md` 的验收清单检查登录、资源、构建、发布、回滚和通知。

需要在验收环境查看多人排班、故障流转和通知记录的完整展示效果时，可在已配置数据库连接的源码目录执行：

```bash
python scripts/seed_demo_data.py
```

该脚本可重复执行，只会重建带 `[演示]` / `demo:` 标识的数据，不会修改真实业务记录和构建配置。生产环境不需要演示数据时，不要执行该脚本。

## 15. 常见故障

### ImagePullBackOff

检查镜像名称/Tag、离线镜像是否导入、拉取 Secret 是否存在：

```bash
kubectl -n devops-platform describe pod <pod>
kubectl -n devops-platform get secret harbor-pull-secret
```

### readyz 失败或无法登录

```bash
kubectl -n devops-platform logs deployment/devops-platform --tail=300
nc -vz <数据库地址> <端口>
```

核对数据库网络、账号权限和数据库名，不要把真实密码粘贴到公共日志。

### 构建无日志或失败

```bash
kubectl -n devops-platform get jobs,pods
kubectl -n devops-platform describe job <build-job>
kubectl -n devops-platform logs job/<build-job> --all-containers --tail=300
```

检查 Git URL/ref、认证、构建目录、Dockerfile 相对路径、基础镜像、Harbor 推送权限和 Kaniko 缓存权限。

### 节点指标无数据

```bash
kubectl -n devops-platform get ds devops-platform-monitor
kubectl -n devops-platform logs ds/devops-platform-monitor --tail=200
kubectl -n devops-platform get configmap devops-platform-monitor -o yaml
```

### 发布未完成

```bash
kubectl -n <业务命名空间> rollout status deployment/<应用> --timeout=5m
kubectl -n <业务命名空间> describe deployment <应用>
kubectl -n <业务命名空间> get events --sort-by=.lastTimestamp
```

平台不会替业务应用修改端口、探针或资源限制；需要时先在资源管理修正配置。

## 16. 安全建议

- 数据库使用独立最小权限账号并限制来源网段。
- 生产关闭 `registryInsecure`，为 Harbor/Git 使用可信 TLS。
- 收敛 `corsOrigins`，使用 Ingress TLS。
- 定期轮换管理员、Git、Harbor、SMTP 和电话网关凭据。
- 不提交 `values-private.yaml`、`.env`、镜像归档和数据库备份。
- 数据库与 `encryptionKey` 配套备份。
- 定期检查操作审计、失败通知和遗留构建 Job。

