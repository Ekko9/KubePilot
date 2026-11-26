from __future__ import annotations

import json
import base64
from datetime import datetime, timezone
import shlex
from typing import Any

from kubernetes import client, config
from kubernetes.client import ApiException
from kubernetes.dynamic import DynamicClient
import yaml

from .config import get_settings
from .schemas import BuildRequest, DeployRequest


settings = get_settings()

EDITABLE_RESOURCE_TYPES = {
    ("v1", "Pod"),
    ("apps/v1", "Deployment"),
    ("apps/v1", "StatefulSet"),
    ("apps/v1", "DaemonSet"),
    ("v1", "Service"),
    ("networking.k8s.io/v1", "Ingress"),
    ("v1", "ConfigMap"),
    ("v1", "Secret"),
    ("v1", "PersistentVolumeClaim"),
    ("batch/v1", "Job"),
    ("batch/v1", "CronJob"),
}


def parse_cpu(value: str | None) -> float:
    if not value:
        return 0.0
    value = str(value)
    if value.endswith("n"):
        return float(value[:-1]) / 1_000_000_000
    if value.endswith("u"):
        return float(value[:-1]) / 1_000_000
    if value.endswith("m"):
        return float(value[:-1]) / 1000
    return float(value)


def parse_memory(value: str | None) -> float:
    if not value:
        return 0.0
    value = str(value)
    units = {
        "Ki": 1024, "Mi": 1024**2, "Gi": 1024**3, "Ti": 1024**4,
        "K": 1000, "M": 1000**2, "G": 1000**3, "T": 1000**4,
    }
    for unit, factor in units.items():
        if value.endswith(unit):
            return float(value[:-len(unit)]) * factor
    return float(value)


