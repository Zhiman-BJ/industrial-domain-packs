"""Versioned, strict parameters for built-in semantic operations."""

from typing import Annotated, Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator


class ParameterModel(BaseModel):
    model_config = ConfigDict(extra="forbid")

    def nondefault_fields(self):
        return {
            name
            for name in self.model_fields_set
            if getattr(self, name) != type(self).model_fields[name].get_default(call_default_factory=True)
        }


class InputRef(ParameterModel):
    path: str | None = None
    action: str | None = None
    artifact: str | None = None

    @model_validator(mode="after")
    def one_source(self):
        if not (
            (self.path is not None and self.action is None and self.artifact is None)
            or (self.path is None and self.action and self.artifact)
        ):
            raise ValueError("Supply path OR action + artifact")
        return self


class LintParameters(ParameterModel):
    operation: Literal["lint"]
    sources: list[InputRef] = Field(min_length=1)
    top: str


class SynthesisParameters(ParameterModel):
    operation: Literal["synthesize", "elaborate", "check_netlist", "prepare_lvs_reference"]
    sources: list[InputRef] = Field(min_length=1)
    top: str
    liberty: InputRef | None = None
    flatten: bool = False
    frontend: Literal["verilog", "slang"] = "verilog"
    includes: list[InputRef] = Field(default_factory=list)
    defines: dict[str, str] = Field(default_factory=dict)
    top_parameters: dict[str, int] = Field(default_factory=dict)
    technology_maps: list[InputRef] = Field(default_factory=list)
    cell_models: list[InputRef] = Field(default_factory=list)
    spice_models: list[InputRef] = Field(default_factory=list)
    mapped: bool = False
    power_net: str = "VDD"
    ground_net: str = "VSS"

    @model_validator(mode="after")
    def validate_frontend_options(self):
        import re

        for name in [*self.defines, *self.top_parameters, self.power_net, self.ground_net]:
            if not re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]*", name):
                raise ValueError("Macro, parameter and supply names must be identifiers")
        for value in self.defines.values():
            if not re.fullmatch(r"[A-Za-z0-9_.'+-]+", value):
                raise ValueError("Macro values must be simple HDL tokens")
        if self.operation == "prepare_lvs_reference" and not (self.cell_models and self.spice_models):
            raise ValueError("LVS reference conversion requires explicit Verilog interfaces and SPICE models")
        if self.operation != "prepare_lvs_reference" and self.nondefault_fields() & {
            "spice_models",
            "power_net",
            "ground_net",
        }:
            raise ValueError("SPICE model and supply options require prepare_lvs_reference")
        return self


class SimulationParameters(ParameterModel):
    operation: Literal["simulate"]
    sources: list[InputRef] = Field(min_length=1)
    top: str
    trace: bool = True
    output_split: int = Field(default=10000, ge=100)
    output_split_cfuncs: int = Field(default=500, ge=10)


class EquivalenceParameters(ParameterModel):
    operation: Literal["equivalence"]
    gold_sources: list[InputRef] = Field(min_length=1)
    gate_sources: list[InputRef] = Field(min_length=1)
    cell_models: list[InputRef] = Field(default_factory=list)
    cell_liberties: list[InputRef] = Field(default_factory=list)
    top: str
    engine: Literal["yosys", "eqy"] = "yosys"
    memory_mode: Literal["map"] = "map"
    depth: int = Field(default=10, ge=1, le=100)
    partition_timeout_seconds: int = Field(default=60, ge=1)
    solver: Literal["z3", "cvc5", "boolector"] = "z3"

class ExtractionParameters(ParameterModel):
    operation: Literal["extract"]
    layout: InputRef
    technology: InputRef | None = None
    builtin_technology: Literal["scmos"] | None = None
    top: str

    @model_validator(mode="after")
    def technology_required(self):
        if (self.technology is None) == (self.builtin_technology is None):
            raise ValueError("Supply technology OR builtin_technology")
        return self


class RuleParameters(ParameterModel):
    rule_format: Literal["ruby", "macro"] = "ruby"
    variables: dict[str, str | InputRef] = Field(default_factory=dict)
    layout_variable: str = "in_gds"
    report_variable: str = "report_file"
    reference_variable: str = "cdl_file"

    @model_validator(mode="after")
    def validate_variables(self):
        import re

        reserved = [self.layout_variable, self.report_variable, self.reference_variable]
        if len(set(reserved)) != len(reserved):
            raise ValueError("Rule binding variable names must be distinct")
        for name in [*self.variables, *reserved]:
            if not re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]*", name):
                raise ValueError("Rule variables must be identifiers")
        if set(reserved) & self.variables.keys():
            raise ValueError("Use typed layout/reference bindings, not reserved variables")
        if self.rule_format == "ruby" and self.variables:
            raise ValueError("Variables require native macro format")
        return self


