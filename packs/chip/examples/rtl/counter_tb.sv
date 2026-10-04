module counter_tb;
  logic clk = 0;
  logic reset = 1;
  logic [3:0] count;
  counter dut(.*);
  always #5 clk = ~clk;
  initial begin
    $dumpfile("wave.vcd");
    $dumpvars(0, counter_tb);
    #11;
    assert(count == 0) else $fatal(1, "reset failed");
    reset = 0;
    #40;
    assert(count == 4) else $fatal(1, "counter failed");
    $finish;
  end
endmodule
