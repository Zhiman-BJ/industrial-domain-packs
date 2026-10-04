const fs = require("node:fs");
const path = require("node:path");
const crypto = require("node:crypto");
const { execFileSync } = require("node:child_process");
const root = path.resolve(__dirname, "..");
const hash = (bytes) => crypto.createHash("sha256").update(bytes).digest("hex");
// Include tracked and deliberately staged new sources; never local caches.
const names = execFileSync("git", ["ls-files", "-z", "--", "packs", "lib"], {
  cwd: root,
})
  .toString()
  .split("\0")
  .filter(Boolean)
  .sort();
const files = {};
for (const name of names) {
  if (!fs.lstatSync(path.join(root, name)).isFile())
    throw Error("Regular sources required: " + name);
  files[name] = hash(fs.readFileSync(path.join(root, name)));
}
const value =
  JSON.stringify(
    { schemaVersion: 1, contentSha256: hash(JSON.stringify(files)), files },
    null,
    2,
  ) + "\n";
const target = path.join(root, "content-lock.json");
if (process.argv.includes("--check")) {
  if (fs.readFileSync(target, "utf8") !== value)
    throw Error("Update content-lock.json after source changes.");
} else fs.writeFileSync(target, value);

{
  const packageFile = path.join(root, "package.json");
  const metadata = JSON.parse(fs.readFileSync(packageFile, "utf8"));
  const expectedFiles = [
    ...names,
    "content-lock.json",
    "LICENSE",
    "THIRD_PARTY_NOTICES.md",
    "licenses",
    "provenance",
    "validate.sh",
    ".dockerignore",
  ];
  if (process.argv.includes("--check")) {
    if (JSON.stringify(metadata.files) !== JSON.stringify(expectedFiles))
      throw Error("Package file inventory differs from the release lock.");
  } else {
    metadata.files = expectedFiles;
    fs.writeFileSync(packageFile, JSON.stringify(metadata, null, 2) + "\n");
  }
}
