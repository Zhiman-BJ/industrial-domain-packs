const { exact, number } = require("./support.cjs");
function dimension(type) {
  if (typeof type !== "string") return null;
  if (type.endsWith("3D")) return 3;
  if (type.endsWith("2D")) return 2;
  return null;
}
function vector(value, type) {
  exact(value, ["type", "value"]);
  const size = type === "Vector3" ? 3 : 2;
  if (
    value.type !== type ||
    !Array.isArray(value.value) ||
    value.value.length !== size
  )
    throw Error("Use a typed " + type + " value.");
  return type + "(" + value.value.map(number).join(", ") + ")";
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
    let replacement;
    if (change.property === "visible") {
      replacement = "visible = " + change.value;
    } else if (change.property === "size") {
      replacement = "size = " + vector(change.value, "Vector3");
    } else {
      const header = sections[matches[0]].split("\n")[0];
      const type = header.match(/\btype="(\w+)"/)?.[1];
      const size = dimension(type);
      if (!size)
        throw Error(
          "Transforms require a Node2D or Node3D family node type, not " +
            type +
            ".",
        );
      if (change.property === "rotation_degrees" && size === 2)
        throw Error(
          "Node2D rotation_degrees is outside the supported scene edit surface.",
        );
      const expected = size === 3 ? "Vector3" : "Vector2";
      replacement =
        change.property + " = " + vector(change.value, expected);
    }
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
