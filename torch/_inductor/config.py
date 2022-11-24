import os
import sys

# add some debug printouts
debug = False

# Whether to disable a progress bar for autotuning
disable_progress = True

# Whether to enable printing the source code for each future
verbose_progress = False

# use cpp wrapper instead of python wrapper
cpp_wrapper = False

# dead code elimination
dce = False

# assume input tensors are dynamic
dynamic_shapes = (
    os.environ.get("TORCHDYNAMO_DYNAMIC_SHAPES") == "1"
)  # Use dynamic shapes if torchdynamo dynamic shapes is set

# assume weight tensors are fixed size
static_weight_shapes = True

# put correctness assertions in generated code
size_asserts = True

# enable loop reordering based on input orders
pick_loop_orders = True

# generate inplace computations
inplace_buffers = True

# codegen benchmark harness
benchmark_harness = True

# control store vs recompute heuristic
# For fanouts, rematearialization can lead to exponential blowup. So, have
# smaller threshold
realize_reads_threshold = 4
realize_bytes_threshold = 2000

# Threshold to prevent excessive accumulation of ops in one buffer during lowering
realize_acc_reads_threshold = 8

# fallback to eager for random/dropout, this is slow but useful for debugging
fallback_random = False

# automatically create fallbacks when encountering an unhandled op
implicit_fallbacks = True

# Enables a fusion pass that groups nodes together before the scheduler
prefuse_nodes = True

# do bench to decide best layout, currently only for aten.conv
tune_layout = False

# fuse even in cases without common reads
aggressive_fusion = False

# how many nodes to allow into a single fusion
max_fusion_size = 64

# replace small reductions with pointwise, disable with `= 1`
unroll_reductions_threshold = 8

comment_origin = False

compile_threads = (
    min(
        32,
        len(os.sched_getaffinity(0))
        if hasattr(os, "sched_getaffinity")
        else os.cpu_count(),
    )
    if sys.platform != "win32"
    else 1
)

# If kernel is fused, the name is generated from the origin node op names
# for larger kernels limit this
kernel_name_max_ops = 10

# How to import torchinductor, either torchinductor or torch.inductor
inductor_import = __name__.replace(".config", "")

# How to import torchdynamo, either torchdynamo or torch.dynamo
dynamo_import = inductor_import.replace("inductor", "dynamo")

# Pad input tensors of matmul/bmm/addmm to leverage Tensor Cores in NVIDIA GPUs
shape_padding = os.environ.get("TORCHINDUCTOR_SHAPE_PADDING", "0") == "1"
alignment_size = 4

# Fx-based linear/matmul/bmm + permute/transpose vertical fusion
permute_fusion = os.environ.get("TORCHINDUCTOR_PERMUTE_FUSION", "0") == "1"

# Mark the wrapper call in PyTorch profiler
profiler_mark_wrapper_call = False

# config specific to codegen/cpp.pp
class cpp:
    # set to torch.get_num_threads()
    threads = -1

    # Assume number of threads is dynamic, don't specialize thread number.
    # Kernels don't recompile on thread number changes with this flag on.
    # For single-threaded workload, turning it on would incur a slight
    # performance degradation.
    dynamic_threads = False

    simdlen = None
    min_chunk_size = 4096
    cxx = (
        None,  # download gcc12 from conda-forge if conda is installed
        "g++-12",
        "g++-11",
        "g++-10",
        "clang++",
        "g++",
        "g++.par",
    )
    # Allow kernel performance profiling via PyTorch profiler
    enable_kernel_profile = False


# config specific to codegen/triton.py
class triton:

    # Use cudagraphs on output code
    cudagraphs = True

    # choose conv backend, "aten" or "triton" or "autotune"
    convolution = "aten"

    # choose mm backend, "aten" or "triton" or "autotune"
    mm = "aten"

    # Always load full blocks (rather than broadcasting inside the block)
    # Set default as True because otherwise will encouter `map::at` error
    # in triton if loading from 1-dim tensor using 2-dim pointer offset
    # https://triton-lang.slack.com/archives/C01L1FLTX70/p1656023403343639
    # could be set as False if triton fixes the bug later
    dense_indexing = False

    # limit tiling dimensions
    max_tiles = 2

    # use triton.autotune?
    autotune = True

    use_bmm = False

    # should we stop a fusion to allow better tiling?
    tiling_prevents_pointwise_fusion = True
    tiling_prevents_reduction_fusion = True
    # should we give different names to kernels
    ordered_kernel_names = False
    # should we put op names in kernel names
    descriptive_kernel_names = True


