`timescale 1ns/1ps

module tb_up_counter;

  // Clock generation
  reg clk;
  initial clk = 0;
  always #5 clk = ~clk; // 10 ns period

  // Inputs
  reg reset_n;
  reg enable;

  // Outputs
  wire [7:0] count;

  // Instantiate DUT
  up_counter dut (
    .clk(clk),
    .reset_n(reset_n),
    .enable(enable),
    .count(count)
  );

  // Test control
  integer cycle;
  reg reset_check_done, nominal_check_done, edge_check_done;
  integer failed;

  initial begin
    cycle = 0;
    reset_check_done = 0;
    nominal_check_done = 0;
    edge_check_done = 0;
    failed = 0;

    // Apply reset and enable
    reset_n = 0;
    enable = 1; // enable high from start
    #10 reset_n = 1; // deassert reset after first cycle

    // Wait for all checks to complete
    #3000;
    $display("SUMMARY checks=3 failed=%0d", failed);
    if (failed == 0) $display("PASS"); else $display("FAIL");
    $finish;
  end

  always @(posedge clk) begin
    cycle <= cycle + 1;

    // Reset check at cycle 2
    if (cycle == 2 && !reset_check_done) begin
      if (count == 8'd0) begin
        $display("CHECK reset PASS");
      end else begin
        $display("CHECK reset FAIL");
        failed <= failed + 1;
      end
      reset_check_done <= 1;
    end

    // Nominal check at cycle 6
    if (cycle == 6 && !nominal_check_done) begin
      if (count == 8'd5) begin
        $display("CHECK nominal PASS");
      end else begin
        $display("CHECK nominal FAIL");
        failed <= failed + 1;
      end
      nominal_check_done <= 1;
    end

    // Edge case check at cycle 258
    if (cycle == 258 && !edge_check_done) begin
      if (count == 8'd0) begin
        $display("CHECK edge PASS");
      end else begin
        $display("CHECK edge FAIL");
        failed <= failed + 1;
      end
      edge_check_done <= 1;
    end
  end

endmodule
