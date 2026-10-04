import json
from importlib.resources import files
from pathlib import Path
from urllib.parse import quote, unquote

from mcp.server.fastmcp import FastMCP
from mcp.server.fastmcp.exceptions import ToolError

from eda_harness.core.service import Harness


class HarnessMCP(FastMCP):
    """Reject unsupported filters/options instead of silently changing a request's meaning."""

    async def list_tools(self):
        tools = await super().list_tools()
        for tool in tools:
            tool.inputSchema["additionalProperties"] = False
        return tools

    async def call_tool(self, name, arguments):
        tool = next((tool for tool in await self.list_tools() if tool.name == name), None)
        if tool is not None:
            extra = set(arguments) - set(tool.inputSchema.get("properties", {}))
            if extra:
                raise ToolError(f"Unknown arguments for {name}: {', '.join(sorted(extra))}")
        return await super().call_tool(name, arguments)


def create_server(project_root=None, read_only=False):
    # An explicit CLI --project may provide a compatibility default. Plugin startup provides none.
    default_project = str(Path(project_root).resolve()) if project_root is not None else None

    def resolve_project(project_path=None, required=True):
        selected = project_path if project_path is not None else default_project
        if selected is None:
            if not required:
                return None
            raise ToolError(
                json.dumps(
                    {
                        "code": "PROJECT_REQUIRED",
                        "project_path": None,
                        "next_step": "Pass the intended absolute project_path; no startup directory is selected.",
                    }
                )
            )
        path = Path(selected).expanduser()
        if not path.is_absolute():
            raise ToolError(json.dumps({"code": "ABSOLUTE_PROJECT_PATH_REQUIRED", "project_path": selected}))
        return str(path.resolve())

    def h(project_path=None):
        root = resolve_project(project_path)
        if not (Path(root) / "eda.yaml").is_file():
            raise ToolError(
                json.dumps(
                    {
                        "code": "PROJECT_NOT_INITIALIZED",
                        "project_path": root,
                        "directory_exists": Path(root).is_dir(),
                        "next_step": "Confirm this is the intended directory. Use initialize_project for a new project, or pass the existing project's path.",
                    }
                )
            )
        return Harness(root)

    from eda_harness.server.identity import capture_identity

    identity = capture_identity(default_project, read_only)
    server = HarnessMCP(
        "EDA Harness",
        instructions=(
            "First call get_tool_guide or read eda://manual/tools. "
            "Project tools take absolute project_path per call; never assume the startup directory. "
            "For version, wrapper or reconnect issues call get_server_info before drawing conclusions. "
            "For setup/environment questions call check_environment even without eda.yaml. "
            "Do not infer Docker EDA readiness from host PATH; viewers are optional host software. "
            "Inspect context, edit working copy, run semantic EDA actions, then check acceptance. "
            "Execution SUCCESS is not acceptance PASS."
        ),
    )

    @server.tool()
    async def get_server_info() -> dict:
        """Read this session's identity without a project: version, VCS evidence, process and config.

        Launcher information is declared, not verified. Unknown commit does not mean old code.
        Compare live tools and session IDs before/after client reload. No arbitrary environment
        dump, process killing, configuration changes or software installation is performed.
        """
        return {**identity, "available_tools": sorted(t.name for t in await server.list_tools())}

    @server.tool()
    def check_environment(
        target: str | None = None,
        image: str | None = None,
        probe_capabilities: bool = False,
        project_path: str | None = None,
    ) -> dict:
        """Diagnose project/runtime readiness without creating state; works without eda.yaml.

        Without eda.yaml checks Docker and the candidate image inventory (default eda-harness-tools:dev).
        image selects a candidate only before initialization; environment_ready is separate from project ready.
        With a project, without target checks configuration and default runtime only. With target also checks
        declared inputs, action runtime overrides and required executable availability.
        probe_capabilities=true additionally checks native commands/plugins/helpers for the target.
        Returns ready, scoped checks, failure codes, next steps and optional host viewers.
        Does not install software, pull images, run project jobs or prove acceptance.
        """
        from eda_harness.core.environment import check_environment as check

        return {
            **check(resolve_project(project_path, required=False), target, image, probe_capabilities),
            "mcp_server": {"reachable": True},
        }

    @server.tool()
    def initialize_project(
        top: str,
        name: str = "design",
        image: str | None = None,
        local: bool = False,
        project_path: str | None = None,
    ) -> dict:
        """Initialize the explicitly supplied project_path after checking Docker and image tools.

        Default: Docker with eda-harness-tools:dev, or the supplied image. Missing host viewers
        do not block initialization. Only set local=true when the user explicitly chooses host
        EDA execution; local tools must then be checked for the target. No installation/pull is
        performed. Never overwrites eda.yaml. After creation add PDK/inputs/flow configuration
        and call check_environment(target=...). INITIALIZED does not mean execution-ready.
        """
        from eda_harness.core.initialization import initialize_project as initialize

        return initialize(resolve_project(project_path), top, name, image, local)

    @server.tool()
    def tool_capabilities() -> dict:
        """Discover operation and project schemas, dynamic input category rules, backends and rule contracts."""
        from eda_harness.plugins.semantic import capabilities

        return capabilities()

    @server.tool()
    def viewer_capabilities() -> dict:
        """Discover viewers: default yosys_show and yosys_viz use project runtime Yosys/Graphviz; netlistsvg and GUI viewers use the MCP host."""
        from eda_harness.viewers import viewer_capabilities as discover

        return discover()

    @server.tool()
    def open_viewer(
        artifact_id: str,
        viewer: str | None = None,
        companion_ids: list[str] | None = None,
        state_id: str | None = None,
        signals: list[str] | None = None,
        time_range: list[float] | None = None,
        technology: str | None = None,
        top: str | None = None,
        launch: bool = True,
        render_only: bool = False,
        project_path: str | None = None,
    ) -> dict:
        """Export immutable artifacts and launch a viewer on the MCP server's local desktop.

        Requires an installed host viewer. launch=false only exports a launch bundle unless render_only=true.
        Netlist viewers require netlist.json (Yosys JSON); top selects a module. Default yosys_show
        and optional yosys_viz use project runtime Yosys/Graphviz; netlistsvg is an optional host renderer.
        Verilog must first be elaborated/synthesized to JSON. render_only=true renders SVG without a browser;
        RENDERED confirms SVG generation, not design correctness. Rendering has a 60-second timeout.
        Companions: GTKWave .gtkw; KLayout GDS/OAS for reports; Magic child MAG;
        OpenROAD ordered LEFs for DEF. Magic requires technology and GDS requires top.
        GTKWave time_range uses dump time units. LAUNCHED is not proof of GUI loading.
        Remote desktops are not supported. Does not affect verification or acceptance.
        """
        from eda_harness.viewers import open_viewer as open_local

        return open_local(
            h(project_path),
            artifact_id,
            viewer,
            companion_ids=companion_ids,
            state_id=state_id,
            signals=signals,
            time_range=time_range,
            technology=technology,
            top=top,
            launch=launch,
            render_only=render_only,
        )

    @server.tool()
    def preflight(target: str, project_path: str | None = None) -> dict:
        """Check structured input references, backend support and runtime availability before submission."""
        return h(project_path).preflight(target)

    @server.tool()
    def inspect_project(project_path: str | None = None) -> dict:
        """Inspect persisted state, working copy, active runs, and current acceptance."""
        return h(project_path).inspect_project()

    @server.tool()
    def get_operational_context(detail: str = "standard", project_path: str | None = None) -> dict:
        """Recover EDA context after session loss. Detail: brief, standard, or deep."""
        return h(project_path).get_operational_context(detail)

    @server.tool()
    def workspace_status(project_path: str | None = None) -> dict:
        """Hash current files and compute stale downstream results; hooks are not required."""
        return h(project_path).workspace_status()

    @server.tool()
    def list_actions(project_path: str | None = None) -> list[dict]:
        """List actions and readiness. input_types are valid inputs.<category> keys; allowed_input_categories also includes shared config/script. Capture files in inputs before referencing them in parameters."""
        return h(project_path).list_actions()

    @server.tool()
    def run_action(action: str, force: bool = False, project_path: str | None = None) -> dict:
        """Snapshot and submit one action. Returns durable run ID immediately; dependencies must be valid."""
        return h(project_path).submit(action, force=force)

    @server.tool()
    def run_until(target: str, force: bool = False, project_path: str | None = None) -> dict:
        """Submit dependency DAG up to an action, post_route_sta, or final_verify using one frozen snapshot."""
        return h(project_path).submit(target, workflow=True, force=force)

    @server.tool()
    def get_run(run_id: str, project_path: str | None = None) -> dict:
        """Get execution status separately from design status, step history, and evidence IDs."""
        return h(project_path).get_run(run_id)

    @server.tool()
    def cancel_run(run_id: str, project_path: str | None = None) -> dict:
        """Request cancellation; the worker terminates the process/container and preserves history."""
        return h(project_path).cancel_run(run_id)

    @server.tool()
    def recover_runs(project_path: str | None = None) -> dict:
        """Mark runs whose worker no longer exists as failed, preserving completed states for resubmission."""
        return h(project_path).recover_runs()

    @server.tool()
    def get_metrics(state_id: str | None = None, project_path: str | None = None) -> list[dict]:
        """Get normalized metrics including units and evidence from an immutable state."""
        return h(project_path).get_metrics(state_id)

    @server.tool()
    def get_diagnostics(
        state_id: str | None = None, category: str | None = None, project_path: str | None = None
    ) -> list[dict]:
        """Get compact deterministic diagnostics; fetch raw evidence only when needed."""
        return h(project_path).get_diagnostics(state_id, category)

    @server.tool()
    def get_artifacts(
        state_id: str | None = None, type: str | None = None, project_path: str | None = None
    ) -> list[dict]:
        """Query typed, content-addressed artifacts rather than searching run directories."""
        artifacts = h(project_path).get_artifacts(state_id, type)
        prefix = "eda://project/" + quote(resolve_project(project_path), safe="") + "/artifact/"
        return [{**a, "uri": prefix + a["id"]} for a in artifacts]

    @server.tool()
    def read_artifact(
        artifact_id: str, offset: int = 0, limit: int = 16000, project_path: str | None = None
    ) -> dict:
        """Read bounded artifact content; large reports can be paginated."""
        return h(project_path).read_artifact(artifact_id, offset, limit)

    @server.tool()
    def compare_states(state_ids: list[str], project_path: str | None = None) -> dict:
        """Compare normalized metrics with unit-aware deltas across branches."""
        return h(project_path).compare_states(state_ids)

    @server.tool()
    def checkout_state(state_id: str, force: bool = False, project_path: str | None = None) -> dict:
        """Materialize a state's sources; dirty files require force and get a recovery snapshot."""
        return h(project_path).checkout_state(state_id, force)

    @server.tool()
    def create_goal(
        description: str,
        constraints: dict,
        required_verification: list[str] | None = None,
        baseline_state_id: str | None = None,
        project_path: str | None = None,
    ) -> dict:
        """Persist acceptance constraints {metric: {op,value,unit?}}, required checks, and baseline."""
        return h(project_path).create_goal(description, constraints, required_verification, baseline_state_id)

    @server.tool()
    def record_decision(
        base_state_id: str,
        result_state_id: str | None,
        change_summary: str,
        outcome: str,
        project_path: str | None = None,
    ) -> dict:
        """Record experiment facts and KEEP/REJECT/INCONCLUSIVE outcome, not private reasoning."""
        return h(project_path).record_decision(base_state_id, result_state_id, change_summary, outcome)

    @server.resource("eda://project/{project_path}/state/{state_id}")
    def state_resource(project_path: str, state_id: str) -> str:
        return json.dumps(h(unquote(project_path)).state(state_id))

    @server.resource("eda://project/{project_path}/run/{run_id}")
    def run_resource(project_path: str, run_id: str) -> str:
        return json.dumps(h(unquote(project_path)).get_run(run_id))

    @server.resource("eda://project/{project_path}/artifact/{artifact_id}")
    def artifact_resource(project_path: str, artifact_id: str) -> str:
        return json.dumps(h(unquote(project_path)).read_artifact(artifact_id))

    @server.resource("eda://project/{project_path}/report/{artifact_id}")
    def report_resource(project_path: str, artifact_id: str) -> str:
        return json.dumps(h(unquote(project_path)).read_artifact(artifact_id))

    @server.resource("eda://project/{project_path}/diagnostics/{state_id}")
    def diagnostics_resource(project_path: str, state_id: str) -> str:
        return json.dumps(h(unquote(project_path)).get_diagnostics(state_id))

    @server.resource("eda://manual/tools", mime_type="text/markdown")
    def tool_manual() -> str:
        return files("eda_harness.server").joinpath("tool-guide.md").read_text(encoding="utf-8")

    @server.tool()
    async def get_tool_guide(tool: str | None = None) -> dict:
        """Read bundled tool documentation, examples, errors and live input schemas. No project required.

        Omit tool for the full guide and available tool names; supply a tool name for its
        live schema. In read-only mode mutating tools are unavailable. Start here before
        using run_action/run_until; poll get_run and check acceptance separately.
        """
        available = {item.name: item for item in await server.list_tools()}
        if tool is not None and tool not in available:
            raise ValueError(f"Unknown or unavailable tool: {tool}")
        return {
            "manual": tool_manual(),
            "available_tools": sorted(available),
            "tool": available[tool].model_dump(mode="json") if tool else None,
            "read_only": read_only,
        }

    if read_only:
        for name in (
            "run_action",
            "run_until",
            "cancel_run",
            "recover_runs",
            "checkout_state",
            "create_goal",
            "record_decision",
            "open_viewer",
            "initialize_project",
        ):
            server.remove_tool(name)
    return server
