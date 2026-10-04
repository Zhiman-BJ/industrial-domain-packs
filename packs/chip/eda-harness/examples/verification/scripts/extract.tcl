# SCMOS teaching fixture. Projects must supply the technology matching their layout.
load $::env(EDA_INPUT_DIR)/layout/device.mag
select top cell
extract all
ext2spice lvs
ext2spice -o extracted.spice
quit -noprompt
