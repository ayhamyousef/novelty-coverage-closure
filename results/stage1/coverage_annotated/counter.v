//      // verilator_coverage annotation
        // Stage 1 smoke test. Trivial on purpose -- just enough to check that the
        // simulator, cocotb and coverage all talk to each other. Not the DUT for the
        // experiment; that gets picked in Stage 2.
        //
        // Verilog-2001, so Verilator and Icarus both take it without language flags.
        module counter #(
            parameter WIDTH = 8
        ) (
 001223     input  wire             clk,
 000003     input  wire             rst_n,     // active-low asynchronous reset
 000229     input  wire             en,        // count enable
 000048     input  wire             load,      // synchronous load (takes priority over en)
 000020     input  wire [WIDTH-1:0] load_val,
 000036     output reg  [WIDTH-1:0] count,
 000038     output wire             wrapped    // high on the cycle the counter rolls over
        );
        
            assign wrapped = en && !load && (count == {WIDTH{1'b1}});
        
 000613     always @(posedge clk or negedge rst_n) begin
 000005         if (!rst_n) begin
 000005             count <= {WIDTH{1'b0}};
 000025         end else if (load) begin
 000025             count <= load_val;
 000154         end else if (en) begin
 000429             count <= count + 1'b1;
                end
            end
        
        endmodule
        
