// Transport-only fixture: it does not claim native GPU qualification.
const { StdioServerTransport } = require('@modelcontextprotocol/sdk/server/stdio.js');
const { createMcpServer } = require('../../packs/cuda/server/mcp.cjs');
const service = { call: async role => ({ ready: true, nativeQualification: 'fixture-only', identity: { role } }) };
createMcpServer(process.argv[2], service).connect(new StdioServerTransport()).catch(error => { process.stderr.write(error.message); process.exitCode = 1; });
