# Chip source bootstrap validation

2026-10-04. Exercised locally on macOS arm64 with CPython 3.13.13 and the committed `uv.lock`. These checks qualify the source import and packaging; native image and Kubernetes qualification remain in the migration plan.

| Check | Result |
| --- | --- |
| Public source provenance | All 79 imported files match the fixed public baseline byte for byte |
| Fresh environment | Locked installation succeeded; Python MCP SDK 1.29.1 |
| Imported contract/runtime tests | 29 passed; 6 native cases skipped because this bootstrap does not prepare their tool environment |
| Real stdio MCP | Initialize, discover 25 tools and read server information passed |
| Committed source archive | 81 Pack file hashes and sizes verified; untracked marker and local environments excluded |
| Deterministic packaging | Repeated archives of the same commit have the same SHA-256 |
| Archive consumer | Extracted source performs the same real 25-tool stdio discovery |
| Repository checks | JSON parsing, new document links, JS syntax and staged whitespace checks passed |

The imported tests include synthetic subprocess fixtures. They do not establish engineering acceptance, native tool versions, remote resource limits or a complete protected Agent session. Public CI repeats installation, imported tests, MCP discovery and source packaging on Linux.
