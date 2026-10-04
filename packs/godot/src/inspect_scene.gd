extends SceneTree

const PREFIX := "HARNESS_SCENE_JSON:"
const PROPERTY_NAMES := [
    "position", "size", "scale", "modulate", "anchor_left", "anchor_top",
    "anchor_right", "anchor_bottom", "offset_left", "offset_top",
    "offset_right", "offset_bottom", "grow_horizontal", "grow_vertical",
    "custom_minimum_size", "collision_layer", "collision_mask", "region_enabled",
    "region_rect", "autoplay", "animation", "text"
]

func _initialize() -> void:
    var args := OS.get_cmdline_user_args()
    if args.size() != 1:
        _finish({"ok": false, "error": "Expected one project-relative .tscn path."}, 2)
        return
    var packed := load("res://" + args[0]) as PackedScene
    if packed == null:
        _finish({"ok": false, "error": "Scene could not be loaded."}, 2)
        return
    var root := packed.instantiate()
    if root == null:
        _finish({"ok": false, "error": "Scene could not be instantiated."}, 2)
        return
    var nodes: Array[Dictionary] = []
    _visit(root, ".", nodes)
    root.free()
    _finish({"ok": true, "scene": args[0], "nodes": nodes, "truncated": nodes.size() >= 256}, 0)

func _visit(node: Node, node_path: String, nodes: Array[Dictionary]) -> void:
    if nodes.size() >= 256:
        return
    var available := {}
    for property in node.get_property_list():
        available[property.name] = true
    var properties := {}
    for name in PROPERTY_NAMES:
        if available.has(name):
            properties[name] = str(node.get(name)).substr(0, 256)
    var row := {"path": node_path, "type": node.get_class(), "properties": properties}
    if node is AnimatedSprite2D and node.sprite_frames != null:
        var animations := {}
        for animation_name in node.sprite_frames.get_animation_names():
            var frames: Array[Dictionary] = []
            for index in range(mini(node.sprite_frames.get_frame_count(animation_name), 64)):
                var texture: Texture2D = node.sprite_frames.get_frame_texture(animation_name, index)
                var frame := {"texture": str(texture).substr(0, 256)}
                if texture is AtlasTexture:
                    frame["region"] = str(texture.region)
                    frame["atlas_path"] = texture.atlas.resource_path
                frames.append(frame)
            animations[animation_name] = {"loop": node.sprite_frames.get_animation_loop(animation_name), "frames": frames}
        row["sprite_animations"] = animations
    if node is CollisionShape2D and node.shape != null:
        row["shape_type"] = node.shape.get_class()
        if node.shape is RectangleShape2D:
            row["shape_size"] = str(node.shape.size)
    nodes.append(row)
    for child in node.get_children():
        _visit(child, node_path + "/" + child.name, nodes)

func _finish(value: Dictionary, code: int) -> void:
    print(PREFIX + JSON.stringify(value))
    quit(code)
