import argparse
import json
import sys
import time

from eda_harness.core.service import TERMINAL, Harness


def emit(value):
    print(json.dumps(value, indent=2, ensure_ascii=False, allow_nan=False))


def main():
    parser = argparse.ArgumentParser(prog="eda", description="Persistent EDA execution and design state")
    parser.add_argument(
        "--project", default=None, help="Explicit project root; MCP otherwise has no default project"
    )
    sub = parser.add_subparsers(dest="command", required=True)
    init = sub.add_parser("init")
    init.add_argument("--name", default="design")
    init.add_argument("--top", required=True)
    init.add_argument("--image")
    init.add_argument("--local", action="store_true")
    tool_parser = sub.add_parser("tools", help="Inspect all required EDA tools in the unified image")
    tool_parser.add_argument("--image", default="eda-harness-tools:dev")
    doctor = sub.add_parser("doctor", help="Check project and execution environment readiness")
    doctor.add_argument("--probe-capabilities", action="store_true")
    doctor.add_argument("--image", help="Candidate Docker tool image before project initialization")

    doctor.add_argument("--target", help="Also check target inputs and required tools")
    sub.add_parser("capabilities")
    sub.add_parser("viewers", help="Discover installed host desktop viewers")
    view = sub.add_parser("open-viewer")
    view.add_argument("artifact_id")
    view.add_argument("--viewer", choices=["gtkwave", "klayout", "magic", "openroad", "netlistsvg", "yosys_show", "yosys_viz"])
    view.add_argument("--companion", action="append", default=[])
    view.add_argument("--state")
    view.add_argument("--signal", action="append")
    view.add_argument("--time-range", nargs=2, type=float)
    view.add_argument("--technology")
    view.add_argument("--top")
    view.add_argument("--prepare-only", action="store_true")
    view.add_argument("--render-only", action="store_true", help="Render netlist SVG without a browser")
    pre = sub.add_parser("preflight")
    pre.add_argument("target")
    sub.add_parser("inspect")
    context = sub.add_parser("context")
    context.add_argument("--detail", choices=["brief", "standard", "deep"], default="standard")
    ws = sub.add_parser("workspace")
    ws.add_argument("operation", choices=["status"])
    sub.add_parser("actions")
    for name in ("run", "run-until"):
        run = sub.add_parser(name)
        run.add_argument("target")
        run.add_argument("--wait", action="store_true")
        run.add_argument("--force", action="store_true", help="Bypass cache")
    for name in ("get-run", "cancel", "wait"):
        run = sub.add_parser(name)
        run.add_argument("run_id")
    sub.add_parser("recover")
    for name in ("metrics", "diagnose", "artifacts"):
        query = sub.add_parser(name)
        query.add_argument("--state")
        if name == "artifacts":
            query.add_argument("--type")
    sub.add_parser("states")
    compare = sub.add_parser("compare")
    compare.add_argument("state_ids", nargs="+")
    checkout = sub.add_parser("checkout")
    checkout.add_argument("state_id")
    checkout.add_argument("--force", action="store_true")
    goal = sub.add_parser("goal")
    goal.add_argument("description")
    goal.add_argument("--constraints", required=True, help="JSON object of normalized metric constraints")
    goal.add_argument("--require", nargs="*")
    goal.add_argument("--baseline")
    export = sub.add_parser("export-artifact")
    export.add_argument("artifact_id")
    export.add_argument("destination")
    read = sub.add_parser("read-artifact")
    read.add_argument("artifact_id")
    read.add_argument("--offset", type=int, default=0)
    mcp_parser = sub.add_parser("mcp")
    mcp_parser.add_argument("--read-only", action="store_true", help="Expose inspection tools only")
    args = parser.parse_args()
    if args.command != "mcp" and args.project is None:
        args.project = "."
    try:
        if args.command == "doctor":
            from eda_harness.core.environment import check_environment

            result = check_environment(args.project, args.target, args.image, args.probe_capabilities)

            emit(result)
            if not result["ready"]:
                raise SystemExit(1)
            return
        if args.command == "viewers":
            from eda_harness.viewers import viewer_capabilities

            emit(viewer_capabilities())
            return
        if args.command == "preflight":
            result = Harness(args.project).preflight(args.target)
            emit(result)
            if not result["ready"]:
                raise SystemExit(1)
            return
        if args.command == "capabilities":
            from eda_harness.plugins.semantic import capabilities

            emit(capabilities())
            return
        if args.command == "tools":
            from eda_harness.tool_inventory import inspect_tools

            result = inspect_tools(args.image)
            emit(result)
            if not result["ready"]:
                sys.exit(1)
            return
        if args.command == "init":
            from eda_harness.core.initialization import initialize_project

            result = initialize_project(args.project, args.top, args.name, args.image, args.local)
            emit(result)
            if not result["created"]:
                raise SystemExit(1)
            return
        if args.command == "mcp":
            from eda_harness.server.mcp import create_server

            create_server(args.project, read_only=args.read_only).run(transport="stdio")
            return
        h = Harness(args.project)
        match args.command:
            case "open-viewer":
                from eda_harness.viewers import open_viewer

                result = open_viewer(
                    h,
                    args.artifact_id,
                    args.viewer,
                    companion_ids=args.companion,
                    state_id=args.state,
                    signals=args.signal,
                    time_range=args.time_range,
                    technology=args.technology,
                    top=args.top,
                    launch=not args.prepare_only,
                    render_only=args.render_only,
                )
            case "inspect":
                result = h.inspect_project()
            case "context":
                result = h.get_operational_context(args.detail)
            case "workspace":
                result = h.workspace_status()
            case "actions":
                result = h.list_actions()
            case "run" | "run-until":
                result = h.submit(args.target, workflow=args.command == "run-until", force=args.force)
                if args.wait:
                    result = wait(h, result["run_id"])
            case "wait":
                result = wait(h, args.run_id)
            case "get-run":
                result = h.get_run(args.run_id)
            case "cancel":
                result = h.cancel_run(args.run_id)
            case "recover":
                result = h.recover_runs()
            case "metrics":
                result = h.get_metrics(args.state)
            case "diagnose":
                result = h.get_diagnostics(args.state)
            case "artifacts":
                result = h.get_artifacts(args.state, args.type)
            case "states":
                result = [
                    {k: s[k] for k in ("id", "parent_state_ids", "stage", "created_at")}
                    for s in h.store.list("state")
                ]
            case "compare":
                result = h.compare_states(args.state_ids)
            case "checkout":
                result = h.checkout_state(args.state_id, args.force)
            case "goal":
                result = h.create_goal(
                    args.description, json.loads(args.constraints), args.require, args.baseline
                )
            case "export-artifact":
                result = h.export_artifact(args.artifact_id, args.destination)
            case "read-artifact":
                result = h.read_artifact(args.artifact_id, args.offset)
        emit(result)
        if isinstance(result, dict) and result.get("status") in {
            "FAILED",
            "TIMEOUT",
            "CANCELLED",
            "VIEWER_UNAVAILABLE",
            "LAUNCH_FAILED",
        }:
            sys.exit(1)
        if isinstance(result, dict) and result.get("design_status") == "BLOCKED":
            sys.exit(2)
    except (ValueError, OSError) as error:
        print(json.dumps({"error": str(error)}, ensure_ascii=False), file=sys.stderr)
        sys.exit(1)


def wait(harness, run_id):
    while True:
        run = harness.get_run(run_id)
        if run["status"] in TERMINAL:
            return run
        time.sleep(0.2)


if __name__ == "__main__":
    main()
