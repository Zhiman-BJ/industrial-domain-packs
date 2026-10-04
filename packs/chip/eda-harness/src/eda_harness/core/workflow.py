from eda_harness.core.models import ActionSpec


def spec(id, tool, deps, inputs, outputs=()):
    return ActionSpec(id=id, tool=tool, dependencies=deps, input_types=inputs, artifact_types=list(outputs))


ACTIONS = {
    a.id: a
    for a in [
        spec("rtl.lint", "verilator", [], ["rtl", "include"]),
        spec(
            "rtl.simulate", "verilator", ["rtl.lint"], ["rtl", "include", "testbench"], ["report.simulation"]
        ),
        spec("logic.synthesize", "yosys", ["rtl.lint"], ["rtl", "include", "liberty"], ["netlist.gate"]),
        spec(
            "physical.floorplan",
            "openroad",
            ["logic.synthesize"],
            ["sdc", "lef", "liberty", "physical"],
            ["layout.floorplan_odb"],
        ),
        spec(
            "physical.place",
            "openroad",
            ["physical.floorplan"],
            ["sdc", "liberty", "physical"],
            ["layout.placed_odb"],
        ),
        spec(
            "physical.cts", "openroad", ["physical.place"], ["sdc", "liberty", "physical"], ["layout.cts_odb"]
        ),
        spec(
            "physical.route",
            "openroad",
            ["physical.cts"],
            ["sdc", "liberty", "physical"],
            ["layout.routed_odb", "layout.def", "parasitic.spef"],
        ),
        spec(
            "physical.streamout", "klayout", ["physical.route"], ["lef", "gds", "streamout"], ["layout.gds"]
        ),
        spec("analysis.sta", "openroad", ["physical.route"], ["sdc", "liberty"], ["report.timing"]),
        spec(
            "analysis.power", "openroad", ["physical.route"], ["sdc", "liberty", "activity"], ["report.power"]
        ),
        spec("verify.drc", "klayout", ["physical.streamout"], ["drc_rules"], ["report.drc"]),
        spec(
            "verify.lvs",
            "klayout",
            ["physical.streamout", "logic.synthesize"],
            ["lvs_rules", "liberty"],
            ["report.lvs"],
        ),
    ]
}
ALIASES = {
    "lint": "rtl.lint",
    "simulate": "rtl.simulate",
    "synth": "logic.synthesize",
    "floorplan": "physical.floorplan",
    "place": "physical.place",
    "cts": "physical.cts",
    "route": "physical.route",
    "sta": "analysis.sta",
    "drc": "verify.drc",
    "lvs": "verify.lvs",
}
TARGETS = {
    "post_route_sta": ["analysis.sta"],
    "final_verify": ["rtl.simulate", "analysis.sta", "verify.drc", "verify.lvs"],
}


def catalog(workflow=None):
    """Resolve an immutable project DAG in topological order, independent of YAML order."""
    from eda_harness.core.models import WorkflowSpec

    workflow = WorkflowSpec.model_validate(workflow or {})
    actions = dict(ACTIONS)
    seen = set()
    for action in workflow.actions:
        if action.id in seen:
            raise ValueError(f"Duplicate action: {action.id}")
        seen.add(action.id)
        if action.tool not in {"verilator", "yosys", "openroad", "klayout", "magic", "netgen", "gtkwave"}:
            raise ValueError(f"Unsupported tool plugin: {action.tool}")
        if len(action.dependencies) != len(set(action.dependencies)):
            raise ValueError(f"Duplicate dependency: {action.id}")
        if (
            action.id in ACTIONS
            and action.tool != ACTIONS[action.id].tool
            and not (action.id == "verify.lvs" and action.tool == "netgen")
        ):
            raise ValueError(f"Cannot change built-in tool identity: {action.id}")
        actions[action.id] = action
    ordered, visiting = {}, set()

    def visit(name):
        if name not in actions:
            raise ValueError(f"Unknown action dependency: {name}")
        if name in visiting:
            raise ValueError(f"Workflow cycle at {name}")
        if name in ordered:
            return
        visiting.add(name)
        for dependency in actions[name].dependencies:
            visit(dependency)
        visiting.remove(name)
        ordered[name] = actions[name]

    for name in actions:
        visit(name)
    for name, targets in workflow.targets.items():
        if name in actions or name in ALIASES or not targets:
            raise ValueError(f"Invalid workflow target: {name}")
        for target in targets:
            if target not in actions:
                raise ValueError(f"Unknown target action: {target}")
    return ordered


def canonical(action, workflow=None):
    action = ALIASES.get(action, action)
    if action not in catalog(workflow):
        raise ValueError(f"Unknown action: {action}")
    return action


def plan(target, workflow=None):
    from eda_harness.core.models import WorkflowSpec

    workflow = WorkflowSpec.model_validate(workflow or {})
    actions = catalog(workflow)
    targets = {**TARGETS, **workflow.targets}
    result = []

    def visit(action):
        if action in result:
            return
        for dep in actions[action].dependencies:
            visit(dep)
        result.append(action)

    for action in targets.get(target, [ALIASES.get(target, target)]):
        visit(canonical(action, workflow))
    return result


def action_input_categories(spec):
    """Categories a file reference may use for this action, including shared inputs."""
    return sorted(set(spec.input_types) | {"config", "script"})


def project_input_categories(workflow=None):
    """Dynamic inputs keys accepted by Project for the resolved workflow."""
    return sorted(
        {category for spec in catalog(workflow).values() for category in action_input_categories(spec)}
    )
