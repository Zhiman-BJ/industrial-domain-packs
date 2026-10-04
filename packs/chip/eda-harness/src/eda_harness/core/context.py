"""Bounded, evidence-backed failure summaries for runtime-independent recovery."""


def blocking_diagnostics(results, acceptance, limit):
    blockers = acceptance["blockers"]
    missing = acceptance["missing_evidence"]
    failed, incomplete = [], []
    covered = set()
    for action, result in results.items():
        verification = result["verification"]
        failure = verification["status"] == "FAIL"
        marker = f"Verification missing/unknown: {action}"
        if not failure and marker not in missing:
            continue
        diagnostics = result.get("diagnostics") or [
            {
                "category": action,
                "severity": "high" if failure else "medium",
                "summary": verification.get("summary") or marker,
                "evidence": verification.get("evidence", []),
                "details": {},
            }
        ]
        (failed if failure else incomplete).extend(diagnostics)
        covered.add(f"Verification failed: {action}" if failure else marker)
    for summary in [*blockers, *missing]:
        if summary in covered:
            continue
        metric = next(
            (
                m
                for name, m in acceptance["metrics"].items()
                if summary.startswith(name + ":") or summary.startswith("Unit mismatch for " + name + ":")
            ),
            None,
        )
        failure = summary in blockers
        (failed if failure else incomplete).append(
            {
                "category": "acceptance",
                "severity": "high" if failure else "medium",
                "summary": summary,
                "evidence": [metric["evidence"]] if metric and metric.get("evidence") else [],
                "details": {"metric": metric} if metric else {},
            }
        )
    ranks = {"critical": 0, "high": 1, "medium": 2, "low": 3, "info": 4}
    # Real failures precede missing evidence, regardless of insertion order or DAG depth.
    ordered = sorted(failed, key=lambda d: ranks.get(d["severity"], 5)) + sorted(
        incomplete, key=lambda d: ranks.get(d["severity"], 5)
    )
    return ordered[:limit]


def execution_diagnostics(run, limit):
    if not run or run["status"] not in {"FAILED", "TIMEOUT", "CANCELLED"}:
        return []
    steps = [s for s in run.get("steps", []) if s["status"] in {"FAILED", "TIMEOUT", "CANCELLED"}]
    if not steps:
        steps = [{}]
    detailed = [
        {**diagnostic, "details": {**diagnostic.get("details", {}), "run_id": run["id"], "status": run["status"]}}
        for step in steps for diagnostic in step.get("diagnostics", [])
    ]
    if detailed:
        return detailed[:limit]
    return [
        {
            "category": "execution",
            "severity": "medium" if run["status"] == "CANCELLED" else "high",
            "summary": run.get("error") or f"Run {run['status'].lower()}",
            "evidence": step.get("artifacts", []),
            "details": {"run_id": run["id"], "status": run["status"], "action": step.get("action")},
        }
        for step in steps[:limit]
    ]
