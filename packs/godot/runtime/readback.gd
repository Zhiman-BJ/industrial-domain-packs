extends SceneTree
var scene: Node
var frames := 0
var limit := 0
var destination := ""
var initial: Array = []
func _initialize() -> void:
    call_deferred("begin")
func value_json(value: Variant) -> Variant:
    if value is Vector2: return [value.x, value.y]
    if value is Vector3: return [value.x, value.y, value.z]
    if value is Color: return [value.r, value.g, value.b, value.a]
    if value is float or value is int or value is bool or value is String: return value
    return null
func nodes_json(node: Node, result: Array) -> void:
    var item := {"path": str(scene.get_path_to(node)), "class": node.get_class(), "properties": {}}
    for prop in ["position", "rotation_degrees", "scale", "visible"]:
        if prop in node: item.properties[prop] = value_json(node.get(prop))
    if node is MeshInstance3D and node.mesh is BoxMesh:
        item.properties["mesh_size"] = value_json(node.mesh.size)
    result.append(item)
    if result.size() > 1000:
        push_error("Scene node limit exceeded")
        quit(2)
        return
    for child in node.get_children(): nodes_json(child, result)
func begin() -> void:
    var args := OS.get_cmdline_user_args()
    if args.size() != 3:
        push_error("Expected scene, output and frame limit")
        quit(2)
        return
    destination = args[1]
    limit = int(args[2])
    var packed := load("res://" + args[0]) as PackedScene
    if packed == null:
        push_error("Scene failed to load")
        quit(2)
        return
    scene = packed.instantiate()
    root.add_child(scene)
    nodes_json(scene, initial)
    if limit == 0: finish()
func _process(_delta: float) -> bool:
    if scene != null and limit > 0:
        frames += 1
        if frames >= limit: call_deferred("finish")
    return false
func finish() -> void:
    var final: Array = []
    nodes_json(scene, final)
    var report := {"schemaVersion": 1, "engine": Engine.get_version_info(), "frames": frames, "initial": initial, "final": final}
    var file := FileAccess.open(destination, FileAccess.WRITE)
    if file == null:
        push_error("Cannot write readback")
        quit(2)
        return
    file.store_string(JSON.stringify(report))
    file.close()
    scene.queue_free()
    quit(0)
