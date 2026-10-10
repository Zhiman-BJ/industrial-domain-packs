"use strict";
// Minimal newline-delimited JSON-RPC stand-in for bridge/pcb-gateway.py.
// Speaks just enough MCP for packs/pcb/runtime/bench-gateway.cjs tests.
const readline = require("node:readline");

const PAGED_DOC = JSON.stringify({
  toolId: "pcb.bench.inspect_board",
  result: { status: "ok", collection: ["x".repeat(40), "y".repeat(40)] },
  isError: false,
});

const rl = readline.createInterface({ input: process.stdin });
const send = (message) =>
  process.stdout.write(JSON.stringify(message) + "\n");

rl.on("line", (line) => {
  if (!line.trim()) return;
  const request = JSON.parse(line);
  if (request.method === "initialize") {
    return send({
      jsonrpc: "2.0",
      id: request.id,
      result: {
        protocolVersion: "2024-11-05",
        capabilities: {},
        serverInfo: { name: "industrial-pcb-gateway", version: "1" },
      },
    });
  }
  if (request.method === "tools/call") {
    const name = request.params.name;
    const arguments_ = request.params.arguments || {};
    if (name === "domain_tool_call" && arguments_.toolId === "pcb.bench.project_status")
      return send({
        jsonrpc: "2.0",
        id: request.id,
        result: {
          content: [
            {
              type: "text",
              text: JSON.stringify({
                providerId: "pcb-bench.tools",
                toolId: "pcb.bench.project_status",
                result: { status: "ok", requirements: "paged" },
                isError: false,
              }),
            },
          ],
          isError: false,
        },
      });
    if (name === "domain_tool_call" && arguments_.toolId === "pcb.bench.inspect_board")
      return send({
        jsonrpc: "2.0",
        id: request.id,
        result: {
          content: [
            {
              type: "text",
              text: JSON.stringify({
                providerId: "pcb-bench.tools",
                responseId: "paged-1",
                paged: true,
                bytes: PAGED_DOC.length,
                characters: PAGED_DOC.length,
                sha256: "0".repeat(64),
              }),
            },
          ],
          isError: false,
        },
      });
    if (name === "domain_tool_call")
      return send({
        jsonrpc: "2.0",
        id: request.id,
        result: {
          content: [{ type: "text", text: JSON.stringify({ error: "outside scope" }) }],
          isError: true,
        },
      });
    if (name === "domain_tool_result_read" && arguments_.responseId === "paged-1") {
      const offset = arguments_.offset || 0;
      const limit = Math.min(arguments_.limit || 4000, 60);
      const text = PAGED_DOC.slice(offset, offset + limit);
      return send({
        jsonrpc: "2.0",
        id: request.id,
        result: {
          content: [
            {
              type: "text",
              text: JSON.stringify({
                responseId: "paged-1",
                text,
                sha256: "0".repeat(64),
                nextOffset:
                  offset + text.length < PAGED_DOC.length ? offset + text.length : null,
              }),
            },
          ],
          isError: false,
        },
      });
    }
    return send({
      jsonrpc: "2.0",
      id: request.id,
      result: {
        content: [{ type: "text", text: JSON.stringify({ error: "unknown surface tool" }) }],
        isError: true,
      },
    });
  }
});
