"""Scoped PCB transport over the locked official MCP SDK.

This gateway never executes CAD or starts another agent loop. The separately
isolated upstream controller owns native actions, evidence and mutation receipts.
"""
import asyncio
import base64
from contextlib import asynccontextmanager
from datetime import timedelta
import hashlib
import importlib.metadata
import json
import os
from pathlib import Path
import sys
import uuid

from jsonschema import Draft202012Validator
from mcp import ClientSession, StdioServerParameters, types
from mcp.client.stdio import stdio_client
from mcp.server.lowlevel import Server
from mcp.server.stdio import stdio_server

MAX_ARGUMENTS = 64 * 1024
MAX_TEXT = 16 * 1024
MAX_CACHE = 4 * 1024 * 1024
CALL_TIMEOUT = 45  # Harness transport policy; not copied from benchmark budgets.
STARTUP_TIMEOUT = 120  # A cold amd64 image can start slowly on an arm64 development host.


def backend_parameters(policy, policy_file):
    args = ["run", "--rm", "--pull", "never", "-i", "--init", "--platform", "linux/amd64", "--user", "0:0",
            "--security-opt", "no-new-privileges", "--workdir", "/workspace"]
    # Execute immutable image sources, not a mutable host checkout. The checkout
    # supplies the host-side Skill; the controller verifies image resources too.
    mounts = [(policy["projectDir"], "/workspace", False),
              (policy["controller"], "/harness/pcb-controller.py", True),
              (str(policy_file), "/harness/policy.json", True)]
    if policy.get("requirementsPath"):
        mounts.append((policy["requirementsPath"], "/public/requirements.json", True))
    for source, target, readonly in mounts:
        args.extend(["--mount", f"type=bind,src={source},dst={target}" + (",readonly" if readonly else "")])
    args.extend(["--env", "PYTHONDONTWRITEBYTECODE=1", "--env", "PCB_TOOL_GROUPS=",
                 "--entrypoint", "/usr/bin/python3", policy["imageId"], "-I", "/harness/pcb-controller.py", "/harness/policy.json"])
    environment = {key: os.environ[key] for key in ("PATH", "HOME", "TMPDIR", "SYSTEMROOT", "WINDIR", "DOCKER_HOST", "DOCKER_CONTEXT", "DOCKER_CONFIG") if key in os.environ}
    return StdioServerParameters(command=policy["docker"], args=args, env=environment)


def schema(name, properties, required=()):
    return types.Tool(name=name, description={
        "domain_tool_list": "Discover a page of allowed canonical PCB tools; load a selected schema separately.",
        "domain_tool_describe": "Read one allowed native PCB schema. The project and runtime are bound by the Harness.",
        "domain_tool_call": "Call an allowed PCB Domain Runtime tool. Supply arguments as an object, or use argumentsJson containing a JSON object when your model cannot express nested booleans or arrays. Mutations require MCP approval. Execution success is not acceptance; do not retry an ambiguous mutation.",
        "domain_tool_result_read": "Read a page of a large response retained by this gateway session.",
    }[name], inputSchema={"type": "object", "properties": properties, "required": list(required), "additionalProperties": False})


SURFACE = [
    schema("domain_tool_list", {"offset": {"type": "integer", "minimum": 0}, "limit": {"type": "integer", "minimum": 1, "maximum": 30}}),
    schema("domain_tool_describe", {"toolId": {"type": "string"}}, ("toolId",)),
    schema("domain_tool_call", {"toolId": {"type": "string"}, "arguments": {"type": "object"}, "argumentsJson": {"type": "string"}}, ("toolId",)),
    schema("domain_tool_result_read", {"responseId": {"type": "string"}, "offset": {"type": "integer", "minimum": 0}, "limit": {"type": "integer", "minimum": 1, "maximum": 4000}}, ("responseId",)),
]


