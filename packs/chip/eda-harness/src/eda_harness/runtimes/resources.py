"""Admission across projects sharing a Docker daemon; retained until cleanup is confirmed."""

import os
import sqlite3
from contextlib import closing
from pathlib import Path


def connect():
    root = Path(os.environ.get("EDA_RESOURCE_STATE_DIR", Path.home() / ".cache/eda-harness"))
    root.mkdir(parents=True, exist_ok=True)
    db = sqlite3.connect(root / "resources.sqlite", timeout=5)
    db.execute("CREATE TABLE IF NOT EXISTS leases (token TEXT PRIMARY KEY, daemon TEXT, owner INTEGER, cpu INTEGER, memory REAL)")
    return db


def acquire(token, resource, tool_identity):
    if tool_identity.get("kind") != "docker":
        return False
    maximum = int(os.environ.get("EDA_MAX_CONCURRENT_ACTIONS", "1"))
    if maximum < 1:
        raise ValueError("EDA_MAX_CONCURRENT_ACTIONS must be a positive integer")
    daemon = tool_identity.get("daemon_id", "default")
    capacity_cpu = tool_identity.get("daemon_cpu")
    capacity_memory = tool_identity.get("daemon_memory_gb")
    with closing(connect()) as db, db:
        db.execute("BEGIN IMMEDIATE")
        leases = db.execute("SELECT token, cpu, memory FROM leases WHERE daemon = ?", (daemon,)).fetchall()
        if len(leases) >= maximum:
            raise ValueError("EDA_RESOURCE_BUSY: another managed EDA action is using this Docker daemon; inspect or recover its run before retrying")
        if capacity_cpu and sum(r[1] for r in leases) + resource.cpu > capacity_cpu:
            raise ValueError("EDA_RESOURCE_CPU: requested CPU allocation exceeds the Docker daemon capacity")
        if capacity_memory and sum(r[2] for r in leases) + resource.memory_gb > capacity_memory - 0.5:
            raise ValueError("EDA_RESOURCE_MEMORY: requested RAM exceeds Docker daemon RAM minus 0.5 GiB reserve; increasing --memory alone cannot increase Docker VM RAM")
        db.execute("INSERT INTO leases VALUES (?, ?, ?, ?, ?)", (token, daemon, os.getpid(), resource.cpu, resource.memory_gb))
    return True


def release(token):
    with closing(connect()) as db, db:
        db.execute("DELETE FROM leases WHERE token = ?", (token,))
