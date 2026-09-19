`timescale 1ns/1ps

module tb_up_counter;

    reg clk;
    reg reset_n;
    reg enable;
    wire [7:0] count;

    integer checks = 0;
    integer failed = 0;

    up_counter dut (
        .clk(clk),
        .reset_n(reset_n),
        .enable(enable),
        .count(count)
    );

    initial begin
        clk = 0;
        reset_n = 0;
        enable = 0;
    end

    always #5 clk = ~clk;

    task check(input [8*31-1:0] name, input [7:0] got, input [7:0] want);
        begin
            checks = checks + 1;
            if (got !== want) begin
                failed = failed + 1;
                $display("CHECK %0s FAIL got=%0d want=%0d", name, got, want);
            end else begin
                $display("CHECK %0s PASS", name);
            end
        end
    endtask

    initial begin
        @(negedge clk);
        check("reset_value", count, 8'd0);

        @(negedge clk);
        enable = 1;

        @(negedge clk);
        check("count_1", count, 8'd1);

        @(negedge clk);
        check("count_2", count, 8'd2);

        @(negedge clk);
        enable = 0;

        @(negedge clk);
        check("hold_1", count, 8'd2);

        @(negedge clk);
        check("hold_2", count, 8'd2);

        @(negedge clk);
        enable = 1;

        @(negedge clk);
        check("resume_1", count, 8'd3);

        @(negedge clk);
        reset_n = 0;

        @(negedge clk);
        check("reset_mid", count, 8'd0);

        @(negedge clk);
        reset_n = 1;

        @(negedge clk);
        check("post_reset", count, 8'd0);

        @(negedge clk);
        check("post_reset_1", count, 8'd1);

        $display("SUMMARY checks=%0d failed=%0d", checks, failed);
        if (failed == 0) begin
            $display("PASS");
        end else begin
            $display("FAIL");
        end
        $finish;
    end

endmodule
