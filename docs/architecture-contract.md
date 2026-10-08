# IH-ARCH-001: repository boundary

Status: accepted by the product owner on 2026-10-08. The canonical [architecture contract](https://github.com/Zhiman-BJ/industrial-agent-harness/blob/main/doc/architecture-contract.md) is maintained in Harness. Read it before implementation.

This repository is the maintained source for domain capabilities, StateProviders, Skills, tools, input rules, Verifiers, dependency locks, image recipes and qualified profiles. Canonical industrial facts remain defined by Harness. It owns no Desktop/CLI product or agent kernel. Pack-level CLIs/MCP are domain entry points and do not bypass the integrating Runtime. Source archives and qualified execution profiles are distinct deliverables.

Run `npm run test:architecture-contract` before existing relevant tests. The checker is an identical, hash-pinned copy from Harness for IH-ARCH-001; it has no native dependencies. Its base-branch policy rejects new application/kernel dependencies, ownership violations and expansion of registered legacy exceptions. Remote consumption additionally requires a full Git commit and matching lock integrity.

Checker/workflow/policy changes require a separate, explicitly owner-approved architecture revision with rationale, migration and positive/negative evidence. Do not rebaseline a feature to pass. CI uses trusted base-branch code and publishes the result for the candidate commit without executing candidate code in its trusted run. Branch protection must require `Architecture contract` and preserve existing required checks. An unmerged contract PR does not change main behavior.

Current migration remains explicit: local Harness still consumes its older snapshots; Desktop/CLI task orchestration is not fully unified. Reconcile already-verified consumer fixes before replacing copies. Supported profiles still require real native success, failure, cancellation and recovery qualification. This static gate does not establish engineering acceptance or qualify new platforms.
