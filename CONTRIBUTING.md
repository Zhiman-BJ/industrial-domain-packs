# Contributing

This document is for human contributors and reviewers. Coding agents follow
[AGENTS.md](AGENTS.md) plus [packs/AGENTS.md](packs/AGENTS.md); the rules below
add the collaboration workflow.

## What belongs here

Domain semantics only: per-pack ToolDescriptors, typed execution logic, Skills,
Verifiers, dependency locks and image recipes. Canonical Artifact/Action/Run/
State/Verification/Checkpoint contracts, the generic Runtime, Broker and the
Kimi integration are owned by
[industrial-agent-harness](https://github.com/Zhiman-BJ/industrial-agent-harness)
— never introduce competing engineering facts here. Integrations vendor their
complete source into the pack (licenses and provenance included); thin bridges
to external checkouts are not accepted.

Never commit customer inputs, generated engineering runs, credentials, private
deployment endpoints, PDK archives, local Python environments or unreviewed
binaries.

## Setup and validation

Node.js ≥ 24 (npm; this repository does not use pnpm):

```sh
npm ci --ignore-scripts
npm test                          # architecture contract + all node tests
npm run test:architecture-contract
```

Pack-scoped suites — run the ones your change touches (full current matrix:
[README.md](README.md#development-and-validation)):

```sh
uv sync --frozen --directory packs/chip/eda-harness
uv run --frozen --directory packs/chip/eda-harness pytest -q
uv run --frozen --directory packs/chip/eda-harness python ../scripts/mcp-smoke.py
```

## Release identity

Every source change under `packs/` or `lib/` changes the release identity.
Stage the reviewed files, run `npm run lock` in the same commit (it updates
`content-lock.json` and the `package.json` file inventory), and coordinate the
consumer pin bump — Harness and the remote service pin an exact Git commit, so
a pack change lands with its consumer PRs planned. Manifest `version` bumps
and `sourceFiles` hashes follow
[docs/release-format.md](docs/release-format.md); never hand-edit a hash.
Container image releases follow [docs/release-runbook.md](docs/release-runbook.md);
`provenance/runtime-distribution.json` is updated only by the release
workflow's lock PR, never by hand.

## Pull requests

- **Conventional Commits**: `feat|fix|docs|chore|test|refactor(packs): summary`.
- Keep pull requests reviewable: around 1,200 changed lines should be split;
  above roughly 2,000 lines a PR must be split or its description must justify
  why it cannot be.
- State verification evidence per the PR template: the architecture contract
  plus the pack's real success/failure/cancellation/recovery evidence.
  Contract fixtures and exit codes are not engineering acceptance.
- English and Chinese READMEs stay aligned.
- `AGENTS.md`, `architecture/`, the contract checker with its tests and
  workflow, and `.github/CODEOWNERS` are hash-locked in
  `architecture/policy.json`; changing them requires a separate owner-approved
  architecture revision — never bundle that into a feature PR.
