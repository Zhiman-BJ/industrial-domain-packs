// The publisher owns input-* tags in the shared personal CCR namespace.
const prefix = "ccr.ccs.tencentyun.com/industrial-mcp-remote/";
function repositoryFor(domain) {
  const names = {
    chip: "chip-rtl",
    freecad: "freecad-worker",
    pcb: "pcb-kicad",
  };
  if (!Object.hasOwn(names, domain)) throw Error("Unknown image domain");
  return prefix + names[domain];
}
function tcrClient() {
  const { AbstractClient } = require("tencentcloud-sdk-nodejs-common");
  return new AbstractClient("tcr.tencentcloudapi.com", "2019-09-24", {
    credential: {
      secretId: process.env.TENCENT_SECRET_ID,
      secretKey: process.env.TENCENT_SECRET_KEY,
    },
    region: "ap-guangzhou",
    profile: { httpProfile: { reqTimeout: 30 } },
  });
}
async function findImage({ repository, input, client = tcrClient() }) {
  if (
    !repository.startsWith(prefix) ||
    !/^[a-z0-9_-]+$/.test(repository.slice(prefix.length))
  )
    throw Error("Image outside allowed namespace");
  if (!/^sha256:[a-f0-9]{64}$/.test(input.buildInputDigest))
    throw Error("Invalid build input identity");
  const tag = "input-" + input.buildInputDigest.slice(7),
    matches = [];
  let offset = 0;
  for (let page = 0; page < 100; page++) {
    const { Data: d } = await client.request("DescribeImagePersonal", {
      RepoName: repository.split("/").slice(1).join("/"),
      Offset: offset,
      Limit: 100,
    });
    if (
      !Array.isArray(d?.TagInfo) ||
      !Number.isSafeInteger(d.TagCount) ||
      d.TagCount < 0
    )
      throw Error("Invalid image list");
    matches.push(...d.TagInfo.filter((t) => t.TagName === tag));
    offset += d.TagInfo.length;
    if (offset >= d.TagCount) {
      if (matches.length > 1) throw Error("Ambiguous image identity");
      if (!matches.length) return null;
      const hash = matches[0].TagId;
      if (!/^sha256:[a-f0-9]{64}$/.test(hash))
        throw Error("Invalid registry image digest");
      return { reference: repository + "@" + hash, platform: input.platform };
    }
    if (!d.TagInfo.length) throw Error("Incomplete image pagination");
  }
  throw Error("Image pagination limit exceeded");
}
module.exports = { findImage, repositoryFor, tcrClient };
