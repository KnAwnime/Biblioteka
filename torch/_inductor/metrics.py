from __future__ import annotations

import csv
import inspect
import os
import re
from dataclasses import dataclass
from functools import lru_cache

from typing import Dict, List, Set, Tuple, TYPE_CHECKING, Union

from torch._inductor import config
from torch._inductor.utils import get_benchmark_name

# Prevent circular import
if TYPE_CHECKING:
    from torch._inductor.scheduler import (
        BaseSchedulerNode,
        ExternKernelSchedulerNode,
        NopKernelSchedulerNode,
        SchedulerNode,
    )

# counter for tracking how many kernels have been generated
generated_kernel_count = 0
generated_cpp_vec_kernel_count = 0
num_bytes_accessed = 0
nodes_num_elem: List[
    Tuple[
        Union[NopKernelSchedulerNode, SchedulerNode, ExternKernelSchedulerNode],
        int,
    ]
] = []
node_runtimes: List[Tuple[BaseSchedulerNode, float]] = []

# counters for tracking fusions
ir_nodes_pre_fusion = 0

# counters for tracking to_dtype inserted
cpp_to_dtype_count = 0

# counters for tracking cpp_wrapper disabled
disable_cpp_wrapper = 0


# reset all counters
def reset():
    global generated_kernel_count
    global generated_cpp_vec_kernel_count
    global num_bytes_accessed, nodes_num_elem
    global ir_nodes_pre_fusion
    global cpp_to_dtype_count
    global disable_cpp_wrapper

    generated_kernel_count = 0
    generated_cpp_vec_kernel_count = 0
    num_bytes_accessed = 0
    nodes_num_elem.clear()
    node_runtimes.clear()
    ir_nodes_pre_fusion = 0
    cpp_to_dtype_count = 0
    disable_cpp_wrapper = 0


REGISTERED_METRIC_TABLES: Dict[str, MetricTable] = {}


@dataclass
class MetricTable:
    table_name: str
    column_names: List[str]

    num_rows_added: int = 0

    def add_row(self, row_fn):
        if self.table_name not in enabled_metric_tables():
            return

        row_dict = row_fn()
        assert len(self.column_names) == len(
            row_dict
        ), f"{len(self.column_names)} v.s. {len(row_dict)}"
        assert set(self.column_names) == set(
            row_dict.keys()
        ), f"{set(self.column_names)} v.s. {set(row_dict.keys())}"

        row = [
            get_benchmark_name(),
        ]
        row += [row_dict[column_name] for column_name in self.column_names]
        self._write_row(row)

    def output_filename(self):
        return f"metric_table_{self.table_name}.csv"

    def write_header(self):
        filename = self.output_filename()
        with open(filename, "w") as fd:
            writer = csv.writer(fd, lineterminator="\n")
            writer.writerow(["model_name"] + self.column_names)

    def _write_row(self, row):
        filename = self.output_filename()
        if self.num_rows_added == 0 and not os.path.exists(filename):
            self.write_header()

        self.num_rows_added += 1

        for idx, orig_val in enumerate(row):
            if isinstance(orig_val, float):
                new_val = f"{orig_val:.6f}"
            elif orig_val is None:
                new_val = ""
            else:
                new_val = orig_val
            row[idx] = new_val

        with open(filename, "a") as fd:
            writer = csv.writer(fd, lineterminator="\n")
            writer.writerow(row)

    @staticmethod
    def register_table(name, column_names):
        table = MetricTable(name, column_names)
        REGISTERED_METRIC_TABLES[name] = table


MetricTable.register_table(
    "slow_fusion",
    [
        "kernel1_path",
        "kernel1_latency",
        "kernel2_path",
        "kernel2_latency",
        "fused_kernel_path",
        "fused_kernel_latency",
        "slow_down_ratio",
    ],
)

# track the fusion statistics for each graph
MetricTable.register_table(
    "graph_stats",
    [
        "graph_id",
        "num_nodes_before_fusion",
        "num_nodes_after_fusion",
    ],
)

# track the perf difference between persistent reduction and non-persistent
# reductions
MetricTable.register_table(
    "persistent_red_perf",
    [
        "kernel1_name",
        "kernel2_name",
        "kernel1_latency",
        "kernel2_latency",
        "size_hints",
        "reduction_hint",
        "speedup",
    ],
)

# Log metadata for pointwise/reduction kernels. E.g., model name, kernel path, numel, rnumel, reduction hint
MetricTable.register_table(
    "kernel_metadata",
    [
        "kernel_name",
        "kernel_path",
        "kernel_category",  # pointwise/reduction/foreach etc.
        "size_hints",
        "reduction_hint",
        "line_of_code",
        "num_load",
        "num_store",
        "num_for_loop",
        "num_args",
        # xyz numel can be different to size_hints since size_hints are rounded
        # up to the nearest power of 2.
        # Inductor kernel will burn in the xyz numel in kernel code for static
        # shape kernels.
        # Logging them will be helpful to find unaligned shape for reduction
        "xnumel",
        "ynumel",
        "rnumel",
    ],
)


