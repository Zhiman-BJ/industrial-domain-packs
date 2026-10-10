"""Stable output contracts for the public PCB tools.

The tool arguments are intentionally tool-specific, but observations share a
small envelope.  Keeping the envelope in one module prevents an ERC/DRC
report, an analysis result, and a delivery result from looking deceptively
similar to a successful CAD mutation.  The schemas describe observations;
they do not grant a verifier any evidence that was not produced by its
backend.
"""
from __future__ import annotations


STATUSES = [
    "PASS", "FAIL", "UNKNOWN", "STALE", "ERROR", "NOT_APPLICABLE",
    "MODIFIED", "GENERATED", "UNCHANGED", "BLOCKED", "ALREADY_PRESENT", "CREATED", "REMOVED", "RENDERED", "STAGED", "FOUND", "NO_LINKS", "COPIED",
]


def _any():
    return {}


def common_observation_schema():
    return {
        "type": "object",
        "required": ["status", "scope", "next_action"],
        "properties": {
            "status": {"type": "string", "enum": STATUSES},
            "ok": {"type": "boolean"},
            "scope": {"type": "string"},
            "issues": {"type": "array", "items": {"anyOf": [{"type":"string"}, {"type":"object"}]}},
            "next_action": {"type": "string"},
            "feedback": {"type": "object"},
            "operation_outcome": {"type": "object"},
            "changed_files": {"type": "array", "items": {"type": "string"}},
            "report_path": {"type": ["string", "null"]},
            "sources": {"type": "object"},
            "board_sha256": {"type": ["string", "null"]},
            "schematic_sha256": {"type": ["string", "null"]},
            "spec_sha256": {"type": ["string", "null"]},
        },
        "additionalProperties": True,
    }


def mutation_output_schema():
    schema = common_observation_schema()
    schema["properties"].update({
        "before": {"type": "object"},
        "after": {"type": "object"},
        "changes": {"type": "array"},
        "verification_required": {"type": "boolean"},
    })
    schema["description"] = (
        "Atomic edit observation. A modified result requires the named "
        "verification stage; it does not imply that stage passed."
    )
    return schema


def verification_output_schema(tool_name: str = "verification"):
    schema = common_observation_schema()
    # Native checks promote ``available``/``report_valid`` when a CLI report
    # exists. Composite checks do not have one native report, so those fields
    # are optional for them.
    if tool_name in {"run_drc", "run_erc"}:
        schema["required"] = ["status", "scope", "next_action", "available", "report_valid"]
    schema["properties"].update({
        "available": {"type": "boolean"},
        "report_valid": {"type": "boolean"},
        "counts": {"type": "object"},
        "violations": {"type": "array", "items": {"type": "object"}},
        "warnings": {"type": "array"},
        "blockers": {"type": "array"},
        "checks": {"type": "object"},
        "check_kind": {"type": "string"},
        "source_path": {"type": "string"},
        "source_sha256": {"type": "string"},
        "report": {"type": "object"},
    })
    if tool_name == 'verify_schematic':
        schema['required'] += ['checks','electrical_status','qualification_scope']
        schema['properties']['checks'] = {'type':'object','required':['cad','schematic_requirements'],
            'properties':{name:{'type':'object','required':['status']} for name in ('cad','schematic_requirements')}}
        schema['properties']['electrical_status'] = {'const':'UNKNOWN',
            'description':'This stage does not evaluate full board electrical qualification; consult verify_design.'}
        schema['properties']['qualification_scope'] = {'type':'string'}
    schema["description"] = (
        f"Fresh {tool_name} observation. PASS means only this check's "
        "declared scope passed; it is not a functional or product sign-off."
    )
    return schema


def stage_output_schema(tool_name: str = "stage"):
    schema = common_observation_schema()
    schema["properties"].update({
        "blocked": {"type": ["string", "null"]},
        "checks": {"type": "object"},
        "affected_nets": {"type": "array"},
        "revision": {"type": "object"},
        "candidate": {"type": ["string", "null"]},
        "candidate_sha256": {"type": ["string", "null"]},
    })
    schema["description"] = (
        f"{tool_name} stage result. Stage completion and candidate creation "
        "are separate from acceptance."
    )
    return schema


def analysis_output_schema():
    schema = stage_output_schema("analysis")
    schema["properties"].update({
        "categories": {"type": "object"},
        "tests": {"type": "object"},
        "backend": {"type": "object"},
        "inputs": {"type": "object"},
        "conditions_sha256": {"type": ["string", "null"]},
    })
    return schema


def delivery_output_schema():
    schema = common_observation_schema()
    schema["properties"].update({
        "artifacts": {"type": "array", "items": {"type": "object"}},
        "artifact_hashes": {"type": "object"},
        "manifest": {"type": "object"},
        "included_3d": {"type": "boolean"},
    })
    schema["description"] = (
        "Export observation. File generation does not certify manufacturing, "
        "electrical, thermal, RF or EMC qualification."
    )
    return schema


def schema_for(tool_name: str):
    """Return the public observation contract for *tool_name*.

    Unknown names deliberately receive the common contract rather than an
    implementation schema.  ``inspect_tool`` still rejects unknown public
    tool names before calling this function.
    """
    if tool_name == 'inspect_schematic':
        from tools.schematic_observation import output_schema
        return output_schema()
    if tool_name in {"run_drc", "run_erc", "verify_schematic", "check_component_datasheet",
                     "check_power_tree", "check_interface", "check_footprints",
                     "compare_schematic_pcb", "export_netlist", "verify_design",
                     "project_status"}:
        return verification_output_schema(tool_name)
    if tool_name in {"run_analysis"}:
        return analysis_output_schema()
    if tool_name in {"generate_schematic", "generate_pcb", "update_pcb", "route_board",
                     "checkout_board", "fill_zones", "view_design"}:
        return stage_output_schema(tool_name)
    if tool_name in {"export_project", "save_project_copy", "export_3d", "prepare_3d_model",
                     "finalize_claims"}:
        return delivery_output_schema()
    return mutation_output_schema()
