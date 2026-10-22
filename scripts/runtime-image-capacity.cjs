// Advisory only: a successful publication is not undone by a capacity query.
const fs = require("node:fs");
const path = require("node:path");
async function checkCapacity({ image, client }) {
  const match =
    /^ccr\.ccs\.tencentyun\.com\/(industrial-mcp-remote\/[a-z0-9_-]+)(?=[:@]|$)/.exec(
      image,
    );
  if (!match) throw Error("Image outside allowed namespace");
  const repository = match[1];
  const [quota, images] = await Promise.all([
    client.request("DescribeUserQuotaPersonal", {}),
    client.request("DescribeImagePersonal", {
      RepoName: repository,
      Offset: 0,
      Limit: 1,
    }),
  ]);
  const limit = quota.Data?.LimitInfo?.find((x) => x.Type === "tag")?.Value;
  const used = images.Data?.TagCount;
  if (
    !Number.isSafeInteger(limit) ||
    limit <= 0 ||
    !Number.isSafeInteger(used) ||
    used < 0 ||
    images.Data?.RepoName !== repository
  )
    throw Error("Invalid capacity response");
  return {
    repository,
    used,
    limit,
    remaining: Math.max(0, limit - used),
    warning: used / limit >= 0.8,
  };
}
async function main() {
  let summary;
  try {
    const { AbstractClient } = require("tencentcloud-sdk-nodejs-common");
    const plan = JSON.parse(
      fs.readFileSync(
        path.join(process.env.PUBLICATION_DIRECTORY, "plan.json"),
      ),
    );
    const client = new AbstractClient("tcr.tencentcloudapi.com", "2019-09-24", {
      credential: {
        secretId: process.env.TENCENT_SECRET_ID,
        secretKey: process.env.TENCENT_SECRET_KEY,
      },
      region: "ap-guangzhou",
      profile: { httpProfile: { reqTimeout: 20 } },
    });
    const r = await checkCapacity({
      image: plan.tool.request.CustomConfiguration.Image,
      client,
    });
    const message = `${r.repository}: ${r.used}/${r.limit} tags used; ${r.remaining} remaining.`;
    console.log(message);
    if (r.warning)
      console.log(
        `::warning title=Image repository capacity::${message} Schedule cleanup after checking shared test/prod references.`,
      );
    summary = `### Image repository capacity\n\n${r.warning ? "⚠️ " : ""}${message}\n${r.warning ? "\nCleanup needed: protect shared test/prod image references before deleting tags.\n" : ""}`;
  } catch {
    console.log(
      "::warning title=Image capacity check unavailable::Could not query image capacity; remaining capacity is unknown. Check the capacity-query credentials and Tencent Cloud API availability.",
    );
    summary =
      "### Image repository capacity\n\n⚠️ Capacity check failed; remaining capacity is unknown. Publication is not failed by this advisory check.\n";
  }
  if (process.env.GITHUB_STEP_SUMMARY)
    fs.appendFileSync(process.env.GITHUB_STEP_SUMMARY, summary);
}
module.exports = { checkCapacity };
if (require.main === module)
  main().catch(() => {
    console.log("::warning::Could not write image capacity summary.");
  });
