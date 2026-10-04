from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator

from eda_harness.core.parameters import Parameters


class Model(BaseModel):
    model_config = ConfigDict(extra="forbid")


class Resources(Model):
    cpu: int = Field(default=2, ge=1)
    memory_gb: float = Field(default=4, gt=0)
    timeout_seconds: int = Field(default=600, ge=1)
    build_jobs: int = Field(default=1, ge=1)
    pids: int = Field(default=256, ge=16)

    @model_validator(mode="after")
    def bounded_build_jobs(self):
        if self.build_jobs > self.cpu:
            raise ValueError("build_jobs must not exceed the runtime CPU allocation")
        if self.build_jobs > max(1, int(self.memory_gb // 2)):
            raise ValueError("Reserve at least 2 GiB per parallel compiler; use build_jobs: 1 for large netlists")
        return self


class RuntimeConfig(Model):
    kind: Literal["local", "docker"] = "docker"
    image: str | None = None
    resources: Resources = Field(default_factory=Resources)
    require_native: bool = False

    @model_validator(mode="after")
    def check_image(self):
        if self.kind == "docker" and not self.image:
            raise ValueError("Docker runtime requires an image; resolved image ID is recorded on each run")
        return self


class ActionConfig(Model):
    parameters: Parameters | None = None
    runtime: RuntimeConfig | None = None
    script: str | None = None
    outputs: dict[str, str] = Field(default_factory=dict)
    metrics_file: str | None = None
    verification_file: str | None = None
    args: list[str] = Field(default_factory=list)

    @model_validator(mode="after")
    def structured_or_script(self):
        if self.parameters is not None and (
            self.script or self.args or self.outputs or self.metrics_file or self.verification_file
        ):
            raise ValueError(
                "Structured parameters own scripts, outputs and reports; do not mix with script mode"
            )
        return self


class Constraint(Model):
    op: Literal[">=", "<=", "==", ">", "<"]
    value: float = Field(allow_inf_nan=False)
    unit: str | None = None


class ActionSpec(Model):
    id: str = Field(pattern=r"^[a-z][a-z0-9_]*(?:\.[a-z][a-z0-9_]*)+$")
    tool: str
    dependencies: list[str]
    input_types: list[str]
    artifact_types: list[str]


class WorkflowSpec(Model):
    actions: list[ActionSpec] = Field(default_factory=list)
    targets: dict[str, list[str]] = Field(default_factory=dict)


class Project(Model):
    name: str
    top: str
    inputs: dict[str, list[str]] = Field(
        description=(
            "Project-relative file paths/globs by category. Allowed keys are the union of the resolved "
            "workflow action input_types plus config and script; custom action input_types extend this set. "
            "parameters path references must also be captured here; JSON Schema alone cannot enforce this rule."
        )
    )
    runtime: RuntimeConfig
    workflow: WorkflowSpec = Field(default_factory=WorkflowSpec)
    identities: dict[str, str] = Field(default_factory=dict)
    actions: dict[str, ActionConfig] = Field(default_factory=dict)
    constraints: dict[str, Constraint] = Field(default_factory=dict)
    required_verification: list[str] = Field(default_factory=lambda: ["rtl.lint"])

    @model_validator(mode="after")
    def validate_semantics(self):
        from eda_harness.core.workflow import catalog, project_input_categories

        actions = catalog(self.workflow)

        categories = set(project_input_categories(self.workflow))
        unknown = set(self.inputs) - categories
        if unknown:
            raise ValueError(
                f"Unknown input categories: {sorted(unknown)}; allowed categories: {sorted(categories)}; declare custom categories in workflow.actions[].input_types"
            )
        unknown_actions = (set(self.actions) | set(self.required_verification)) - set(actions)
        if unknown_actions:
            raise ValueError(f"Use canonical action IDs in project configuration: {sorted(unknown_actions)}")
        return self


class Metric(Model):
    name: str
    value: float = Field(allow_inf_nan=False)
    unit: str
    evidence: str
    action: str


class VerificationResult(Model):
    action: str
    status: Literal["PASS", "FAIL", "UNKNOWN"]
    summary: str
    evidence: list[str] = Field(default_factory=list)


class Diagnostic(Model):
    details: dict = Field(default_factory=dict)
    category: str
    severity: Literal["info", "medium", "high"]
    summary: str
    evidence: list[str] = Field(default_factory=list)


class GoalState(Model):
    id: str
    description: str
    baseline_state_id: str | None = None
    constraints: dict[str, Constraint]
    required_verification: list[str]


class SourceSnapshot(Model):
    id: str
    project_id: str
    base_state_id: str | None
    files: dict[str, dict[str, str]]
    config: dict
    git: dict
    created_at: str


class Artifact(Model):
    id: str
    type: str
    content_hash: str
    size: int
    producer_run_id: str
    state_id: str | None = None
    path: str
    uri: str


class DesignState(Model):
    id: str
    project_id: str
    parent_state_ids: list[str]
    source_snapshot_id: str
    stage: str
    results: dict
    acceptance: dict
    provenance: dict
    created_at: str