def create_gateway(policy, policy_file):
    if policy.get("schemaVersion") != 1 or not policy.get("allowedToolIds"):
        raise ValueError("Invalid PCB gateway policy")
    descriptors = {item["id"]: item for item in policy["tools"]}
    if len(descriptors) != len(policy["tools"]) or not set(policy["allowedToolIds"]) <= descriptors.keys():
        raise ValueError("Invalid PCB tool allowlist")
    allowed = {key: descriptors[key] for key in policy["allowedToolIds"]}
    project = Path(policy["projectDir"]).resolve(strict=True)
    cache = Path(policy["cacheDir"])
    cache.mkdir(mode=0o700, parents=True, exist_ok=True)
    cache.chmod(0o700)
    for stale in cache.glob("*.json"):
        stale.unlink()
    responses = {}
    cache_bytes = 0

    def output(data, error=False, images=()):
        return types.CallToolResult(content=[types.TextContent(type="text", text=json.dumps(data, ensure_ascii=False)), *images],
                                    structuredContent=data, isError=error)

    def bounded(value):
        nonlocal cache_bytes
        raw = json.dumps(value, ensure_ascii=False, separators=(",", ":"))
        size = len(raw.encode())
        if size <= MAX_TEXT:
            return value
        if size > MAX_CACHE:
            raise ValueError("PCB response exceeds the gateway limit. Inspect controller receipts before retrying a mutation.")
        while responses and cache_bytes + size > MAX_CACHE:
            key = next(iter(responses))
            file, old_size, _ = responses.pop(key)
            cache_bytes -= old_size
            file.unlink(missing_ok=True)
        ident = str(uuid.uuid4())
        file = cache / (ident + ".json")
        with file.open("w") as stream:
            file.chmod(0o600)
            stream.write(raw)
        responses[ident] = (file, size, hashlib.sha256(raw.encode()).hexdigest())
        cache_bytes += size
        return {"providerId": policy["id"], "projectDir": str(project), "responseId": ident,
                "paged": True, "bytes": size, "characters": len(raw), "sha256": responses[ident][2],
                "nextStep": "Use domain_tool_result_read. Transport pagination does not establish native CAD observation coverage."}

    def require(tool_id):
        if tool_id not in allowed:
            raise ValueError("Tool is outside the current Broker scope.")
        return allowed[tool_id]

    @asynccontextmanager
    async def lifespan(_server):
        if importlib.metadata.version("mcp") != policy["mcpVersion"]:
            raise RuntimeError("PCB gateway MCP SDK differs from the declared lock.")
        async with stdio_client(backend_parameters(policy, policy_file)) as (read, write):
            async with ClientSession(read, write, read_timeout_seconds=timedelta(seconds=STARTUP_TIMEOUT)) as client:
                initialized = await client.initialize()
                if initialized.serverInfo.name != "pcb-atomic-tools" or initialized.serverInfo.version != "1.0.0":
                    raise RuntimeError("PCB upstream server identity differs from the registered backend.")
                page = await client.list_tools()
                if page.nextCursor or len(page.tools) != len(descriptors) or {t.name for t in page.tools} != {t["name"] for t in descriptors.values()}:
                    raise RuntimeError("PCB live tool surface differs from the registered snapshot.")
                definitions = [{"type": "function", "function": {"name": t.name, "description": t.description, "parameters": t.inputSchema}} for t in page.tools]
                digest = hashlib.sha256(json.dumps(definitions, sort_keys=True).encode()).hexdigest()
                if digest != policy["toolSchemaSha256"]:
                    raise RuntimeError("PCB live tool schemas differ from the registered snapshot.")
                yield {"client": client, "tools": {t.name: t for t in page.tools}}

    server = Server("industrial-pcb-gateway", lifespan=lifespan)

    @server.list_tools()
    async def list_tools():
        return SURFACE

    @server.call_tool()
    async def call_tool(name, arguments):
        try:
            descriptor = next((t for t in SURFACE if t.name == name), None)
            if descriptor is None:
                raise ValueError("Unknown gateway tool.")
            Draft202012Validator(descriptor.inputSchema).validate(arguments)
            if Path(policy["projectDir"]).resolve(strict=True) != project or not project.is_dir():
                raise ValueError("Bound PCB project changed; reconnect the gateway.")
            state = server.request_context.lifespan_context
            if name == "domain_tool_list":
                offset, limit = arguments.get("offset", 0), arguments.get("limit", 20)
                entries = list(allowed.values())
                items = [{"toolId": t["id"], "nativeName": t["name"], "summary": t["summary"], "risk": t["risk"]} for t in entries[offset:offset + limit]]
                return output({"providerId": policy["id"], "projectDir": str(project), "tools": items,
                               "total": len(entries), "nextOffset": offset + len(items) if offset + len(items) < len(entries) else None})
            if name == "domain_tool_result_read":
                record = responses.get(arguments["responseId"])
                if record is None:
                    raise ValueError("Unknown response in this gateway session.")
                file, _, digest = record
                raw = file.read_text()
                offset, limit = arguments.get("offset", 0), arguments.get("limit", 4000)
                if offset > len(raw):
                    raise ValueError("Response offset is out of range.")
                text = raw[offset:offset + limit]
                # JSON escaping can exceed a byte cap even for 4000 characters.
                while len(json.dumps({"text": text}, ensure_ascii=False).encode()) > MAX_TEXT - 1024:
                    text = text[:max(1, len(text) // 2)]
                return output({"responseId": arguments["responseId"], "text": text, "sha256": digest,
                               "nextOffset": offset + len(text) if offset + len(text) < len(raw) else None})
            item = require(arguments["toolId"])
            native = state["tools"][item["name"]]
            if name == "domain_tool_describe":
                return output(bounded({"toolId": item["id"], "nativeName": item["name"], "risk": item["risk"],
                                       "description": native.description, "inputSchema": native.inputSchema}))
            if "arguments" in arguments and "argumentsJson" in arguments:
                raise ValueError("Supply either arguments or argumentsJson, not both.")
            if "argumentsJson" in arguments:
                raw = arguments["argumentsJson"]
                if len(raw.encode()) > MAX_ARGUMENTS:
                    raise ValueError("PCB tool arguments exceed the gateway limit.")
                def unique_object(pairs):
                    result = {}
                    for key, value in pairs:
                        if key in result:
                            raise ValueError("argumentsJson contains a duplicate key.")
                        result[key] = value
                    return result
                def reject_constant(value):
                    raise ValueError("argumentsJson contains a non-JSON number: " + value)
                try:
                    supplied = json.loads(raw, object_pairs_hook=unique_object, parse_constant=reject_constant)
                except ValueError as error:
                    raise ValueError("argumentsJson must contain a JSON object.") from error
                if not isinstance(supplied, dict):
                    raise ValueError("argumentsJson must contain a JSON object.")
            else:
                supplied = arguments.get("arguments", {})
            if len(json.dumps(supplied, ensure_ascii=False).encode()) > MAX_ARGUMENTS:
                raise ValueError("PCB tool arguments exceed the gateway limit.")
            Draft202012Validator(native.inputSchema).validate(supplied)
            if item["name"] == "inspect_tool" and supplied.get("name") in {t["name"] for t in descriptors.values()} and supplied["name"] not in {t["name"] for t in allowed.values()}:
                raise ValueError("Requested native tool contract is outside the current Broker scope.")
            try:
                result = await asyncio.wait_for(state["client"].call_tool(item["name"], supplied), timeout=CALL_TIMEOUT)
            except (TimeoutError, asyncio.TimeoutError) as error:
                raise ValueError("PCB transport timed out. Native mutation outcome may be unknown; inspect controller receipts. No automatic resubmission.") from error
            texts, images, visual = [], [], []
            image_bytes = 0
            for block in result.content:
                if block.type == "text":
                    try:
                        texts.append(json.loads(block.text))
                    except ValueError:
                        texts.append(block.text)
                elif block.type == "image":
                    data = base64.b64decode(block.data, validate=True)
                    image_bytes += len(data)
                    if block.mimeType != "image/png" or len(data) > 4 * 1024 * 1024 or image_bytes > 10 * 1024 * 1024 or len(visual) >= 4:
                        raise ValueError("PCB image response exceeds the declared image boundary; inspect receipts before retrying.")
                    delivered = bool(policy.get("imageInput"))
                    visual.append({"mimeType": block.mimeType, "bytes": len(data), "sha256": hashlib.sha256(data).hexdigest(), "delivered": delivered})
                    if delivered:
                        images.append(block)
                else:
                    raise ValueError("Unsupported PCB result content type; inspect native receipts before retrying.")
            value = {"providerId": policy["id"], "projectDir": str(project), "toolId": item["id"],
                     "result": result.structuredContent if result.structuredContent is not None else (texts[0] if len(texts) == 1 else texts),
                     "isError": bool(result.isError), "images": visual, "requirementsBound": bool(policy.get("requirementsSha256"))}
            return output(bounded(value), bool(result.isError), images)
        except Exception as error:
            return output({"error": str(error)}, True)

    return server


async def main():
    file = Path(sys.argv[1]).resolve(strict=True)
    policy = json.loads(file.read_text())
    server = create_gateway(policy, file)
    async with stdio_server() as (read, write):
        await server.run(read, write, server.create_initialization_options())


if __name__ == "__main__":
    asyncio.run(main())
