set count [gtkwave::getNumFacs]
if {$count < 1} {exit 1}
set signals {}
for {set i 0} {$i < $count} {incr i} {
    lappend signals [gtkwave::getFacName $i]
}
gtkwave::addSignalsFromList $signals
gtkwave::setZoomRangeTimes 0 20
gtkwave::/File/Write_Save_File wave.gtkw
set f [open wave-checks.json w]
puts $f {{"checks": [{"name": "GTKWave loaded waveform signals", "passed": true}]}}
close $f
gtkwave::/File/Quit
