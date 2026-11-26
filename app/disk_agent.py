from __future__ import annotations

import json
import logging
import os
import shutil
import time

from kubernetes import client, config
from kubernetes.client import ApiException


logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
logger = logging.getLogger("monitor-agent")
NODE_NAME = os.environ["NODE_NAME"]
NAMESPACE = os.getenv("POD_NAMESPACE", "devops-platform")
CONFIG_MAP = os.getenv("DISK_CONFIG_MAP", "devops-platform-monitor")
INTERVAL_SECONDS = max(int(os.getenv("DISK_POLL_INTERVAL_SECONDS", "30")), 10)
_previous_cpu: tuple[int, int] | None = None


def monitored_paths(api: client.CoreV1Api) -> list[str]:
    config_map = api.read_namespaced_config_map(CONFIG_MAP, NAMESPACE)
    raw = (config_map.data or {}).get("paths", "/")
    result = []
    for value in raw.replace(",", "\n").splitlines():
        path = value.strip()
        if path and path.startswith("/") and path not in result:
            result.append(path.rstrip("/") or "/")
    return result or ["/"]


def collect(paths: list[str]) -> list[dict]:
    result = []
    for path in paths:
        host_path = "/host" if path == "/" else "/host" + path
        try:
            usage = shutil.disk_usage(host_path)
            result.append({
                "path": path,
                "total": usage.total,
                "used": usage.used,
                "free": usage.free,
                "percent": round(usage.used / usage.total * 100, 2) if usage.total else 0,
            })
        except OSError as exc:
            logger.warning("cannot inspect %s: %s", path, exc)
    return result


def collect_cpu_memory() -> dict:
    """Read host counters directly so node monitoring does not depend on metrics-server."""
    global _previous_cpu
    cpu_percent = 0.0
    try:
        fields = open("/host/proc/stat", encoding="utf-8").readline().split()[1:]
        values = [int(value) for value in fields[:8]]
        idle = values[3] + (values[4] if len(values) > 4 else 0)
        total = sum(values)
        if _previous_cpu:
            previous_total, previous_idle = _previous_cpu
            total_delta = total - previous_total
            idle_delta = idle - previous_idle
            if total_delta > 0:
                cpu_percent = max(0.0, min(100.0, (total_delta - idle_delta) / total_delta * 100))
        _previous_cpu = (total, idle)
    except (OSError, ValueError, IndexError):
        logger.exception("cannot inspect host cpu")
    memory = {"total": 0, "available": 0}
    try:
        for line in open("/host/proc/meminfo", encoding="utf-8"):
            key, raw = line.split(":", 1)
            if key in {"MemTotal", "MemAvailable"}:
                memory[key.lower().replace("mem", "")] = int(raw.split()[0]) * 1024
    except (OSError, ValueError, IndexError):
        logger.exception("cannot inspect host memory")
    total = memory["total"]
    available = memory["available"]
    used = max(total - available, 0)
    return {
        "cpu_percent": round(cpu_percent, 2),
        "memory_total": total,
        "memory_used": used,
        "memory_percent": round(used / total * 100, 2) if total else 0,
        "timestamp": int(time.time()),
    }


def main() -> None:
    config.load_incluster_config()
    api = client.CoreV1Api()
    while True:
        try:
            usage = collect(monitored_paths(api))
            metrics = collect_cpu_memory()
            metrics["disk_mounts"] = usage
            api.patch_node(NODE_NAME, {
                "metadata": {"annotations": {
                    "kubepilot.io/disk-usage": json.dumps(usage, separators=(",", ":")),
                    "kubepilot.io/node-metrics": json.dumps(metrics, separators=(",", ":")),
                }}
            })
            logger.info("updated %s disk usage for %d paths", NODE_NAME, len(usage))
        except ApiException:
            logger.exception("kubernetes API request failed")
        except Exception:
            logger.exception("disk collection failed")
        time.sleep(INTERVAL_SECONDS)


if __name__ == "__main__":
    main()
