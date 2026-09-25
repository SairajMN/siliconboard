`timescale 1ns/1ps

module tb_up_counter8;
    // Clock and reset
    reg clk;
    reg reset_n;
    reg enable;
    wire [7:0] count;

    // Instantiate DUT
    up_counter8 dut (
        .clk(clk),
        .reset_n(reset_n),
        .enable(enable),
        .count(count)
    );

    // Clock generation: 10 ns period
    initial begin
        clk = 0;
    end
    always #5 clk = ~clk; // toggle every 5 ns => 10 ns period

    // Testbench stimulus and self‑checking
    integer checks;
    integer failed;
    // task for checking values
    task check;
        input [8*32-1:0] name; // up to 32 characters
        input [7:0] observed;
        input [7:0] expected;
        begin
            checks = checks + 1;
            if (observed === expected) begin
                $display("CHECK %s PASS", name);
            end else begin
                failed = failed + 1;
                $display("CHECK %s FAIL got=%0d want=%0d", name, observed, expected);
            end
        end
    endtask

    initial begin
        // initialise all driven regs at time 0
        reset_n = 0; // active low reset asserted
        enable   = 0;
        checks = 0;
        failed = 0;

        // Hold reset for two clock cycles
        @(negedge clk);
        @(negedge clk);
        // De‑assert reset
        reset_n = 1;
        // After reset release, count should be 0
        @(negedge clk);
        check("reset_zero", count, 8'd0);

        // Nominal operation: enable counting
        enable = 1;
        // Let counter run three cycles, expect count = 3
        repeat (3) @(negedge clk);
        check("inc_by_3", count, 8'd3);

        // Edge case: saturation at 255
        // Run enough cycles to reach 255 (currently 3)
        repeat (252) @(negedge clk);
        check("sat_at_255", count, 8'd255);
        // One more cycle, count should stay at 255
        @(negedge clk);
        check("sat_hold", count, 8'd255);

        // Disable enable, counter should hold value (still 255)
        enable = 0;
        @(negedge clk);
        check("hold_disable", count, 8'd255);

        // Summary and verdict
        $display("SUMMARY checks=%0d failed=%0d", checks, failed);
        if (failed == 0) begin
            $display("PASS");
        end else begin
            $display("FAIL");
        end
        $finish;
    end
endmodule
