const descriptor = {
  schemaVersion: "1",
  id: "cad.freecad.geometry",
  version: "1.1.4-pack.9",
  risk: "mutating",
  verification: ["cad.freecad.geometry"],
};

// Keep the canonical geometry checks in index.cjs. The protected service
// adapter loads this module only after the profile has passed Linux/amd64
// qualification; the pack remains qualification-pending meanwhile.
const { verify } = require("./index.cjs");

module.exports = { descriptor, verifier: verify };
