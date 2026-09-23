// Stage 1 smoke test. Trivial on purpose -- just enough to check that the
// simulator, cocotb and coverage all talk to each other. Not the DUT for the
// experiment; that gets picked in Stage 2.
//
// Verilog-2001, so Verilator and Icarus both take it without language flags.
module counter #(
    parameter WIDTH = 8
) (
    input  wire             clk,
    input  wire             rst_n,     // active-low asynchronous reset
    input  wire             en,        // count enable
    input  wire             load,      // synchronous load (takes priority over en)
    input  wire [WIDTH-1:0] load_val,
    output reg  [WIDTH-1:0] count,
    output wire             wrapped    // high on the cycle the counter rolls over
);

    assign wrapped = en && !load && (count == {WIDTH{1'b1}});

    always @(posedge clk or negedge rst_n) begin
        if (!rst_n) begin
            count <= {WIDTH{1'b0}};
        end else if (load) begin
            count <= load_val;
        end else if (en) begin
            count <= count + 1'b1;
        end
    end

endmodule