class LVSParameters(RuleParameters):
    operation: Literal["lvs"]
    layout: InputRef
    reference: InputRef
    top: str
    reference_top: str | None = None
    rules: InputRef | None = None


class DRCParameters(RuleParameters):
    operation: Literal["drc"]
    layout: InputRef
    rules: InputRef


class WaveformParameters(ParameterModel):
    operation: Literal["waveform"]
    waveform: InputRef
    format: Literal["vcd", "fst"]


class TimingParameters(ParameterModel):
    corner: str | None = Field(default=None, pattern=r"^[A-Za-z_][A-Za-z0-9_]*$")
    min_libraries: list[InputRef] = Field(default_factory=list)
    operation: Literal["sta", "power"]
    database: InputRef
    libraries: list[InputRef] = Field(min_length=1)
    constraints: InputRef
    parasitics: InputRef


class PhysicalParameters(ParameterModel):
    operation: Literal["floorplan", "place", "cts", "route"]
    top: str
    libraries: list[InputRef] = Field(min_length=1)
    constraints: InputRef
    database: InputRef | None = None
    netlist: InputRef | None = None
    lefs: list[InputRef] = Field(default_factory=list)
    technology_setup: InputRef | None = None
    site: str | None = None
    die_area: tuple[float, float, float, float] | None = None
    core_area: tuple[float, float, float, float] | None = None
    pin_layers: tuple[str, str] | None = None
    density: float = Field(default=0.5, gt=0, le=1, allow_inf_nan=False)
    clock_buffers: list[str] = Field(default_factory=list)
    routing_layers: tuple[str, str] | None = None
    extraction_rules: InputRef | None = None

    @model_validator(mode="after")
    def required_by_stage(self):
        import math
        import re

        if self.operation == "floorplan":
            if not all((self.netlist, self.lefs, self.site, self.die_area, self.core_area, self.pin_layers)):
                raise ValueError("floorplan needs netlist, LEFs, site, die/core areas and pin layers")
            for area in (self.die_area, self.core_area):
                if not all(math.isfinite(v) for v in area) or area[0] >= area[2] or area[1] >= area[3]:
                    raise ValueError("Invalid die/core rectangle")
            d, c = self.die_area, self.core_area
            if not (d[0] <= c[0] < c[2] <= d[2] and d[1] <= c[1] < c[3] <= d[3]):
                raise ValueError("Core must be inside die")
        elif self.database is None:
            raise ValueError("Physical stages after floorplan require database")
        if self.operation == "cts" and not self.clock_buffers:
            raise ValueError("CTS requires technology-specific clock buffers")
        if self.operation == "route" and (not self.routing_layers or not self.extraction_rules):
            raise ValueError("Route requires routing layers and extraction rules")
        names = [*self.clock_buffers, *(self.pin_layers or ()), *(self.routing_layers or ())]
        if self.site:
            names.append(self.site)
        if any(not re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]*", name) for name in names):
            raise ValueError("Technology names must be simple identifiers")
        return self


class StreamoutParameters(ParameterModel):
    operation: Literal["streamout"]
    top: str
    layout: InputRef
    technology: InputRef
    lefs: list[InputRef] = Field(min_length=1)
    libraries: list[InputRef] = Field(min_length=1)
    dbu: float = Field(gt=0, allow_inf_nan=False)


class DatabaseParameters(ParameterModel):
    database: InputRef
    top: str
    libraries: list[InputRef] = Field(default_factory=list)
    constraints: InputRef | None = None
    technology_setup: InputRef | None = None
    parasitics: InputRef | None = None


class ExportParameters(DatabaseParameters):
    operation: Literal["export_design"]
    formats: list[Literal["odb", "def", "verilog", "sdc", "spef"]] = Field(min_length=1)
    include_power_ground: bool = True

    @model_validator(mode="after")
    def export_requirements(self):
        if len(self.formats) != len(set(self.formats)):
            raise ValueError("Duplicate export format")
        if "sdc" in self.formats and self.constraints is None:
            raise ValueError("SDC export requires constraints")
        if "spef" in self.formats and self.parasitics is None:
            raise ValueError("SPEF export requires supplied parasitics; extraction is a separate operation")
        return self


