// Ephemeral evidence from a successful job in this workflow attempt, not a
// durable qualification or a resource reservation. Missing/mismatched evidence
// always falls back to a fresh registry check.
const immutable = (reference) => reference?.replace(/:[^/:@]+(?=@sha256:)/, "");
function workflow(env) {
  return [env.GITHUB_REPOSITORY, env.GITHUB_RUN_ID, env.GITHUB_RUN_ATTEMPT];
}
function imageEvidence(candidate, image, registry, env) {
  return {
    workflow: workflow(env),
    domain: candidate.domain,
    repository: candidate.repository,
    buildInputDigest: candidate.input.buildInputDigest,
    image,
    registry,
  };
}
function consumeEvidence(record, candidate, env) {
  const scope = workflow(env);
  if (
    !record ||
    scope.some((v) => !v) ||
    JSON.stringify(scope) !== JSON.stringify(record.workflow)
  )
    return null;
  const { image, registry } = record;
  const identity = registry?.identity;
  const reference = image?.reference;
  if (
    record.domain !== candidate.domain ||
    record.repository !== candidate.repository ||
    record.buildInputDigest !== candidate.input.buildInputDigest ||
    image?.platform !== candidate.input.platform ||
    typeof reference !== "string" ||
    !reference.startsWith(candidate.repository + "@sha256:") ||
    !/^sha256:[a-f0-9]{64}$/.test(reference.split("@")[1] || "") ||
    identity?.digest !== reference.split("@")[1] ||
    identity?.platform !== candidate.input.platform ||
    identity?.verification !== "metadata-only" ||
    (candidate.image && immutable(candidate.image.reference) !== reference)
  )
    return null;
  return { image, registry };
}
module.exports = { imageEvidence, consumeEvidence };
