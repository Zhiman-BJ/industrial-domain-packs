import operator

from eda_harness.core.models import Constraint

OPS = {">=": operator.ge, "<=": operator.le, "==": operator.eq, ">": operator.gt, "<": operator.lt}


def metrics_from(results, workflow=None):
    # Later DAG stages may update metrics such as area; result insertion order is not trusted.
    from eda_harness.core.workflow import catalog

    metrics = {}
    for action in catalog(workflow):
        if action in results:
            for metric in results[action]["metrics"]:
                metrics[metric["name"]] = metric
    return metrics


def evaluate(results, constraints, required, baseline_metrics=None, workflow=None):
    metrics = metrics_from(results, workflow)
    if baseline_metrics and "area.total" in metrics and "area.total" in baseline_metrics:
        base, current = baseline_metrics["area.total"], metrics["area.total"]
        if base["value"] > 0 and base["unit"] == current["unit"]:
            metrics["area.delta_percent"] = {
                **current,
                "name": "area.delta_percent",
                "unit": "%",
                "value": 100 * (current["value"] / base["value"] - 1),
            }
    blockers, missing = [], []
    for action in required:
        verification = results.get(action, {}).get("verification", {})
        status = verification.get("status", "UNKNOWN")
        if status == "FAIL":
            blockers.append(f"Verification failed: {action}")
        elif status != "PASS":
            missing.append(f"Verification missing/unknown: {action}")
    for name, raw in constraints.items():
        rule = raw if isinstance(raw, Constraint) else Constraint.model_validate(raw)
        metric = metrics.get(name)
        if metric is None:
            missing.append(f"Missing metric: {name}")
        elif rule.unit and rule.unit != metric["unit"]:
            missing.append(f"Unit mismatch for {name}: expected {rule.unit}, got {metric['unit']}")
        elif not OPS[rule.op](metric["value"], rule.value):
            blockers.append(f"{name}: {metric['value']} {rule.op} {rule.value} is false")
    # An observed verification failure cannot be hidden by a weaker goal policy.
    for action, result in results.items():
        if result["verification"]["status"] == "FAIL" and action not in required:
            blockers.append(f"Verification failed: {action}")
    status = "BLOCKED" if blockers else "INCOMPLETE" if missing or not (required or constraints) else "PASS"
    return {"status": status, "blockers": blockers, "missing_evidence": missing, "metrics": metrics}
