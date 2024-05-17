# flake8: noqa: B950
from ._internal import register_artifact, register_log

DYNAMIC = [
    "torch.fx.experimental.symbolic_shapes",
    "torch.fx.experimental.sym_node",
    "torch.fx.experimental.recording",
]
DISTRIBUTED = [
    "torch.distributed",
    "torch._dynamo.backends.distributed",
    "torch.nn.parallel.distributed",
]

register_log("dynamo", ["torch._dynamo", *DYNAMIC])
register_log("aot", ["torch._functorch.aot_autograd", "torch._functorch._aot_autograd"])
register_log("autograd", "torch.autograd")
register_log("inductor", ["torch._inductor", "torch._inductor.cudagraph_trees"])

register_artifact(
    "cudagraphs",
    "Logs information from wrapping inductor generated code with cudagraphs.",
)

register_log("dynamic", DYNAMIC)
register_log("torch", "torch")
register_log("distributed", DISTRIBUTED)
register_log(
    "dist_c10d", ["torch.distributed.distributed_c10d", "torch.distributed.rendezvous"]
)
register_log(
    "dist_ddp", ["torch.nn.parallel.distributed", "torch._dynamo.backends.distributed"]
)
register_log("dist_pp", ["torch.distributed.pipelining"])
register_log("dist_fsdp", ["torch.distributed.fsdp"])
register_log("onnx", "torch.onnx")
register_log("export", ["torch._dynamo", "torch.export", *DYNAMIC])

register_artifact(
    "guards",
    "This prints the guards for every compiled Dynamo frame. It does not tell you where the guards come from.",
    visible=True,
)
register_artifact("verbose_guards", "", off_by_default=True)
register_artifact(
    "bytecode",
    "Prints the original and modified bytecode from Dynamo. Mostly useful if you're debugging our bytecode generation in Dynamo.",
    off_by_default=True,
)
register_artifact(
    "graph",
    "Prints the dynamo traced graph (prior to AOTDispatch) in a table. If you prefer python code use `graph_code` instead. ",
)
register_artifact("graph_code", "Like `graph`, but gives you the Python code instead.")
register_artifact(
    "graph_sizes", "Prints the sizes of all FX nodes in the dynamo graph."
)
register_artifact(
    "trace_source",
    "As we execute bytecode, prints the file name / line number we are processing and the actual source code. Useful with `bytecode`",
)
register_artifact(
    "trace_call",
    "Like trace_source, but it will give you the per-expression blow-by-blow if your Python is recent enough.",
)
register_artifact(
    "trace_bytecode",
    "As we trace bytecode, prints the instruction and the current stack.",
)
register_artifact(
    "aot_graphs",
    "Prints the FX forward and backward graph generated by AOTDispatch, after partitioning. Useful to understand what's being given to Inductor",
    visible=True,
)
register_artifact(
    "aot_joint_graph",
    "Print FX joint graph from AOTAutograd, prior to partitioning. Useful for debugging partitioning",
)
register_artifact(
    "post_grad_graphs",
    "Prints the FX graph generated by post grad passes. Useful to understand what's being given to Inductor after post grad passes",
)
register_artifact(
    "compiled_autograd",
    "Prints various logs in compiled_autograd, including but not limited to the graphs. Useful for debugging compiled_autograd.",
    visible=True,
)
register_artifact(
    "compiled_autograd_verbose",
    "Will affect performance. Prints compiled_autograd logs with C++ info e.g. autograd node -> fx node mapping",
    off_by_default=True,
)
register_artifact(
    "ddp_graphs",
    "Only relevant for compiling DDP. DDP splits into multiple graphs to trigger comms early. This will print each individual graph here.",
)
register_artifact(
    "recompiles",
    "Prints the reason why we recompiled a graph. Very, very useful.",
    visible=True,
)
register_artifact(
    "recompiles_verbose",
    "Prints all guard checks that fail during a recompilation. "
    "At runtime, Dynamo will stop at the first failed check for each failing guard. "
    "So not all logged failing checks are actually ran by Dynamo.",
    visible=True,
    off_by_default=True,
)
register_artifact(
    "graph_breaks",
    "Prints whenever Dynamo decides that it needs to graph break (i.e. create a new graph). Useful for debugging why torch.compile has poor performance",
    visible=True,
)
register_artifact(
    "not_implemented",
    "Prints log messages whenever we return NotImplemented in a multi-dispatch, letting you trace through each object we attempted to dispatch to",
)
register_artifact(
    "output_code",
    "Prints the code that Inductor generates (either Triton or C++)",
    off_by_default=True,
    visible=True,
)
register_artifact(
    "schedule",
    "Inductor scheduler information. Useful if working on Inductor fusion algo",
    off_by_default=True,
)
register_artifact("perf_hints", "", off_by_default=True)
register_artifact("onnx_diagnostics", "", off_by_default=True)
register_artifact(
    "fusion",
    "Detailed Inductor fusion decisions. More detailed than 'schedule'",
    off_by_default=True,
)
register_artifact(
    "overlap",
    "Detailed Inductor compute/comm overlap decisions",
    off_by_default=True,
)
register_artifact(
    "sym_node",
    "Logs extra info for various SymNode operations",
    off_by_default=True,
)

register_artifact("custom_format_test_artifact", "Testing only", log_format="")
