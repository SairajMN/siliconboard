`timescale 1ns/1ps

module tb_counter;

    reg        clk = 0;
    reg        rst_n = 0;
    reg        en = 0;
    wire [7:0] q;
    integer    checks = 0;
    integer    failed = 0;

    counter dut (.clk(clk), .rst_n(rst_n), .en(en), .q(q));

    always #5 clk = ~clk;

    task check;
        input [255:0] name;
        input [7:0]   got;
        input [7:0]   want;
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
        check("reset", q, 8'd0);

        rst_n = 1;
        en = 1;
        @(negedge clk);
        @(negedge clk);
        @(negedge clk);
        check("count_three", q, 8'd3);

        en = 0;
        @(negedge clk);
        @(negedge clk);
        check("hold_when_disabled", q, 8'd3);

        rst_n = 0;
        @(negedge clk);
        check("reset_again", q, 8'd0);

        $display("SUMMARY checks=%0d failed=%0d", checks, failed);
        if (failed == 0)
            $display("PASS");
        else
            $display("FAIL");
        $finish;
    end

endmodule
