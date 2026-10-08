// Godot 4.7.2 emits this macOS TLS bootstrap diagnostic in an offline sandbox.
// It is retained verbatim as evidence; it does not affect scene import/readback.
const bootstrap =
  /ERROR: Condition \"ret != noErr\" is true\. Returning: \"\"\n\s+at: get_system_ca_certificates \(platform\/macos\/os_macos\.mm:1035\)/g;
function completed(result) {
  return (
    result.status === "COMPLETED" &&
    !/(SCRIPT ERROR|ERROR:|Parse Error)/i.test(
      result.log.replace(bootstrap, ""),
    )
  );
}
module.exports = { completed };
