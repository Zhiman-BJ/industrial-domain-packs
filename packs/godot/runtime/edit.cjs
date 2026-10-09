const { exact, number } = require("./support.cjs");
function value(text) {
  if (typeof text === "boolean") return String(text);
  if (typeof text === "number") return String(number(text));
  exact(text, ["type", "value"]);
  const sizes = { Vector2: 2, Vector3: 3 };
  if (
    !sizes[text.type] ||
    !Array.isArray(text.value) ||
    text.value.length !== sizes[text.type]
  )
    throw Error("Use a typed Vector2 or Vector3 value.");
  return text.type + "(" + text.value.map(number).join(", ") + ")";
}
function edit(source, changes) {
  if (!Array.isArray(changes) || !changes.length || changes.length > 32)
    throw Error("Use 1..32 typed scene changes.");
  if (!source.startsWith("[gd_scene "))
    throw Error("Only text Godot scenes are supported.");
  const sections = source.split(/(?=^\[)/m);
  const seen = new Set();
  for (const change of changes) {
    exact(change, ["section", "property", "value"]);
    if (
      !["position", "rotation_degrees", "scale", "visible", "size"].includes(
        change.property,
      )
    )
      throw Error("Property is outside the supported scene edit surface.");
    if (change.property === "visible" && typeof change.value !== "boolean")
      throw Error("Visibility requires a boolean.");
    if (
      change.property === "size" &&
      (change.value?.type !== "Vector3" ||
        change.value.value?.some((n) => n <= 0))
    )
      throw Error("BoxMesh dimensions require a positive Vector3.");
    const key = change.section + "/" + change.property;
    if (seen.has(key)) throw Error("Duplicate scene change.");
    seen.add(key);
    const matches = sections
      .map((text, index) => {
        const header = text.split("\n")[0];
        const name = header.match(/\bname="([\w .-]+)"/)?.[1],
          parent = header.match(/\bparent="([\w .\/-]+)"/)?.[1];
        const node =
          parent === undefined
            ? "."
            : parent === "."
              ? name
              : parent + "/" + name;
        const resource = header.match(
          /^\[sub_resource type="BoxMesh" id="([\w.-]+)"/,
        )?.[1];
        return (header.startsWith("[node ") &&
          change.section === "node:" + node) ||
          (resource && change.section === "resource:" + resource)
          ? index
          : -1;
      })
      .filter((i) => i >= 0);
    if (matches.length !== 1)
      throw Error("Expected one supported scene section: " + change.section);
    if ((change.property === "size") !== change.section.startsWith("resource:"))
      throw Error(
        "size edits require a BoxMesh subresource; transforms require a node.",
      );
    const replacement = change.property + " = " + value(change.value);
    const index = matches[0],
      lines = sections[index].split("\n"),
      existing = lines.findIndex((x) => x.startsWith(change.property + " = "));
    if (existing >= 0) lines[existing] = replacement;
    else lines.splice(1, 0, replacement);
    sections[index] = lines.join("\n");
  }
  return sections.join("");
}
module.exports = { edit };
