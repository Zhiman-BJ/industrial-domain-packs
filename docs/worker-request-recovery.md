# Worker request recovery capability

The shared HTTP wrapper adds `POST /v1/worker/executions` and authenticated
`GET /v1/worker/executions/{requestId}`. The legacy blocking
`POST /v1/worker/invoke` remains available. Invocation schema version 1 is
unchanged; callers must explicitly select the new capability after qualifying
an image built from the updated Pack source.

Submission validates the same server-created scope, pinned runtime and immutable
snapshot as blocking invocation. It atomically records `working`, starts the
executor independently of the client connection, and returns 202. The same ID and
identical complete input return the recorded state with 200; different input
returns `REQUEST_ID_CONFLICT`. A concurrent execution returns
`INVOCATION_IN_FLIGHT`. Queries return `working`, `completed` (with the existing
observation envelope), or `unknown`, plus the original request/runtime/snapshot
identity and input digest. An absent record returns `EXECUTION_NOT_FOUND`; it is
not permission to repeat an uncertain mutation.

Execution records remain in the sandbox work directory. Atomic file replacement
keeps concurrent readers from observing partial JSON. Uncollected records are
never pruned by the legacy retention path. `WORKER_MAX_WORK_DIRS` bounds their
count; exhausted retention returns `RESULT_RETENTION_FULL`. The service should
collect and durably commit the observation, then retire the sandbox. There is no
acknowledge/delete endpoint or automatic re-execution. Credentials are accepted
only in the data-plane authorization header, never invocation JSON.

This capability recovers a lost service connection while the worker survives.
It does not recover a terminated worker, executor, instance or lost filesystem.
No new native profile or image qualification is claimed. The Remote Service
consumer's real Python HTTP tests exercise acceptance, duplicate/conflicting
input, authenticated query and independent completion using a synthetic executor;
Linux/native failure, cancellation and service crash gates remain prerequisites
for enabling a published image.
