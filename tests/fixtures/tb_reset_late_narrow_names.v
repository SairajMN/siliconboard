`timescale 1ns/1ps

module tb_up_counter;

    reg clk;
    reg rst_n;
    reg en;
    wire [7:0] count;

    integer checks = 0;
    integer failed = 0;

    up_counter dut (
        .clk(clk),
        .rst_n(rst_n),
        .en(en),
        .count(count)
    );

    initial begin
        clk = 0;
        rst_n = 0;
        en = 0;
    end

    always #5 clk = ~clk;

    task wait_negedge;
        begin
            @(negedge clk);
        end
    endtask

    task check_value(input [7:0] expected, input [8*8-1:0] name);
        begin
            checks = checks + 1;
            if (count !== expected) begin
                failed = failed + 1;
                $display("CHECK %0s FAIL got=%0d want=%0d", name, count, expected);
            end else begin
                $display("CHECK %0s PASS", name);
            end
        end
    endtask

    initial begin
        // Wait for initial state to settle
        wait_negedge;

        // 1. Reset check
        // rst_n=0, en=0. Counter should be 0.
        check_value(8'd0, "reset_initial");

        // 2. Enable check
        // Set en=1, rst_n=1. Counter should increment.
        en = 1;
        wait_negedge; // Edge 1: count becomes 1
        check_value(8'd1, "enable_increment_1");

        wait_negedge; // Edge 2: count becomes 2
        check_value(8'd2, "enable_increment_2");

        // 3. Disable check
        // Set en=0. Counter should hold.
        en = 0;
        wait_negedge; // Edge 3: count holds at 2
        check_value(8'd2, "disable_hold_1");

        wait_negedge; // Edge 4: count holds at 2
        check_value(8'd2, "disable_hold_2");

        // 4. Reset precedence check
        // Set rst_n=0, en=1. Reset should take precedence, count becomes 0.
        rst_n = 0;
        wait_negedge; // Edge 5: count becomes 0
        check_value(8'd0, "reset_precedence");

        // 5. Re-enable after reset
        // Set rst_n=1, en=1. Counter should increment from 0.
        rst_n = 1;
        wait_negedge; // Edge 6: count becomes 1
        check_value(8'd1, "re_enable_increment");

        // 6. Boundary check (255 -> 0 wrap)
        // Force count to 255 by enabling for 254 more cycles.
        // Current count is 1. Need 254 increments to reach 255.
        // Then one more increment to wrap to 0.
        // Total 255 cycles.
        // Let's just run 255 cycles.
        // Current count is 1. After 254 cycles, count is 255.
        // After 255 cycles, count is 0.
        // Let's do 254 cycles to get to 255.
        repeat (254) begin
            wait_negedge;
        end
        check_value(8'd255, "boundary_255");

        // One more cycle to wrap to 0
        wait_negedge;
        check_value(8'd0, "boundary_wrap_0");

        // 7. Back-to-back operations
        // Enable, disable, enable quickly.
        en = 0;
        wait_negedge; // Hold at 0
        check_value(8'd0, "back_to_back_hold");

        en = 1;
        wait_negedge; // Increment to 1
        check_value(8'd1, "back_to_back_inc");

        en = 0;
        wait_negedge; // Hold at 1
        check_value(8'd1, "back_to_back_hold2");

        // Final summary
        $display("SUMMARY checks=%0d failed=%0d", checks, failed);
        if (failed == 0) begin
            $display("PASS");
        end else begin
            $display("FAIL");
        end
        $finish;
    end

endmodule