def _parse_kernel_fn_code(kernel_module_code):
    """
    The kernel_module_code is the python module that contains kernel function code.
    kernel function is the proper triton kernel function annotated with
    @triton.jit
    """
    from .codecache import PyCodeCache
    from .wrapper_benchmark import get_triton_kernel

    mod = PyCodeCache.load(kernel_module_code)
    kernel = get_triton_kernel(mod)
    # kernel is a CachingAutotune; kernel.fn is the JITFunction;
    # kernel.fn.fn is the function being decorate by triton.jit
    return inspect.getsource(kernel.fn.fn)


def _parse_kernel_line_of_code(proper_kernel_fn_code):
    """
    Return the line of code for the kernel excluding the decorators.
    """
    return len(proper_kernel_fn_code.splitlines())


def _parse_size_hints(kernel_module_code):
    m = re.search(r"size_hints=(\[[0-9, ]*\]),", kernel_module_code)
    assert m, "size_hints not found in kernel source code!"
    return m.group(1)


def _parse_reduction_hint(kernel_category, kernel_module_code):
    if kernel_category not in ("reduction", "persistent_reduction"):
        return None
    m = re.search(r"reduction_hint=ReductionHint\.(\w*),", kernel_module_code)
    assert m, "reduction_hint not found in kernel source code!"
    return m.group(1)


def _count_pattern(proper_kernel_fn_code, pattern):
    return proper_kernel_fn_code.count(pattern)


def _count_args(proper_kernel_fn_code):
    def_line = proper_kernel_fn_code.splitlines()[0]
    assert def_line.startswith("def ")
    start_idx = def_line.index("(")
    end_idx = def_line.index("):")
    decl_csv = def_line[start_idx + 1 : end_idx]
    comps = decl_csv.split(",")
    return len(comps)


def _parse_proper_kernel_fn_code(kernel_fn_code):
    """
    Skip decorators.
    """
    start_pos = kernel_fn_code.index("def ")
    return kernel_fn_code[start_pos:]


def _parse_numel(proper_kernel_fn_code, numel_arg_name):
    m = re.search(f"{numel_arg_name} = ([\\d]+)", proper_kernel_fn_code)
    if m:
        return int(m.group(1))
    else:
        return None


def log_kernel_metadata(kernel_name, kernel_path, kernel_module_code):
    """
    An utility to log kernel metadata. We may parse metadata from kernel source code here.

    It's fine to parse the generated kernel code here since the logging is
    disabled by default. It would hurt compilation time.
    """
    from .wrapper_benchmark import get_kernel_category_by_source_code

    kernel_category = get_kernel_category_by_source_code(kernel_module_code)
    reduction_hint = _parse_reduction_hint(kernel_category, kernel_module_code)
    size_hints = _parse_size_hints(kernel_module_code)
    kernel_fn_code = _parse_kernel_fn_code(kernel_module_code)

    proper_kernel_fn_code = _parse_proper_kernel_fn_code(kernel_fn_code)

    # the line of code excluding the decortors
    kernel_line_of_code = _parse_kernel_line_of_code(proper_kernel_fn_code)

    get_metric_table("kernel_metadata").add_row(
        lambda: {
            "kernel_name": kernel_name,
            "kernel_path": kernel_path,
            "kernel_category": kernel_category,
            "size_hints": size_hints,
            "reduction_hint": reduction_hint,
            "line_of_code": kernel_line_of_code,
            "num_load": _count_pattern(proper_kernel_fn_code, "tl.load"),
            "num_store": _count_pattern(proper_kernel_fn_code, "tl.store"),
            "num_for_loop": _count_pattern(proper_kernel_fn_code, "for "),
            "num_args": _count_args(proper_kernel_fn_code),
            "xnumel": _parse_numel(proper_kernel_fn_code, "xnumel"),
            "ynumel": _parse_numel(proper_kernel_fn_code, "ynumel"),
            "rnumel": _parse_numel(proper_kernel_fn_code, "rnumel"),
        }
    )


def purge_old_log_files():
    """
    Purge the old log file at the beginning when the benchmark script runs.
    Should do it in the parent process rather than the child processes running
    each individual model.
    """
    for name, table in REGISTERED_METRIC_TABLES.items():
        if name in enabled_metric_tables():
            filename = table.output_filename()
            if os.path.exists(filename):
                os.unlink(filename)

            table.write_header()


@lru_cache
def enabled_metric_tables() -> Set[str]:
    config_str = config.enabled_metric_tables

    enabled = set()
    for name in config_str.split(","):
        name = name.strip()
        if not name:
            continue
        assert (
            name in REGISTERED_METRIC_TABLES
        ), f"Metric table name {name} is not registered"
        enabled.add(name)
    return enabled


def is_metric_table_enabled(name):
    return name in enabled_metric_tables()


def get_metric_table(name):
    assert name in REGISTERED_METRIC_TABLES, f"Metric table {name} is not defined"
    return REGISTERED_METRIC_TABLES[name]
