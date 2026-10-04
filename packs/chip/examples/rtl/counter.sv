module counter(input logic clk, input logic reset, output logic [3:0] count);
  always_ff @(posedge clk)
    if (reset) count <= 0;
    else count <= count + 1;
endmodule
