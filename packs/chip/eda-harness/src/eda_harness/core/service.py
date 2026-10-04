import hashlib
import json
import os
import subprocess
import sys
from pathlib import Path

from eda_harness import __version__
from eda_harness.core.acceptance import evaluate, metrics_from
from eda_harness.core.context import blocking_diagnostics, execution_diagnostics
from eda_harness.core.models import ActionConfig, Artifact, DesignState, GoalState, Project
from eda_harness.core.store import Store, digest, now, uid
from eda_harness.core.workflow import action_input_categories, canonical, catalog, plan
from eda_harness.core.workspace import (
    load_project,
    materialize,
    project_lock,
    safe_path,
    scan,
    snapshot,
)
from eda_harness.plugins.tools import PLUGINS
from eda_harness.runtimes.diagnostics import diagnose
from eda_harness.runtimes.execution import Runtime, cleanup_container, identity
from eda_harness.runtimes.resources import release as release_resources

TERMINAL = {"SUCCESS", "FAILED", "TIMEOUT", "CANCELLED"}


class Harness:
    def __init__(self, root):
        self.root = Path(root).resolve()
        if not (self.root / "eda.yaml").is_file():
            raise ValueError(f"No eda.yaml in {self.root}; initialize a project first")
        self.store = Store(self.root)
        package = Path(__file__).resolve().parents[1]
        self.engine_hash = digest(
            {
                str(p.relative_to(package)): hashlib.sha256(p.read_bytes()).hexdigest()
                for directory in ("core", "plugins", "runtimes")
                for p in sorted((package / directory).glob("*.py"))
            }
        )

    def state(self, state_id=None):
        state_id = state_id or self.store.meta("current_state")
        return self.store.get("state", state_id) if state_id else None

    def _active(self):
        return [r for r in self.store.list("run") if r["status"] not in TERMINAL or r.get("cleanup_pending")]

    def _runtime(self, project, action):
        cfg = project.actions.get(action, ActionConfig())
        return cfg.runtime or project.runtime

    def _identities(self, project, actions, strict=True):
        identities, memo = {}, {}
        for action in actions:
            if action not in catalog(project.workflow):
                if strict:
                    raise ValueError(f"Action removed from workflow: {action}")
                continue
            runtime = self._runtime(project, action)
            key = (runtime.kind, runtime.image, runtime.require_native, catalog(project.workflow)[action].tool)
            try:
                if key not in memo:
                    memo[key] = identity(runtime, catalog(project.workflow)[action].tool)
                identities[action] = dict(memo[key])
                parameters = project.actions.get(action, ActionConfig()).parameters
                if runtime.kind == "local" and getattr(parameters, "engine", None) == "eqy":
                    identities[action]["dependencies"] = {
                        name: identity(runtime, name) for name in ("eqy", "sby", parameters.solver)
                    }
            except (ValueError, OSError, subprocess.SubprocessError) as error:
                if strict:
                    raise
                identities[action] = {"unavailable": str(error)}
        return identities

    def _key(self, action, snap, identities, results):
        spec = catalog(snap["config"].get("workflow"))[action]
        cfg = snap["config"]["actions"].get(action, {})
        file_inputs = {
            path: item
            for path, item in snap["files"].items()
            if item["type"] in [*spec.input_types, "config", "script"] or path == cfg.get("script")
        }
        identities_cfg = snap["config"]["identities"]
        # Rule-deck identity changes affect verification only. PDK/toolchain are conservative global inputs.
        domain_ids = {
            k: v for k, v in identities_cfg.items() if k != "rule_deck" or action.startswith("verify.")
        }
        return digest(
            {
                "schema": 1,
                "harness": {"version": __version__, "code_hash": self.engine_hash},
                "action": spec.model_dump(),
                "inputs": file_inputs,
                "top": snap["config"]["top"],
                "config": cfg,
                "runtime": cfg.get("runtime") or snap["config"]["runtime"],
                "tool_identity": identities.get(action),
                "identities": domain_ids,
                "dependencies": {d: results[d]["result_hash"] for d in spec.dependencies},
            }
        )

    def _intact(self, result):
        try:
            for artifact in result["artifacts"]:
                self.store.read_blob(artifact["content_hash"])
            return True
        except (ValueError, OSError):
            return False

    def _valid(self, candidates, snap, identities):
        valid = {}
        for action, spec in catalog(snap["config"].get("workflow")).items():
            result = candidates.get(action)
            if (
                result
                and all(d in valid for d in spec.dependencies)
                and result["fingerprint"] == self._key(action, snap, identities, valid)
                and self._intact(result)
            ):
                valid[action] = result
        return valid

    def workspace_status(self):
        project = load_project(self.root)
        files = scan(self.root, project)
        state = self.state()
        prior = self.store.get("snapshot", state["source_snapshot_id"]) if state else None
        before = prior["files"] if prior else {}
        changes = [
            {
                "path": path,
                "semantic_type": (files.get(path) or before[path])["type"],
                "change": "added" if path not in before else "deleted" if path not in files else "modified",
            }
            for path in sorted(files.keys() | before.keys())
            if files.get(path) != before.get(path)
        ]
        config_changed = prior is None or project.model_dump() != prior["config"]
        snap = {"files": files, "config": project.model_dump()}
        results = state["results"] if state else {}
        identities = self._identities(project, results, strict=False)
        valid = self._valid(results, snap, identities)
        return {
            "project_id": project.name,
            "base_state_id": state["id"] if state else None,
            "dirty": bool(changes or config_changed),
            "changes": changes,
            "config_changed": config_changed,
            "source_fingerprint": digest(snap),
            "invalidation": {a: a not in valid for a in catalog(project.workflow)},
            "valid_actions": list(valid),
        }

    def list_actions(self):
        project = load_project(self.root)
        valid = self.workspace_status()["valid_actions"]
        return [
            {
                **spec.model_dump(),
                "allowed_input_categories": action_input_categories(spec),
                "configured": action in ("rtl.lint", "logic.synthesize")
                or bool(project.actions.get(action, ActionConfig()).script)
                or project.actions.get(action, ActionConfig()).parameters is not None,
                "dependencies_ready": all(d in valid for d in spec.dependencies),
            }
            for action, spec in catalog(project.workflow).items()
        ]

    def preflight(self, target):
        from eda_harness.core.workspace import scan
        from eda_harness.plugins.semantic import preflight

        project = load_project(self.root)
        files = scan(self.root, project)
        checks = []
        for action in plan(target, project.workflow):
            cfg = project.actions.get(action, ActionConfig())
            try:
                if cfg.parameters is not None:
                    check = preflight(action, project, {"files": files})
                else:
                    if not cfg.script and action not in ("rtl.lint", "logic.synthesize"):
                        raise ValueError("Configure structured parameters or a project script")
                    check = {"action": action, "ready": True, "mode": "script" if cfg.script else "default"}
                check["tool_identity"] = self._identities(project, [action])[action]
            except (ValueError, OSError) as error:
                check = {"action": action, "ready": False, "error": str(error)}
            checks.append(check)
        return {
            "ready": all(c["ready"] for c in checks),
            "checks": checks,
            "scope": "Inputs, declared dependencies and runtime identity; tool-version compatibility is established by native contract tests",
        }

    def submit(self, target, workflow=False, background=True, force=False):
        with project_lock(self.store):
            if self._active():
                raise ValueError("Project already has an active run; inspect/cancel/recover it first")
            snap = snapshot(self.root, self.store, self.store.meta("current_state"))
            project = Project.model_validate(snap["config"])
            actions = plan(target, project.workflow) if workflow else [canonical(target, project.workflow)]
            needs_rtl = any("rtl" in catalog(project.workflow)[a].input_types for a in actions)
            if needs_rtl and not any(f["type"] == "rtl" for f in snap["files"].values()):
                raise ValueError("Project must declare at least one existing RTL input")
            base = self.state()
            identities = self._identities(project, base["results"] if base else {}, strict=False)
            identities.update(self._identities(project, actions))
            valid = self._valid(base["results"] if base else {}, snap, identities)
            for action in actions:
                cfg = project.actions.get(action, ActionConfig())
                if cfg.parameters is not None:
                    from eda_harness.plugins.semantic import preflight

                    preflight(action, project, snap)
                if (
                    action not in ("rtl.lint", "logic.synthesize")
                    and not cfg.script
                    and cfg.parameters is None
                ):
                    raise ValueError(
                        f"{action} requires a configured script; no placeholder execution is allowed"
                    )
            if not workflow and any(
                d not in valid for d in catalog(project.workflow)[actions[0]].dependencies
            ):
                raise ValueError("Dependencies missing or stale; use run-until to build the dependency DAG")
            run = {
                "id": uid("R"),
                "project_id": project.name,
                "target": target,
                "actions": actions,
                "status": "PENDING",
                "source_snapshot_id": snap["id"],
                "base_state_id": snap["base_state_id"],
                "state_id": None,
                "steps": [],
                "goal_id": self.store.meta("active_goal"),
                "engine_hash": self.engine_hash,
                "tool_identities": identities,
                "force": force,
                "cancel_requested": False,
                "worker_pid": None if background else os.getpid(),
                "process_pid": None,
                "container_name": None,
                "created_at": now(),
                "updated_at": now(),
                "error": None,
            }
            self.store.put("run", run["id"], run)
            if background:
                folder = self.store.root / "runs" / run["id"]
                folder.mkdir()
                try:
                    with (folder / "worker.log").open("ab") as log:
                        proc = subprocess.Popen(
                            [sys.executable, "-m", "eda_harness.worker", str(self.root), run["id"]],
                            stdin=subprocess.DEVNULL,
                            stdout=log,
                            stderr=log,
                            start_new_session=True,
                            cwd=self.root,
                        )
                    run["worker_pid"] = proc.pid
                    self.store.put("run", run["id"], run, mutable=True)
                except Exception as error:
                    run.update(status="FAILED", error=f"Cannot launch worker: {error}")
                    self.store.put("run", run["id"], run, mutable=True)
                    raise
        if not background:
            self.execute(run["id"])
            return self.get_run(run["id"])
        return {"run_id": run["id"], "status": run["status"]}

    def _update(self, run_id, **fields):
        with project_lock(self.store):
            run = self.store.get("run", run_id)
            run.update(fields, updated_at=now())
            self.store.put("run", run_id, run, mutable=True)
            return run

    def _acceptance(self, results, project):
        goal_id = self.store.meta("active_goal")
        goal = self.store.get("goal", goal_id) if goal_id else None
        baseline_id = goal["baseline_state_id"] if goal else self.store.meta("baseline_state")
        baseline = self.state(baseline_id) if baseline_id else None
        return evaluate(
            results,
            goal["constraints"] if goal else project.constraints,
            goal["required_verification"] if goal else project.required_verification,
            metrics_from(
                baseline["results"],
                self.store.get("snapshot", baseline["source_snapshot_id"])["config"].get("workflow"),
            )
            if baseline
            else None,
            project.workflow,
        )

    def execute(self, run_id):
        with project_lock(self.store):
            run = self.store.get("run", run_id)
            if run["status"] != "PENDING":
                raise ValueError("Run has already been claimed")
            run.update(status="PREPARING", worker_pid=os.getpid(), updated_at=now())
            self.store.put("run", run_id, run, mutable=True)
        try:
            self._execute(run)
        except Exception as error:
            failed_run = self.store.get("run", run_id)
            steps = failed_run["steps"]
            if steps and steps[-1]["status"] not in TERMINAL:
                steps[-1]["status"] = "FAILED"
                steps[-1].setdefault("artifacts", [])
                steps[-1]["verification"] = {
                    "action": steps[-1]["action"], "status": "UNKNOWN",
                    "summary": f"Execution/collection exception: {type(error).__name__}: {error}",
                    "evidence": steps[-1]["artifacts"],
                }
                steps[-1]["diagnostics"] = [{
                    "category": "execution", "severity": "high", "summary": str(error),
                    "evidence": steps[-1]["artifacts"],
                    "details": {"code": "RUNTIME_EXCEPTION", "action": steps[-1]["action"]},
                }]
            cleanup = cleanup_container(failed_run["container_name"]) if failed_run.get("container_name") else {}
            pending = bool(cleanup and not cleanup["confirmed"])
            if cleanup and cleanup["confirmed"]:
                release_resources(failed_run["container_name"])
            self._update(
                run_id,
                status="FAILED",
                error=f"{type(error).__name__}: {error}",
                steps=steps,
                finished_at=now(),
                process_pid=None,
                container_name=failed_run.get("container_name") if pending else None,
                cleanup_pending=pending,
                cleanup=cleanup,
            )

    def _execute(self, run):
        run_id = run["id"]
        snap = self.store.get("snapshot", run["source_snapshot_id"])
        project = Project.model_validate(snap["config"])
        if run.get("engine_hash") != self.engine_hash:
            raise ValueError("Harness implementation changed after submission; resubmit with the new version")
        parent = self.state(run["base_state_id"]) if run["base_state_id"] else None
        results = self._valid(parent["results"] if parent else {}, snap, run["tool_identities"])
        folder = self.store.root / "runs" / run_id
        source = folder / "inputs"
        materialize(self.store, snap, source)
        steps = []
        for index, action in enumerate(run["actions"]):
            if self.store.get("run", run_id)["cancel_requested"]:
                self._update(run_id, status="CANCELLED", finished_at=now())
                return
            spec = catalog(snap["config"].get("workflow"))[action]
            if any(d not in results for d in spec.dependencies):
                raise ValueError(f"Missing dependencies for {action}")
            fingerprint = self._key(action, snap, run["tool_identities"], results)
            state_id = uid("S")
            cached = None
            if not run["force"]:
                for candidate in reversed(self.store.list("cache")):
                    if candidate["fingerprint"] == fingerprint and self._intact(candidate):
                        cached = candidate
                        break
            step = {"action": action, "status": "PREPARING", "cache_hit": cached is not None}
            steps.append(step)
            self._update(run_id, status="PREPARING", steps=steps)
            if cached:
                result = cached
            else:
                step_root = folder / f"{index:02d}-{action}"
                work, deps = step_root / "work", step_root / "deps"
                work.mkdir(parents=True)
                deps.mkdir()
                manifest = {}
                for dep in spec.dependencies:
                    manifest[dep] = []
                    for artifact in results[dep]["artifacts"]:
                        target = deps / dep / artifact["type"]
                        target.parent.mkdir(exist_ok=True)
                        target.write_bytes(self.store.read_blob(artifact["content_hash"]))
                        manifest[dep].append(
                            {
                                "id": artifact["id"],
                                "type": artifact["type"],
                                "file": str(target.relative_to(deps)),
                            }
                        )
                (deps / "manifest.json").write_text(json.dumps(manifest, indent=2))
                runtime = Runtime(
                    self._runtime(project, action),
                    run["tool_identities"][action],
                    f"eda-{run_id.lower()}-{index}",
                    source,
                    deps,
                    work,
                )
                plugin = PLUGINS[spec.tool]
                prepared = plugin.prepare(action, project, snap, runtime.paths, work)
                cfg = project.actions.get(action, ActionConfig())
                outputs = dict(prepared.outputs)
                if cfg.metrics_file:
                    outputs["report.metrics"] = cfg.metrics_file
                if cfg.verification_file:
                    outputs["report.verification"] = cfg.verification_file
                log = work / "tool.log"
                env = {
                    "EDA_INPUT_DIR": runtime.paths["input"],
                    "EDA_DEPS_DIR": runtime.paths["deps"],
                    "EDA_OUTPUT_DIR": runtime.paths["work"],
                    "EDA_TOP": project.top,
                    "EDA_ACTION": action,
                }
                executions = []
                for command in prepared.commands:
                    self._update(
                        run_id, status="RUNNING",
                        container_name=runtime.token if runtime.config.kind == "docker" else None,
                    )
                    execution = runtime.execute(
                        command,
                        env,
                        log,
                        lambda: self.store.get("run", run_id)["cancel_requested"],
                        lambda pid, container: self._update(
                            run_id, process_pid=pid, container_name=container
                        ),
                    )
                    executions.append(execution.__dict__)
                    if execution.status != "SUCCESS":
                        break
                pending = bool(execution.cleanup and not execution.cleanup["confirmed"])
                self._update(
                    run_id, status="COLLECTING", process_pid=None,
                    container_name=runtime.token if pending else None, cleanup_pending=pending,
                )
                (work / "execution.json").write_text(json.dumps(executions, indent=2) + "\n")
                outputs["report.execution"] = "execution.json"
                outputs["log.tool"] = "tool.log"
                artifacts = []
                missing = []
                for type_, rel in outputs.items():
                    path = safe_path(work, rel)
                    if not path.is_file() or path.stat().st_size == 0:
                        missing.append(f"{type_}: {rel}")
                        continue
                    content = path.read_bytes()
                    artifact = Artifact(
                        id=uid("A"),
                        type=type_,
                        content_hash=self.store.blob(content),
                        size=len(content),
                        producer_run_id=run_id,
                        state_id=None,
                        path=rel,
                        uri="",
                    ).model_dump()
                    artifact["uri"] = f"eda://artifact/{artifact['id']}"
                    self.store.put("artifact", artifact["id"], artifact)
                    artifacts.append(artifact)
                absent_types = set(spec.artifact_types) - {a["type"] for a in artifacts}
                step.update(executions=executions, artifacts=[a["id"] for a in artifacts])
                if execution.status != "SUCCESS":
                    step["status"] = execution.status
                    diagnostics = diagnose(execution, log.read_text(errors="replace")[-65536:], action)
                    for diagnostic in diagnostics:
                        diagnostic["evidence"] = [a["id"] for type_ in ("log.tool", "report.execution")
                                                  for a in artifacts if a["type"] == type_]
                    step.update(
                        diagnostics=diagnostics,
                        verification={"action": action, "status": "UNKNOWN", "summary": "Execution failed; no engineering acceptance", "evidence": [a["id"] for a in artifacts]},
                    )
                    self._update(
                        run_id,
                        status=execution.status,
                        steps=steps,
                        finished_at=now(),
                        error=f"{action}: " + "; ".join(d["summary"] for d in diagnostics),
                    )
                    return
                self._update(run_id, steps=steps)
                if missing or absent_types:
                    raise ValueError(
                        f"Required outputs missing/empty: {missing}, types={sorted(absent_types)}"
                    )
                self._update(run_id, status="VERIFYING", steps=steps)
                observation = plugin.observe(action, project, work, log, artifacts)
                result = {
                    "action": action,
                    "fingerprint": fingerprint,
                    "artifacts": artifacts,
                    "metrics": [m.model_dump() for m in observation.metrics],
                    "verification": observation.verification.model_dump(),
                    "diagnostics": [d.model_dump() for d in observation.diagnostics],
                    "producer_run_id": run_id,
                }
                result["result_hash"] = digest(
                    {
                        "fingerprint": fingerprint,
                        "artifacts": {
                            a["type"]: a["content_hash"] for a in artifacts if a["type"] != "log.tool"
                        },
                        "metrics": [
                            {k: v for k, v in m.items() if k != "evidence"} for m in result["metrics"]
                        ],
                        "verification": observation.verification.status,
                    }
                )
                # A failed check remains observable but is never a reusable cache entry.
                if result["verification"]["status"] != "FAIL":
                    self.store.put("cache", uid("C"), result)
            results[action] = result
            # Re-running an upstream node invalidates downstream records by dependency result hashes.
            results = self._valid(results, snap, run["tool_identities"])
            acceptance = self._acceptance(results, project)
            state = DesignState(
                id=state_id,
                project_id=project.name,
                parent_state_ids=[parent["id"]] if parent else [],
                source_snapshot_id=snap["id"],
                stage=action,
                results=results,
                acceptance=acceptance,
                provenance={
                    "run_id": run_id,
                    "tool_identity": run["tool_identities"][action],
                    "fingerprint": fingerprint,
                    "cache_hit": cached is not None,
                    "reused_from_run": result["producer_run_id"] if cached else None,
                    "harness_version": __version__,
                    "engine_hash": self.engine_hash,
                    "goal_id": run.get("goal_id"),
                },
                created_at=now(),
            ).model_dump()
            with project_lock(self.store):
                # State and head must commit atomically; immutable rows are never overwritten.
                from eda_harness.core.store import encoded

                with self.store.connect() as db:
                    db.execute(
                        "INSERT INTO objects VALUES ('state', ?, ?)", (state_id, encoded(state).decode())
                    )
                    db.execute(
                        "INSERT OR REPLACE INTO metadata VALUES ('current_state', ?)", (json.dumps(state_id),)
                    )
                if self.store.meta("baseline_state") is None:
                    self.store.set_meta("baseline_state", state_id)
            parent = state
            step.update(
                status="SUCCESS",
                state_id=state_id,
                design_status=acceptance["status"],
                verification=result["verification"]["status"],
            )
            self._update(run_id, steps=steps, state_id=state_id)
            if result["verification"]["status"] == "FAIL":
                # Process success and design failure are intentionally different dimensions.
                self._update(
                    run_id,
                    status="SUCCESS",
                    design_status="BLOCKED",
                    stopped_reason=f"Verification failed at {action}",
                    finished_at=now(),
                )
                return
        self._update(
            run_id, status="SUCCESS", design_status=parent["acceptance"]["status"], finished_at=now()
        )

    def get_run(self, run_id):
        return self.store.get("run", run_id)

    def cancel_run(self, run_id):
        with project_lock(self.store):
            run = self.get_run(run_id)
            if run["status"] not in TERMINAL:
                run.update(cancel_requested=True, updated_at=now())
                self.store.put("run", run_id, run, mutable=True)
            return {"run_id": run_id, "status": run["status"], "cancel_requested": run["cancel_requested"]}

    def recover_runs(self):
        recovered = []
        pending = []
        with project_lock(self.store):
            for run in self._active():
                pid = run["worker_pid"]
                try:
                    if pid and not run.get("cleanup_pending"):
                        os.kill(pid, 0)
                        continue
                except ProcessLookupError:
                    pass
                if run.get("container_name"):
                    cleanup = cleanup_container(run["container_name"])
                    run["cleanup"] = cleanup
                    if not cleanup["confirmed"]:
                        run.update(cleanup_pending=True, updated_at=now())
                        self.store.put("run", run["id"], run, mutable=True)
                        pending.append(run["id"])
                        continue
                    release_resources(run["container_name"])
                run.update(
                    status="FAILED",
                    error="Worker lost; resubmit to reuse completed states",
                    finished_at=now(),
                    updated_at=now(),
                    cleanup_pending=False,
                    container_name=None,
                    process_pid=None,
                )
                self.store.put("run", run["id"], run, mutable=True)
                recovered.append(run["id"])
        return {"recovered": recovered, "cleanup_pending": pending}

    def get_metrics(self, state_id=None):
        state = self.state(state_id)
        return list(state["acceptance"]["metrics"].values()) if state else []

    def get_diagnostics(self, state_id=None, category=None):
        state = self.state(state_id)
        items = [d for r in state["results"].values() for d in r["diagnostics"]] if state else []
        return [d for d in items if category is None or d["category"] == category]

    def get_artifacts(self, state_id=None, type=None):
        state = self.state(state_id)
        items = [a for r in state["results"].values() for a in r["artifacts"]] if state else []
        return [{**a, "state_id": state["id"]} for a in items if type is None or a["type"] == type]

    def compare_states(self, state_ids):
        if len(state_ids) < 2:
            raise ValueError("Compare requires at least two states")
        metrics = {s: {m["name"]: m for m in self.get_metrics(s)} for s in state_ids}
        names = sorted({name for values in metrics.values() for name in values})
        rows = []
        for name in names:
            values = {s: metrics[s].get(name) for s in state_ids}
            units = {m["unit"] for m in values.values() if m}
            base = values[state_ids[0]]
            rows.append(
                {
                    "name": name,
                    "values": values,
                    "delta": {
                        s: values[s]["value"] - base["value"]
                        if base and values[s] and len(units) == 1
                        else None
                        for s in state_ids[1:]
                    },
                    "comparable": len(units) == 1,
                }
            )
        return {"states": state_ids, "metrics": rows}

    def checkout_state(self, state_id, force=False):
        with project_lock(self.store):
            if self._active():
                raise ValueError("Cannot checkout while a run is active")
            status = self.workspace_status()
            if status["dirty"] and not force:
                raise ValueError(
                    "Working copy is dirty; commit/copy edits or use force=True (creates recovery snapshot)"
                )
            target = self.state(state_id)
            snap = self.store.get("snapshot", target["source_snapshot_id"])
            # Resolve and verify everything before mutating; protect unrelated untracked paths as well.
            for path, item in snap["files"].items():
                safe_path(self.root, path)
                self.store.read_blob(item["hash"])
            recovery = snapshot(self.root, self.store, status["base_state_id"])
            tracked = set(recovery["files"])
            for path in snap["files"]:
                existing = safe_path(self.root, path)
                if existing.exists() and path not in tracked:
                    raise ValueError(f"Checkout would overwrite untracked file: {path}")
            for path in tracked - snap["files"].keys():
                safe_path(self.root, path).unlink(missing_ok=True)
            materialize(self.store, snap, self.root)
            self.store.set_meta("current_state", state_id)
            return {"state_id": state_id, "recovery_snapshot_id": recovery["id"]}

    def create_goal(self, description, constraints, required_verification=None, baseline_state_id=None):
        with project_lock(self.store):
            if self._active():
                raise ValueError(
                    "Change goals between runs to keep acceptance policy stable during execution"
                )
            project = load_project(self.root)
            baseline_state_id = baseline_state_id or self.store.meta("baseline_state")
            if baseline_state_id:
                self.state(baseline_state_id)
            required = (
                project.required_verification if required_verification is None else required_verification
            )
            goal = GoalState(
                id=uid("G"),
                description=description,
                baseline_state_id=baseline_state_id,
                constraints=constraints,
                required_verification=[canonical(a, project.workflow) for a in required],
            ).model_dump()
            if "area.delta_percent" in goal["constraints"]:
                baseline = self.state(baseline_state_id) if baseline_state_id else None
                area = (
                    metrics_from(
                        baseline["results"],
                        self.store.get("snapshot", baseline["source_snapshot_id"])["config"].get("workflow"),
                    ).get("area.total")
                    if baseline
                    else None
                )
                if area is None or area["value"] <= 0:
                    raise ValueError("Area delta goals require a baseline with positive area.total")
            self.store.put("goal", goal["id"], goal)
            self.store.set_meta("active_goal", goal["id"])
            return goal

    def record_decision(self, base_state_id, result_state_id, change_summary, outcome):
        self.state(base_state_id)
        if result_state_id:
            self.state(result_state_id)
        if outcome not in ("KEEP", "REJECT", "INCONCLUSIVE"):
            raise ValueError("Outcome must be KEEP, REJECT, or INCONCLUSIVE")
        entry = {
            "id": uid("E"),
            "base_state_id": base_state_id,
            "result_state_id": result_state_id,
            "change_summary": change_summary,
            "outcome": outcome,
            "created_at": now(),
            "result_summary": self.compare_states([base_state_id, result_state_id])
            if result_state_id
            else {},
        }
        self.store.put("decision", entry["id"], entry)
        return entry

    def get_operational_context(self, detail="standard"):
        if detail not in ("brief", "standard", "deep"):
            raise ValueError("Detail must be brief, standard, or deep")
        project = load_project(self.root)
        state = self.state()
        status = self.workspace_status()
        goal_id = self.store.meta("active_goal")
        goal = self.store.get("goal", goal_id) if goal_id else None
        valid = {a: r for a, r in state["results"].items() if a in status["valid_actions"]} if state else {}
        acceptance = self._acceptance(valid, project)
        runs = self.store.list("run")
        last_run = (
            {k: runs[-1].get(k) for k in ("id", "target", "status", "error", "state_id", "design_status")}
            if runs
            else None
        )
        result = {
            "last_run": last_run,
            "project_id": project.name,
            "current_state": state["id"] if state else None,
            "stage": state["stage"] if state else None,
            "working_copy": status,
            "active_goal": goal,
            "baseline_state": goal["baseline_state_id"] if goal else self.store.meta("baseline_state"),
            "design_status": acceptance["status"],
            "acceptance": acceptance,
            "active_runs": [
                {k: r[k] for k in ("id", "target", "status", "updated_at")} for r in self._active()
            ],
        }
        count = {"brief": 3, "standard": 5, "deep": 15}[detail]
        result["blocking_diagnostics"] = blocking_diagnostics(valid, acceptance, count)
        result["execution_diagnostics"] = execution_diagnostics(runs[-1] if runs else None, count)
        if detail != "brief":
            result.update(
                recent_transitions=[
                    {"id": s["id"], "parents": s["parent_state_ids"], "stage": s["stage"]}
                    for s in self.store.list("state")[-count:]
                ],
                recent_experiments=self.store.list("decision")[-count:],
                available_actions=self.list_actions(),
            )
        if detail == "deep":
            result["artifacts"] = self.get_artifacts()[:50]
        return result

    def inspect_project(self):
        return self.get_operational_context("brief")

    def read_artifact(self, artifact_id, offset=0, limit=16000):
        if offset < 0 or not 1 <= limit <= 64000:
            raise ValueError("offset >= 0 and 1 <= limit <= 64000 required")
        artifact = self.store.get("artifact", artifact_id)
        data = self.store.read_blob(artifact["content_hash"])
        chunk = data[offset : offset + limit]
        try:
            text = chunk.decode("utf-8")
        except UnicodeDecodeError:
            return {
                "artifact": artifact,
                "binary": True,
                "message": "Use eda export-artifact to materialize this artifact",
            }
        return {
            "artifact": artifact,
            "offset": offset,
            "text": text,
            "next_offset": offset + len(chunk) if offset + len(chunk) < len(data) else None,
        }

    def export_artifact(self, artifact_id, destination):
        artifact = self.store.get("artifact", artifact_id)
        destination = Path(destination).resolve()
        if destination.exists():
            raise ValueError("Export destination already exists")
        destination.write_bytes(self.store.read_blob(artifact["content_hash"]))
        return {"path": str(destination), "content_hash": artifact["content_hash"]}
