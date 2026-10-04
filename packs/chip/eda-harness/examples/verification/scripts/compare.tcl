set extracted [file join $::env(EDA_DEPS_DIR) layout.extract netlist.extracted]
set reference [file join $::env(EDA_INPUT_DIR) reference device.spice]
set left [netgen::readnet spice $extracted]
set right [netgen::readnet spice $reference]
lvs [list device $left] [list device $right] nosetup lvs.log
set matched [expr {[netgen::verify unique] == 1}]
set f [open checks.json w]
puts $f [format {{"checks": [{"name": "Netgen unique device and connectivity match", "passed": %s}]}} [expr {$matched ? "true" : "false"}]]
close $f
quit