class RepairParameters(DatabaseParameters):
    operation: Literal["repair_design", "repair_timing", "repair_tie_fanout", "repair_antennas"]
    mode: Literal["setup", "hold"] = "setup"
    margin: float = Field(default=0, ge=0, allow_inf_nan=False)
    max_utilization: float = Field(default=80, gt=0, le=100, allow_inf_nan=False)
    tie_port: str | None = None
    separation: float = Field(default=0, ge=0, allow_inf_nan=False)
    diode_cell: str | None = None
    iterations: int = Field(default=1, ge=1, le=100)
    routing_layers: tuple[str, str] | None = None

    @model_validator(mode="after")
    def repair_requirements(self):
        if not self.libraries or self.constraints is None or self.technology_setup is None:
            raise ValueError(
                "Repair requires libraries, constraints and RC technology setup for updated estimates"
            )
        if self.operation == "repair_tie_fanout" and not self.tie_port:
            raise ValueError("Tie repair requires library cell/port")
        if self.operation == "repair_antennas" and not (self.diode_cell and self.routing_layers):
            raise ValueError("Antenna repair requires diode cell and routing layers")
        common = set(DatabaseParameters.model_fields) | {"operation"}
        options = {
            "repair_design": {"max_utilization"},
            "repair_timing": {"mode", "margin", "max_utilization"},
            "repair_tie_fanout": {"tie_port", "separation"},
            "repair_antennas": {"diode_cell", "iterations", "routing_layers"},
        }
        if extra := self.nondefault_fields() - common - options[self.operation]:
            raise ValueError(f"Options do not apply to {self.operation}: {sorted(extra)}")
        return self


class PowerConnection(ParameterModel):
    net: str
    pin_pattern: str
    instance_pattern: str = ".*"
    kind: Literal["power", "ground"]


class Stripe(ParameterModel):
    layer: str
    width: float = Field(gt=0, allow_inf_nan=False)
    pitch: float = Field(gt=0, allow_inf_nan=False)
    offset: float = Field(default=0, ge=0, allow_inf_nan=False)
    followpins: bool = False


class PhysicalUtilityParameters(DatabaseParameters):
    operation: Literal["connect_power", "generate_pdn", "insert_tapcells", "insert_fillers"]
    connections: list[PowerConnection] = Field(default_factory=list)
    power_net: str = "VDD"
    ground_net: str = "VSS"
    stripes: list[Stripe] = Field(default_factory=list)
    connect_layers: list[tuple[str, str]] = Field(default_factory=list)
    tapcell: str | None = None
    endcap: str | None = None
    distance: float = Field(default=20, gt=0, allow_inf_nan=False)
    fillers: list[str] = Field(default_factory=list)

    @model_validator(mode="after")
    def utility_requirements(self):
        if self.operation == "connect_power" and not self.connections:
            raise ValueError("Power connection requires explicit connection rules")
        if self.operation == "generate_pdn" and not (
            self.connections and self.stripes and self.connect_layers
        ):
            raise ValueError("PDN requires connections, stripes and layer connections")
        if self.operation == "insert_tapcells" and not (self.tapcell or self.endcap):
            raise ValueError("Tap/endcap insertion requires at least one master")
        if self.operation == "insert_fillers" and not self.fillers:
            raise ValueError("Filler insertion requires masters")
        common = set(DatabaseParameters.model_fields) | {"operation"}
        options = {
            "connect_power": {"connections"},
            "generate_pdn": {"connections", "power_net", "ground_net", "stripes", "connect_layers"},
            "insert_tapcells": {"tapcell", "endcap", "distance"},
            "insert_fillers": {"fillers"},
        }
        if extra := self.nondefault_fields() - common - options[self.operation]:
            raise ValueError(f"Options do not apply to {self.operation}: {sorted(extra)}")
        return self


class TimingCheckParameters(DatabaseParameters):
    operation: Literal["check_constraints", "check_design_rules"]

    @model_validator(mode="after")
    def timing_requirements(self):
        if not self.libraries or self.constraints is None:
            raise ValueError("Timing checks require libraries and constraints")
        if (
            self.operation == "check_design_rules"
            and self.parasitics is None
            and self.technology_setup is None
        ):
            raise ValueError("Design-rule checks require parasitics or RC technology setup")
        return self


Parameters = Annotated[
    LintParameters
    | SynthesisParameters
    | SimulationParameters
    | EquivalenceParameters
    | ExtractionParameters
    | LVSParameters
    | DRCParameters
    | WaveformParameters
    | TimingParameters
    | PhysicalParameters
    | StreamoutParameters
    | ExportParameters
    | RepairParameters
    | PhysicalUtilityParameters
    | TimingCheckParameters,
    Field(discriminator="operation"),
]
