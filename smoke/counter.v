module counter #(
    parameter WIDTH = 8
) (
    input  wire             clk,
    input  wire             rst_n,
    input  wire             en,
    output reg  [WIDTH-1:0] q
);

    always @(posedge clk) begin
        if (!rst_n)
            q <= {WIDTH{1'b0}};
        else if (en)
            q <= q + 1'b1;
    end

endmodule