# create a directory containing lots of debug information
class trace:
    # master switch for all debugging flags below
    enabled = os.environ.get("TORCHINDUCTOR_TRACE", "0") == "1"

    # Save python logger call >=logging.DEBUG
    debug_log = True

    # Save python logger call >=logging.INFO
    info_log = False

    # Save input FX graph (post decomps)
    fx_graph = True

    # Save TorchInductor IR before fusion pass
    ir_pre_fusion = True

    # Save TorchInductor IR after fusion pass
    ir_post_fusion = True

    # Copy generated code to trace dir
    output_code = True

    # SVG figure showing post-fusion graph
    graph_diagram = False

    # Store cProfile (see snakeviz to view)
    compile_profile = False

    # Upload the .tar.gz file
    # Needs to be overriden based on specific environment needs
    upload_tar = None


class InductorConfigContext:
    static_memory: bool
    matmul_tune: str
    matmul_padding: bool
    trition_autotune: bool
    trition_bmm: bool
    trition_mm: str
    trition_convolution: str
    rematerialize_threshold: int
    rematerialize_acc_threshold: int

    def save(self):
        self.static_memory = triton.cudagraphs
        self.matmul_tune = triton.mm
        self.matmul_padding = shape_padding
        self.triton_autotune = triton.autotune
        self.triton_bmm = triton.use_bmm
        self.triton_mm = triton.mm
        self.triton_convolution = triton.convolution
        self.rematerialize_threshold = realize_reads_threshold
        self.rematerialize_acc_threshold = realize_acc_reads_threshold

    def apply(self):
        triton.cudagraphs = self.static_memory
        triton.mm = self.matmul_tune
        shape_padding = self.matmul_padding
        triton.autotune = self.triton_autotune
        triton.use_bmm = self.triton_bmm
        triton.mm = self.triton_mm
        triton.convolution = self.triton_convolution
        realize_reads_threshold = self.rematerialize_threshold
        realize_acc_reads_threshold = self.rematerialize_acc_threshold

    # "default" -> trition.cudagraphs = False
    # "reduce-overhead" -> trition.cudagraphs = True
    # "max-autotune" -> triton.mm = "autotune" + trition.convolution = "autotune", cudagraphs = False, shape_padding = True

    # "static-memory":bool == tirton.cudagraphs
    # "matmul-tune":str == trition.mm
    # "matmul-padding":bool == shape_padding
    # "triton-autotune": bool == triton.autotune
    # "triton-bmm":bool = trition.use_bmm
    # "remiterialize_threshold" : int = realize_reads_threshold
    # "remiterialize_acc_threshold" : int = realize_acc_reads_threshold
    def __init__(self, arg=None):
        self.save()
        if arg is None:
            return
        # Handle mode
        if type(arg) is str:
            if arg == "default":
                self.static_memory = False
            elif arg == "reduce-overhead":
                self.static_memory = True
            elif arg == "max-autotune":
                self.static_memory = False
                self.matmul_padding = True
                self.trition_convolution = "autotune"
                self.trition_mm = "autotune"
                self.matmul_padding = True
            else:
                raise RuntimeError(f"Unrecognized mode {arg}, expected ")
            return
        # Handle passes
        for (name, val) in arg.items():
            attr_name = name.replace("-", "_")
            if not hasattr(self, attr_name):
                raise RuntimeError(f"Unexpected optimization pass {name}")
            if type(val) != type(getattr(self, attr_name)):
                raise RuntimeError(
                    f"Unexpected type of attr {name}, got {type(val)} should be {type(getattr(self, attr_name))}"
                )
            setattr(self, attr_name, val)

    def __enter__(self):
        self._prev = InductorConfigContext()
        self.apply()

    def __exit__(self, exc_type, exc_val, exc_tb):
        self._prev.apply()
