// Read-only PR gate: no image build, Tool mutation or lock writeback.
const path = require("node:path");
const { inputs } = require("./runtime-build.cjs");
const { recipes } = require("./publish-runtime.cjs");
const { findImage, repositoryFor } = require("./runtime-cloud-image.cjs");
const { verifyImage } = require("./runtime-image-reuse.cjs");
async function checkImage(
  { domain, repository, input },
  { find = findImage, verify = verifyImage } = {},
) {
  const image = await find({ repository, input });
  if (!image)
    throw Error(
      `${domain}: image missing for ${input.buildInputDigest}; run npm run publish:images locally on the candidate source, then rerun this check`,
    );
  const evidence = await verify(image, true, true);
  if (
    evidence?.identity?.digest !== image.reference.split("@")[1] ||
    evidence?.identity?.platform !== input.platform ||
    evidence?.identity?.verification !== "metadata-only"
  )
    throw Error(`${domain}: registry image identity mismatch`);
  return image;
}
async function main() {
  const pack = require("../lib/index.cjs");
  pack.verifyIntegrity();
  let failed = false;
  for (const [domain, [packId, , profileId, dockerfile]] of Object.entries(
    recipes,
  )) {
    try {
      const profile = pack
        .getPack(packId)
        .profiles.find((p) => p.id === profileId);
      const input = inputs({
        root: path.resolve(__dirname, ".."),
        recipe: `packs/${domain}/images/${dockerfile}`,
        platform: profile.platform,
      });
      const image = await checkImage({
        domain,
        repository: repositoryFor(domain),
        input,
      });
      console.log(
        `${domain}: ready ${image.reference} (${input.buildInputDigest})`,
      );
    } catch (error) {
      failed = true;
      // Do not dump subprocess/SDK objects, which may contain credentials.
      console.error(`${domain}: ${error.code || error.message}`);
    }
  }
  if (failed)
    throw Error(
      "Runtime images are not ready; see the per-domain errors above",
    );
}
module.exports = { checkImage };
if (require.main === module)
  main().catch((error) => {
    console.error(error.message);
    process.exitCode = 1;
  });
