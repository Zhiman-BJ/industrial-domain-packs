const fs = require("node:fs"),
  path = require("node:path");
const domains = ["chip", "freecad", "pcb"];
function publicationMatrix(directory, runId) {
  return domains.filter((domain) => {
    const check = JSON.parse(
      fs.readFileSync(
        path.join(directory, `runtime-check-${domain}-${runId}`, "check.json"),
      ),
    );
    if (check.domain !== domain || typeof check.needsPublication !== "boolean")
      throw Error("Invalid publication check: " + domain);
    return check.needsPublication;
  });
}
module.exports = { publicationMatrix };
if (require.main === module) {
  const selected = publicationMatrix(
    process.env.PUBLICATION_DIRECTORY,
    process.env.GITHUB_RUN_ID,
  );
  fs.appendFileSync(
    process.env.GITHUB_OUTPUT,
    `domains=${JSON.stringify(selected)}\nhas-changes=${selected.length > 0}\n`,
  );
  fs.appendFileSync(
    process.env.GITHUB_STEP_SUMMARY,
    `## Publication check\n\nNeeds publication: ${selected.join(", ") || "none (all resources reused)"}.\n`,
  );
}