class KubernetesService:
    def __init__(self) -> None:
        self._loaded = False

    def load(self) -> None:
        if self._loaded:
            return
        try:
            config.load_incluster_config()
        except config.ConfigException:
            config.load_kube_config(config_file=settings.kubeconfig_path or None)
        self.core = client.CoreV1Api()
        self.apps = client.AppsV1Api()
        self.batch = client.BatchV1Api()
        self.custom = client.CustomObjectsApi()
        self.api_client = client.ApiClient()
        self.dynamic = DynamicClient(self.api_client)
        self._loaded = True

    @staticmethod
    def _clean_manifest(manifest: dict[str, Any]) -> dict[str, Any]:
        manifest = dict(manifest)
        manifest.pop("status", None)
        metadata = dict(manifest.get("metadata") or {})
        for key in ("managedFields", "creationTimestamp", "generation", "selfLink", "uid"):
            metadata.pop(key, None)
        manifest["metadata"] = metadata
        return manifest

    def _dynamic_resource(self, api_version: str, kind: str):
        self.load()
        if (api_version, kind) not in EDITABLE_RESOURCE_TYPES:
            raise ValueError("该资源类型不在平台允许管理的范围内")
        return self.dynamic.resources.get(api_version=api_version, kind=kind)

    def list_namespaced_resource_types(self) -> list[dict[str, Any]]:
        self.load()
        discovered = {}
        # The dynamic client search also returns ``ResourceList`` objects. They
        # do not have a ``namespaced`` field, and their dynamic attribute
        # fallback can raise while filtering. Read the concrete resource
        # attribute directly so discovery remains safe across client versions.
        for resource in self.dynamic.resources.search():
            if not resource.__dict__.get("namespaced", False):
                continue
            verbs = set(resource.verbs or [])
            if not {"get", "list"}.issubset(verbs) or "/" in (resource.name or ""):
                continue
            if (resource.group_version, resource.kind) not in EDITABLE_RESOURCE_TYPES:
                continue
            key = (resource.group or "", resource.kind)
            item = {
                "api_version": resource.group_version,
                "kind": resource.kind,
                "plural": resource.name,
                "editable": bool({"update", "patch"} & verbs),
                "preferred": bool(resource.preferred),
            }
            if key not in discovered or item["preferred"]:
                discovered[key] = item
        result = list(discovered.values())
        for item in result:
            item.pop("preferred", None)
        priority = {"Pod": 0, "Deployment": 1, "StatefulSet": 2, "DaemonSet": 3, "Service": 4,
                    "Ingress": 5, "ConfigMap": 6, "Secret": 7, "PersistentVolumeClaim": 8,
                    "Job": 9, "CronJob": 10}
        result.sort(key=lambda item: (priority.get(item["kind"], 100), item["kind"], item["api_version"]))
        return result

    def list_namespaced_resources(self, namespace: str, api_version: str, kind: str) -> list[dict[str, Any]]:
        resource = self._dynamic_resource(api_version, kind)
        response = self.dynamic.get(resource=resource, namespace=namespace)
        result = []
        for item in response.items:
            data = item.to_dict()
            metadata = data.get("metadata") or {}
            status = data.get("status") or {}
            summary = status.get("phase") or status.get("state") or ""
            if kind in {"Deployment", "StatefulSet"}:
                summary = f"{status.get('readyReplicas', 0)}/{(data.get('spec') or {}).get('replicas', 0)} Ready"
            elif kind == "DaemonSet":
                summary = f"{status.get('numberReady', 0)}/{status.get('desiredNumberScheduled', 0)} Ready"
            elif kind == "Service":
                spec = data.get("spec") or {}
                summary = f"{spec.get('type', 'ClusterIP')} · {spec.get('clusterIP', '')}"
            elif not summary:
                conditions = status.get("conditions") or []
                ready = next((condition for condition in reversed(conditions) if condition.get("status") == "True"), None)
                summary = (ready or {}).get("type", "配置资源")
            result.append({
                "name": metadata.get("name", ""), "namespace": metadata.get("namespace", namespace),
                "api_version": api_version, "kind": kind, "summary": summary,
                "created_at": metadata.get("creationTimestamp", ""),
                "labels": metadata.get("labels") or {},
            })
        return result

    def get_namespaced_resource(self, namespace: str, api_version: str, kind: str, name: str) -> dict[str, Any]:
        resource = self._dynamic_resource(api_version, kind)
        item = self.dynamic.get(resource=resource, namespace=namespace, name=name)
        manifest = self._clean_manifest(item.to_dict())
        return {
            "manifest": manifest,
            "content": yaml.safe_dump(manifest, allow_unicode=True, sort_keys=False),
            "editable": bool({"update", "patch"} & set(resource.verbs or [])),
        }

    def replace_namespaced_resource(
        self, namespace: str, api_version: str, kind: str, name: str, manifest: dict[str, Any]
    ) -> tuple[dict[str, Any], dict[str, Any]]:
        resource = self._dynamic_resource(api_version, kind)
        if "update" not in set(resource.verbs or []):
            raise ValueError("该资源类型不支持更新")
        current = self.dynamic.get(resource=resource, namespace=namespace, name=name).to_dict()
        current_clean = self._clean_manifest(current)
        manifest = self._clean_manifest(manifest)
        metadata = manifest.setdefault("metadata", {})
        if manifest.get("apiVersion") != api_version or manifest.get("kind") != kind:
            raise ValueError("apiVersion 或 kind 不能修改")
        if metadata.get("name") != name or metadata.get("namespace", namespace) != namespace:
            raise ValueError("资源名称和命名空间不能修改")
        metadata["namespace"] = namespace
        metadata["resourceVersion"] = (current.get("metadata") or {}).get("resourceVersion")
        updated = self.dynamic.replace(
            resource=resource, namespace=namespace, name=name, body=manifest,
        ).to_dict()
        return current_clean, self._clean_manifest(updated)

    def _metrics(self, plural: str, namespace: str | None = None) -> dict[str, Any]:
        self.load()
        try:
            if namespace:
                return self.custom.list_namespaced_custom_object(
                    "metrics.k8s.io", "v1beta1", namespace, plural
                )
            return self.custom.list_cluster_custom_object("metrics.k8s.io", "v1beta1", plural)
        except ApiException:
            return {"items": []}

    def list_namespaces(self) -> list[str]:
        self.load()
        return sorted(item.metadata.name for item in self.core.list_namespace().items)

    def get_disk_monitor_paths(self) -> list[str]:
        self.load()
        try:
            item = self.core.read_namespaced_config_map(
                settings.disk_config_map, settings.build_namespace,
            )
            raw = (item.data or {}).get("paths", "/")
        except ApiException as exc:
            if exc.status != 404:
                raise
            raw = "/"
        return [path.strip() for path in raw.replace(",", "\n").splitlines() if path.strip()]

    def set_disk_monitor_paths(self, paths: list[str]) -> list[str]:
        self.load()
        name = settings.disk_config_map
        body = client.V1ConfigMap(
            metadata=client.V1ObjectMeta(name=name, namespace=settings.build_namespace),
            data={"paths": "\n".join(paths)},
        )
        try:
            self.core.replace_namespaced_config_map(name, settings.build_namespace, body)
        except ApiException as exc:
            if exc.status != 404:
                raise
            self.core.create_namespaced_config_map(settings.build_namespace, body)
        return paths

    def set_build_secret(self, namespace: str, name: str, values: dict[str, str]) -> str:
        self.load()
        secret = client.V1Secret(
            metadata=client.V1ObjectMeta(
                name=name,
                namespace=namespace,
                labels={"app.kubernetes.io/managed-by": "kubepilot", "kubepilot.io/build-credential": "true"},
            ),
            type="Opaque",
            data={key: base64.b64encode(value.encode("utf-8")).decode("ascii") for key, value in values.items()},
        )
        try:
            current = self.core.read_namespaced_secret(name, namespace)
            labels = current.metadata.labels or {}
            if labels.get("kubepilot.io/build-credential") != "true":
                raise ValueError(f"Secret {namespace}/{name} 已存在且不归平台管理")
            secret.metadata.resource_version = current.metadata.resource_version
            self.core.replace_namespaced_secret(name, namespace, secret)
        except ApiException as exc:
            if exc.status != 404:
                raise
            self.core.create_namespaced_secret(namespace, secret)
        return name

    def delete_build_secret(self, namespace: str, name: str) -> None:
        self.load()
        try:
            current = self.core.read_namespaced_secret(name, namespace)
            labels = current.metadata.labels or {}
            if labels.get("kubepilot.io/build-credential") != "true":
                return
            self.core.delete_namespaced_secret(name, namespace)
        except ApiException as exc:
            if exc.status != 404:
                raise

    @staticmethod
    def build_secret_names(config_id: int) -> tuple[str, str]:
        return f"build-config-{config_id}-git", f"build-config-{config_id}-registry"

    def configure_build_credentials(
        self,
        namespace: str,
        config_id: int,
        *,
        registry_server: str,
        git_username: str = "",
        git_password: str = "",
        registry_username: str = "",
        registry_password: str = "",
        clear_git: bool = False,
        clear_registry: bool = False,
    ) -> tuple[str | None, str | None]:
        git_name, registry_name = self.build_secret_names(config_id)
        git_secret = registry_secret = None
        if git_username and git_password:
            git_secret = self.set_build_secret(namespace, git_name, {
                "username": git_username,
                "password": git_password,
            })
        elif clear_git:
            self.delete_build_secret(namespace, git_name)
        if registry_username and registry_password:
            auth = base64.b64encode(f"{registry_username}:{registry_password}".encode("utf-8")).decode("ascii")
            config_json = json.dumps({"auths": {registry_server: {"auth": auth}}}, separators=(",", ":"))
            registry_secret = self.set_build_secret(namespace, registry_name, {"config.json": config_json})
        elif clear_registry:
            self.delete_build_secret(namespace, registry_name)
        return git_secret, registry_secret

    def list_nodes(self) -> list[dict[str, Any]]:
        self.load()
        metrics = {x["metadata"]["name"]: x for x in self._metrics("nodes").get("items", [])}
        result = []
        for node in self.core.list_node().items:
            name = node.metadata.name
            alloc = node.status.allocatable or {}
            capacity = node.status.capacity or {}
            usage = metrics.get(name, {}).get("usage", {})
            cpu_total = parse_cpu(alloc.get("cpu"))
            mem_total = parse_memory(alloc.get("memory"))
            cpu_used = parse_cpu(usage.get("cpu"))
            mem_used = parse_memory(usage.get("memory"))
            cpu_percent = cpu_used / cpu_total * 100 if cpu_total else 0
            disk_capacity = parse_memory(capacity.get("ephemeral-storage"))
            disk_allocatable = parse_memory(alloc.get("ephemeral-storage"))
            disk_reserved = max(disk_capacity - disk_allocatable, 0.0)
            disk_mounts = []
            agent_metrics: dict[str, Any] = {}
            try:
                annotations = node.metadata.annotations or {}
                agent_metrics = json.loads(annotations.get("kubepilot.io/node-metrics", "{}"))
                if not isinstance(agent_metrics, dict):
                    agent_metrics = {}
                disk_mounts = agent_metrics.get("disk_mounts") or json.loads(annotations.get("kubepilot.io/disk-usage", "[]"))
                if not isinstance(disk_mounts, list):
                    disk_mounts = []
            except (TypeError, ValueError):
                disk_mounts = []
                agent_metrics = {}
            agent_fresh = bool(agent_metrics.get("timestamp", 0) and (datetime.now(timezone.utc).timestamp() - float(agent_metrics["timestamp"]) < 180))
            if agent_fresh:
                cpu_percent = float(agent_metrics.get("cpu_percent") or 0)
                mem_used = float(agent_metrics.get("memory_used") or 0)
                mem_total = float(agent_metrics.get("memory_total") or mem_total)
                cpu_used = cpu_total * cpu_percent / 100 if cpu_total else 0
            ready = next((c.status for c in node.status.conditions or [] if c.type == "Ready"), "Unknown")
            result.append({
                "name": name,
                "ready": ready,
                "cpu_capacity": cpu_total,
                "cpu_used": cpu_used,
                "cpu_percent": round(cpu_percent if agent_fresh else (cpu_used / cpu_total * 100 if cpu_total else 0), 2),
                "memory_capacity": int(mem_total),
                "memory_used": int(mem_used),
                "memory_percent": round(float(agent_metrics.get("memory_percent") or 0) if agent_fresh else (mem_used / mem_total * 100 if mem_total else 0), 2),
                "disk_capacity": int(disk_capacity),
                "disk_used": int(disk_reserved),
                "disk_percent": round(disk_reserved / disk_capacity * 100, 2) if disk_capacity else 0,
                "disk_mounts": disk_mounts,
                "internal_ip": next((a.address for a in node.status.addresses or [] if a.type == "InternalIP"), ""),
                "kubelet_version": node.status.node_info.kubelet_version,
                "os_image": node.status.node_info.os_image,
            })
        return result

    def list_pods(self, namespace: str = "") -> list[dict[str, Any]]:
        self.load()
        metric_items = self._metrics("pods", namespace or None).get("items", [])
        metrics = {(m["metadata"].get("namespace", namespace), m["metadata"]["name"]): m for m in metric_items}
        pods = self.core.list_namespaced_pod(namespace).items if namespace else self.core.list_pod_for_all_namespaces().items
        result = []
        for pod in pods:
            ns, name = pod.metadata.namespace, pod.metadata.name
            pod_metric = metrics.get((ns, name), {})
            usage_by_container = {c["name"]: c.get("usage", {}) for c in pod_metric.get("containers", [])}
            cpu_used = mem_used = cpu_limit = mem_limit = 0.0
            for container in pod.spec.containers or []:
                usage = usage_by_container.get(container.name, {})
                cpu_used += parse_cpu(usage.get("cpu"))
                mem_used += parse_memory(usage.get("memory"))
                resources = container.resources or client.V1ResourceRequirements()
                limits = resources.limits or resources.requests or {}
                cpu_limit += parse_cpu(limits.get("cpu"))
                mem_limit += parse_memory(limits.get("memory"))
            statuses = pod.status.container_statuses or []
            restarts = sum(s.restart_count or 0 for s in statuses)
            ready = sum(1 for s in statuses if s.ready)
            total = len(statuses) or len(pod.spec.containers or [])
            owner_references = pod.metadata.owner_references or []
            controller = next((owner for owner in owner_references if getattr(owner, "controller", False)), None)
            owner_kind = controller.kind if controller else (owner_references[0].kind if owner_references else "")
            phase = pod.status.phase or "Unknown"
            can_restart = bool(controller) and not (
                controller.kind == "Job" and phase == "Succeeded"
            )
            result.append({
                "namespace": ns,
                "name": name,
                "phase": phase,
                "owner_kind": owner_kind,
                "can_restart": can_restart,
                "ready": f"{ready}/{total}",
                "restarts": restarts,
                "node": pod.spec.node_name or "",
                "pod_ip": pod.status.pod_ip or "",
                "cpu_used": round(cpu_used, 4),
                "cpu_percent": round(cpu_used / cpu_limit * 100, 2) if cpu_limit else 0,
                "memory_used": int(mem_used),
                "memory_percent": round(mem_used / mem_limit * 100, 2) if mem_limit else 0,
                "created_at": pod.metadata.creation_timestamp.isoformat() if pod.metadata.creation_timestamp else "",
            })
        return result

    def list_deployments(self, namespace: str = "") -> list[dict[str, Any]]:
        self.load()
        items = self.apps.list_namespaced_deployment(namespace).items if namespace else self.apps.list_deployment_for_all_namespaces().items
        service_items = (
            self.core.list_namespaced_service(namespace).items
            if namespace else self.core.list_service_for_all_namespaces().items
        )
        service_ports = {}
        for service in service_items:
            ports = service.spec.ports or []
            if ports:
                service_ports[(service.metadata.namespace, service.metadata.name)] = (
                    ports[0].target_port or ports[0].port
                )
        result = []
        for item in items:
            containers = item.spec.template.spec.containers or []
            container = containers[0] if containers else None
            resources = container.resources if container else None
            requests = (resources.requests or {}) if resources else {}
            limits = (resources.limits or {}) if resources else {}
            ports = [port.container_port for port in (container.ports or [])] if container else []
            # Do not invent a port for workloads that do not expose one. The
            # deployment form uses None to preserve this state on updates.
            service_port = service_ports.get(
                (item.metadata.namespace, item.metadata.name),
                ports[0] if ports else None,
            )
            result.append({
                "namespace": item.metadata.namespace,
                "name": item.metadata.name,
                "replicas": item.spec.replicas or 0,
                "ready_replicas": item.status.ready_replicas or 0,
                "updated_replicas": item.status.updated_replicas or 0,
                "available_replicas": item.status.available_replicas or 0,
                "image": container.image if container else "",
                "container_port": service_port,
                "ports": ports,
                "cpu_request": requests.get("cpu", "100m"),
                "memory_request": requests.get("memory", "128Mi"),
                "cpu_limit": limits.get("cpu", "1"),
                "memory_limit": limits.get("memory", "1Gi"),
                "revision": int((item.metadata.annotations or {}).get("deployment.kubernetes.io/revision", "0")),
            })
        return result

    def ensure_namespace(self, namespace: str) -> None:
        self.load()
        try:
            self.core.read_namespace(namespace)
        except ApiException as exc:
            if exc.status != 404:
                raise
            self.core.create_namespace(client.V1Namespace(metadata=client.V1ObjectMeta(name=namespace)))

    def deploy(self, request: DeployRequest) -> dict[str, Any]:
        self.load()
        deployment = self.apps.read_namespaced_deployment(request.name, request.namespace)
        containers = deployment.spec.template.spec.containers or []
        if not containers:
            raise ValueError("Deployment 没有可更新的容器")
        containers[0].image = request.image
        result = self.apps.replace_namespaced_deployment(
            request.name, request.namespace, deployment,
        )
        return {
            "action": "updated",
            "name": result.metadata.name,
            "namespace": request.namespace,
            "replicas": result.spec.replicas or 0,
        }

    def deployment_revisions(self, namespace: str, name: str) -> dict[str, Any]:
        self.load()
        deployment = self.apps.read_namespaced_deployment(name, namespace)
        match_labels = deployment.spec.selector.match_labels or {}
        selector = ",".join(f"{key}={value}" for key, value in match_labels.items())
        replica_sets = self.apps.list_namespaced_replica_set(
            namespace, label_selector=selector,
        ).items
        current_revision = int(
            (deployment.metadata.annotations or {}).get("deployment.kubernetes.io/revision", "0")
        )
        items = []
        for replica_set in replica_sets:
            revision = int(
                (replica_set.metadata.annotations or {}).get("deployment.kubernetes.io/revision", "0")
            )
            if not revision or revision == current_revision:
                continue
            containers = replica_set.spec.template.spec.containers or []
            created_at = replica_set.metadata.creation_timestamp
            items.append({
                "revision": revision,
                "image": containers[0].image if containers else "",
                "created_at": created_at.isoformat() if created_at else "",
                "replicas": replica_set.spec.replicas or 0,
                "ready_replicas": (replica_set.status.ready_replicas or 0) if replica_set.status else 0,
            })
        items.sort(key=lambda item: item["revision"], reverse=True)
        return {"current_revision": current_revision, "items": items}

    def deployment_rollout_status(
        self, namespace: str, name: str, expected_image: str = "",
    ) -> dict[str, Any]:
        self.load()
        deployment = self.apps.read_namespaced_deployment(name, namespace)
        status = deployment.status
        desired = deployment.spec.replicas or 0
        updated = (status.updated_replicas or 0) if status else 0
        ready = (status.ready_replicas or 0) if status else 0
        available = (status.available_replicas or 0) if status else 0
        unavailable = (status.unavailable_replicas or 0) if status else 0
        observed = (status.observed_generation or 0) if status else 0
        generation = deployment.metadata.generation or 0
        conditions = status.conditions or [] if status else []
        failed_condition = next(
            (
                condition for condition in conditions
                if condition.type == "Progressing"
                and condition.status == "False"
                and condition.reason == "ProgressDeadlineExceeded"
            ),
            None,
        )
        if failed_condition:
            rollout_status = "failed"
        elif observed >= generation and updated >= desired and ready >= desired and available >= desired and unavailable == 0:
            rollout_status = "succeeded"
        else:
            rollout_status = "running"

        match_labels = deployment.spec.selector.match_labels or {}
        selector = ",".join(f"{key}={value}" for key, value in match_labels.items())
        pods = self.core.list_namespaced_pod(namespace, label_selector=selector).items
        pods.sort(
            key=lambda pod: pod.metadata.creation_timestamp or datetime.min.replace(tzinfo=timezone.utc),
            reverse=True,
        )
        pod_rows = []
        relevant_uids = {deployment.metadata.uid}
        for pod in pods:
            relevant_uids.add(pod.metadata.uid)
            container_statuses = pod.status.container_statuses or []
            pod_rows.append({
                "name": pod.metadata.name,
                "phase": pod.status.phase or "Unknown",
                "ready": sum(1 for item in container_statuses if item.ready),
                "containers": len(pod.spec.containers or []),
                "restarts": sum(item.restart_count or 0 for item in container_statuses),
                "image": (pod.spec.containers or [None])[0].image if pod.spec.containers else "",
                "created_at": pod.metadata.creation_timestamp.isoformat() if pod.metadata.creation_timestamp else "",
            })

        event_rows = []
        try:
            events = self.core.list_namespaced_event(namespace).items
            for event in events:
                if event.involved_object.uid not in relevant_uids:
                    continue
                event_time = event.event_time or event.last_timestamp or event.metadata.creation_timestamp
                event_rows.append({
                    "type": event.type or "Normal",
                    "reason": event.reason or "",
                    "message": event.message or "",
                    "object": event.involved_object.name or "",
                    "created_at": event_time.isoformat() if event_time else "",
                })
            event_rows.sort(key=lambda item: item["created_at"], reverse=True)
            event_rows = event_rows[:30]
        except ApiException:
            event_rows = []

        container_logs = ""
        log_pod = next(
            (
                pod for pod in pods
                if not expected_image
                or (pod.spec.containers and pod.spec.containers[0].image == expected_image)
            ),
            pods[0] if pods else None,
        )
        if log_pod and log_pod.spec.containers:
            try:
                container_logs = self.core.read_namespaced_pod_log(
                    log_pod.metadata.name,
                    namespace,
                    container=log_pod.spec.containers[0].name,
                    tail_lines=200,
                    timestamps=True,
                )
            except ApiException:
                container_logs = ""

        current_containers = deployment.spec.template.spec.containers or []
        current_image = current_containers[0].image if current_containers else ""
        revision = int(
            (deployment.metadata.annotations or {}).get("deployment.kubernetes.io/revision", "0")
        )
        condition_rows = [{
            "type": condition.type,
            "status": condition.status,
            "reason": condition.reason or "",
            "message": condition.message or "",
        } for condition in conditions]
        return {
            "status": rollout_status,
            "namespace": namespace,
            "name": name,
            "image": current_image,
            "expected_image": expected_image,
            "revision": revision,
            "desired_replicas": desired,
            "updated_replicas": updated,
            "ready_replicas": ready,
            "available_replicas": available,
            "unavailable_replicas": unavailable,
            "conditions": condition_rows,
            "pods": pod_rows,
            "events": event_rows,
            "container_logs": container_logs,
        }

    def rollback(self, namespace: str, name: str, revision: int | None = None) -> dict[str, Any]:
        self.load()
        deployment = self.apps.read_namespaced_deployment(name, namespace)
        selector = ",".join(f"{k}={v}" for k, v in deployment.spec.selector.match_labels.items())
        replica_sets = self.apps.list_namespaced_replica_set(namespace, label_selector=selector).items
        candidates = []
        for rs in replica_sets:
            rev = int((rs.metadata.annotations or {}).get("deployment.kubernetes.io/revision", "0"))
            if rev:
                candidates.append((rev, rs))
        candidates.sort(key=lambda item: item[0], reverse=True)
        current_revision = int((deployment.metadata.annotations or {}).get("deployment.kubernetes.io/revision", "0"))
        target = None
        if revision:
            target = next((item for rev, item in candidates if rev == revision), None)
        else:
            target = next((item for rev, item in candidates if rev < current_revision), None)
        if not target:
            raise ValueError("未找到可回滚的历史版本")
        patch = {"spec": {"template": self.api_client.sanitize_for_serialization(target.spec.template)}}
        result = self.apps.patch_namespaced_deployment(name, namespace, patch)
        target_containers = target.spec.template.spec.containers or []
        return {
            "name": name,
            "namespace": namespace,
            "from_revision": current_revision,
            "target_revision": int((target.metadata.annotations or {}).get("deployment.kubernetes.io/revision", "0")),
            "target_image": target_containers[0].image if target_containers else "",
            "replicas": result.spec.replicas or 0,
            "generation": result.metadata.generation,
        }

    def scale(self, namespace: str, name: str, replicas: int) -> dict[str, Any]:
        self.load()
        result = self.apps.patch_namespaced_deployment_scale(name, namespace, {"spec": {"replicas": replicas}})
        return {"namespace": namespace, "name": name, "replicas": result.spec.replicas}

    def restart(self, namespace: str, name: str) -> dict[str, Any]:
        self.load()
        stamp = datetime.now(timezone.utc).isoformat()
        patch = {"spec": {"template": {"metadata": {"annotations": {"kubepilot/restartedAt": stamp}}}}}
        self.apps.patch_namespaced_deployment(name, namespace, patch)
        return {"namespace": namespace, "name": name, "restarted_at": stamp}

    def create_build_job(self, request: BuildRequest, build_id: int) -> str:
        self.load()
        self.ensure_namespace(request.namespace)
        job_name = f"build-{request.name}-{build_id}"
        clone_command = (
            "set -eu; rm -rf /workspace/src; "
            "if [ -n \"${GIT_USERNAME:-}\" ]; then "
            "cat > /tmp/git-askpass <<'ASKPASS'\n#!/bin/sh\n"
            "case \"$1\" in *Username*) printf '%s\\n' \"$GIT_USERNAME\";; "
            "*) printf '%s\\n' \"$GIT_PASSWORD\";; esac\nASKPASS\n"
            "chmod 700 /tmp/git-askpass; export GIT_ASKPASS=/tmp/git-askpass GIT_TERMINAL_PROMPT=0; fi; "
            f"git clone --depth 1 --branch {shlex.quote(request.git_ref)} "
            f"{shlex.quote(request.git_url)} /workspace/src"
        )
        volumes = [client.V1Volume(name="workspace", empty_dir=client.V1EmptyDirVolumeSource())]
        init_mounts = [client.V1VolumeMount(name="workspace", mount_path="/workspace")]
        kaniko_mounts = [client.V1VolumeMount(name="workspace", mount_path="/workspace")]
        if request.git_secret_name:
            volumes.append(client.V1Volume(name="git-auth", secret=client.V1SecretVolumeSource(secret_name=request.git_secret_name, default_mode=0o400)))
            init_mounts.append(client.V1VolumeMount(name="git-auth", mount_path="/root/.ssh", read_only=True))
        git_env = []
        if request.git_auth_secret_name:
            git_env = [
                client.V1EnvVar(name="GIT_USERNAME", value_from=client.V1EnvVarSource(
                    secret_key_ref=client.V1SecretKeySelector(name=request.git_auth_secret_name, key="username"),
                )),
                client.V1EnvVar(name="GIT_PASSWORD", value_from=client.V1EnvVarSource(
                    secret_key_ref=client.V1SecretKeySelector(name=request.git_auth_secret_name, key="password"),
                )),
            ]
        if request.registry_secret_name:
            volumes.append(client.V1Volume(name="registry-auth", secret=client.V1SecretVolumeSource(secret_name=request.registry_secret_name)))
            kaniko_mounts.append(client.V1VolumeMount(name="registry-auth", mount_path="/kaniko/.docker", read_only=True))
        context_path = "/workspace/src" if request.context_dir in ("", ".") else f"/workspace/src/{request.context_dir.strip('/')}"
        args = [
            f"--context=dir://{context_path}",
            f"--dockerfile={context_path}/{request.dockerfile}",
            f"--destination={request.destination}",
            "--cache=true",
            "--cache-copy-layers=true",
            "--snapshot-mode=redo",
        ]
        if settings.registry_insecure:
            args.extend(["--insecure", "--skip-tls-verify"])
        args.extend(f"--build-arg={key}={value}" for key, value in request.build_args.items())
        pod_spec = client.V1PodSpec(
            restart_policy="Never",
            service_account_name=settings.service_account_name,
            init_containers=[client.V1Container(
                name="git-clone", image=settings.git_clone_image,
                command=["/bin/sh", "-c", clone_command], volume_mounts=init_mounts, env=git_env,
            )],
            containers=[client.V1Container(
                name="kaniko", image=settings.kaniko_image, args=args, volume_mounts=kaniko_mounts,
                resources=client.V1ResourceRequirements(
                    requests={"cpu": "250m", "memory": "512Mi"},
                    limits={"cpu": "2", "memory": "4Gi"},
                ),
            )],
            volumes=volumes,
        )
        job = client.V1Job(
            metadata=client.V1ObjectMeta(name=job_name, namespace=request.namespace, labels={"kubepilot/build-id": str(build_id)}),
            spec=client.V1JobSpec(
                backoff_limit=0,
                ttl_seconds_after_finished=86400,
                active_deadline_seconds=request.timeout_seconds,
                template=client.V1PodTemplateSpec(
                    metadata=client.V1ObjectMeta(labels={"job-name": job_name, "kubepilot/build-id": str(build_id)}),
                    spec=pod_spec,
                ),
            ),
        )
        self.batch.create_namespaced_job(request.namespace, job)
        return job_name

    def build_status(self, namespace: str, job_name: str) -> dict[str, Any]:
        self.load()
        job = self.batch.read_namespaced_job(job_name, namespace)
        if job.status.succeeded:
            status = "succeeded"
        elif job.status.failed:
            status = "failed"
        elif job.status.active:
            status = "running"
        else:
            status = "pending"
        pods = self.core.list_namespaced_pod(namespace, label_selector=f"job-name={job_name}").items
        logs = ""
        if pods:
            try:
                logs = self.core.read_namespaced_pod_log(pods[0].metadata.name, namespace, container="kaniko", tail_lines=200)
            except ApiException:
                logs = ""
            if not logs:
                try:
                    logs = self.core.read_namespaced_pod_log(pods[0].metadata.name, namespace, container="git-clone", tail_lines=200)
                except ApiException:
                    logs = ""
        return {"status": status, "logs": logs}

    def delete_build_job(self, namespace: str, job_name: str) -> None:
        self.load()
        try:
            self.batch.delete_namespaced_job(job_name, namespace, propagation_policy="Foreground")
        except ApiException as exc:
            if exc.status != 404:
                raise

    def list_managed_build_jobs(self) -> list[dict[str, Any]]:
        """Return only build Jobs created by this platform.

        The build-id label is the stable ownership marker used by all versions
        of KubePilot, so this also finds orphaned Jobs from older releases.
        """
        self.load()
        result = []
        for job in self.batch.list_job_for_all_namespaces(
            label_selector="kubepilot/build-id",
        ).items:
            labels = job.metadata.labels or {}
            raw_build_id = labels.get("kubepilot/build-id", "")
            try:
                build_id = int(raw_build_id)
            except (TypeError, ValueError):
                continue
            result.append({
                "build_id": build_id,
                "namespace": job.metadata.namespace,
                "job_name": job.metadata.name,
            })
        return result

    def pod_logs(
        self, namespace: str, name: str, container: str = "",
        tail_lines: int = 500, since_seconds: int = 0,
        previous: bool = False, timestamps: bool = True,
    ) -> dict[str, Any]:
        self.load()
        pod = self.core.read_namespaced_pod(name, namespace)
        status_groups = (
            getattr(pod.status, "container_statuses", None) or [],
            getattr(pod.status, "init_container_statuses", None) or [],
            getattr(pod.status, "ephemeral_container_statuses", None) or [],
        )
        restart_counts = {
            status.name: status.restart_count or 0
            for statuses in status_groups for status in statuses
        }
        containers = []
        for kind, specs in (
            ("container", getattr(pod.spec, "containers", None) or []),
            ("init", getattr(pod.spec, "init_containers", None) or []),
            ("ephemeral", getattr(pod.spec, "ephemeral_containers", None) or []),
        ):
            containers.extend({
                "name": spec.name,
                "type": kind,
                "restart_count": restart_counts.get(spec.name, 0),
            } for spec in specs)
        if not containers:
            raise ValueError("Pod 没有可查看日志的容器")
        selected = container or containers[0]["name"]
        selected_meta = next((item for item in containers if item["name"] == selected), None)
        if not selected_meta:
            raise ValueError("指定容器不存在")
        if previous and not selected_meta["restart_count"]:
            raise ValueError("该容器没有可查看的上一实例日志")
        logs = self.core.read_namespaced_pod_log(
            name, namespace, container=selected,
            tail_lines=None if tail_lines <= 0 else max(1, min(tail_lines, 5000)),
            since_seconds=None if since_seconds <= 0 else min(since_seconds, 604800),
            previous=previous, timestamps=timestamps,
        )
        return {
            "logs": logs,
            "containers": containers,
            "selected_container": selected,
        }

    def restart_pod(self, namespace: str, name: str) -> dict[str, Any]:
        self.load()
        pod = self.core.read_namespaced_pod(name, namespace)
        if getattr(pod.metadata, "deletion_timestamp", None):
            raise ValueError("Pod 正在终止，请等待控制器完成重建")
        owners = getattr(pod.metadata, "owner_references", None) or []
        controller = next((owner for owner in owners if getattr(owner, "controller", False)), None)
        if not controller:
            raise ValueError("该 Pod 没有控制器接管，删除后无法自动重建，请在资源管理中修改配置")
        if controller.kind == "Job" and getattr(pod.status, "phase", "") == "Succeeded":
            raise ValueError("已成功完成的 Job Pod 不需要重启")
        self.core.delete_namespaced_pod(
            name, namespace,
            body=client.V1DeleteOptions(grace_period_seconds=30),
        )
        return {
            "namespace": namespace,
            "name": name,
            "controller_kind": controller.kind,
            "controller_name": controller.name,
        }


k8s = KubernetesService()

