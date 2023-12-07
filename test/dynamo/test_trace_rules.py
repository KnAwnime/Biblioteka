# Owner(s): ["module: dynamo"]
import importlib
import types
import unittest

import torch
import torch._dynamo.test_case
from torch._dynamo.skipfiles import (
    FUNC_INLINELIST,
    LEGACY_MOD_INLINELIST,
    MOD_INLINELIST,
)
from torch._dynamo.trace_rules import (
    get_torch_obj_rule_map,
    load_object,
    manual_torch_name_rule_map,
)
from torch._dynamo.utils import istype

try:
    from .utils import create_dummy_module_and_function
except ImportError:
    from utils import create_dummy_module_and_function


ignored_torch_name_rule_set = {
    "torch.ExcludeDispatchKeyGuard",
    "torch._C.DisableTorchFunction",
    "torch._C._AutoDispatchBelowAutograd",
    "torch._C._DisableAutocast",
    "torch._C._DisableFuncTorch",
    "torch._C._DisablePythonDispatcher",
    "torch._C._DisableTorchDispatch",
    "torch._C._EnablePreDispatch",
    "torch._C._EnablePythonDispatcher",
    "torch._C._EnableTorchFunction",
    "torch._C._ExcludeDispatchKeyGuard",
    "torch._C._ForceDispatchKeyGuard",
    "torch._C._IncludeDispatchKeyGuard",
    "torch._C._InferenceMode",
    "torch._C._RestorePythonTLSSnapshot",
    "torch._C._SetExcludeDispatchKeyGuard",
    "torch._decomp.decompositions_for_rng.PhiloxStateTracker",
    "torch.ao.nn.sparse.quantized.utils.LinearBlockSparsePattern",
    "torch.autograd.anomaly_mode.detect_anomaly",
    "torch.autograd.anomaly_mode.set_detect_anomaly",
    "torch.autograd.forward_ad._set_fwd_grad_enabled",
    "torch.autograd.forward_ad.dual_level",
    "torch.autograd.grad_mode._force_original_view_tracking",
    "torch.autograd.grad_mode._unsafe_preserve_version_counter",
    "torch.autograd.grad_mode.set_multithreading_enabled",
    "torch.autograd.graph._CloneArgBeforeMutateMode",
    "torch.autograd.graph._swap_with_cloned",
    "torch.autograd.graph.save_on_cpu",
    "torch.autograd.graph.saved_tensors_hooks",
    "torch.backends.mkl.verbose",
    "torch.backends.mkldnn.verbose",
    "torch.cpu.StreamContext",
    "torch.cuda.StreamContext",
    "torch.cuda._DeviceGuard",
    "torch.cuda.device",
    "torch.cuda.device_of",
    "torch.cuda.graphs.graph",
    "torch.device",  # as constant folding function
    "torch.sparse.check_sparse_tensor_invariants",
    "torch.onnx._internal.fx.patcher.ONNXTorchPatcher",
    "torch.jit.strict_fusion",
    "torch.overrides.BaseTorchFunctionMode",
    "torch.jit._script.RecursiveScriptClass",
    "torch._jit_internal._IgnoreContextManager",
    "torch.distributed.rpc.server_process_global_profiler._server_process_global_profile",
    "torch.overrides.TorchFunctionMode",
    "torch.jit._ir_utils._InsertPoint",
    "torch.onnx._internal.diagnostics.infra.context.DiagnosticContext",
    "torch.distributed._device_mesh.DeviceMesh",
    "torch.distributed.autograd.context",
}


def gen_get_func_inlinelist(dummy_func_inlinelist):
    def get_func_inlinelist():
        inlinelist = set()
        for f in dummy_func_inlinelist:
            module_name, fn_name = f.rsplit(".", 1)
            m = importlib.import_module(module_name)
            fn = getattr(m, fn_name)
            inlinelist.add(fn.__code__)
        return inlinelist

    return get_func_inlinelist


class TraceRuleTests(torch._dynamo.test_case.TestCase):
    # We are using python function and module string names for these inlinelist,
    # this unit test is to make sure the functions/modules can be correctly imported
    # or loaded in case there is typo in the strings.
    def test_skipfiles_inlinelist(self):
        for m in LEGACY_MOD_INLINELIST.union(MOD_INLINELIST):
            self.assertTrue(
                isinstance(importlib.import_module(m), types.ModuleType),
                f"{m} from skipfiles.MOD_INLINELIST/LEGACY_MOD_INLINELIST is not a python module, please check and correct it.",
            )
        for f in FUNC_INLINELIST:
            module_name, fn_name = f.rsplit(".", 1)
            m = importlib.import_module(module_name)
            self.assertTrue(
                isinstance(getattr(m, fn_name), types.FunctionType),
                f"{f} from skipfiles.FUNC_INLINELIST is not a python function, please check and correct it.",
            )

    def test_func_inlinelist_torch_function(self):
        def fn(x):
            if istype(x, torch.Tensor):
                return x + 1
            else:
                return x - 1

        func_inlinelist = torch._dynamo.skipfiles.FUNC_INLINELIST.copy()
        func_inlinelist.add("torch._dynamo.utils.istype")

        self.assertTrue(
            "torch._dynamo" not in torch._dynamo.skipfiles.LEGACY_MOD_INLINELIST
        )
        self.assertTrue("torch._dynamo" not in torch._dynamo.skipfiles.MOD_INLINELIST)

        with unittest.mock.patch(
            "torch._dynamo.skipfiles.get_func_inlinelist",
            gen_get_func_inlinelist(func_inlinelist),
        ):
            x = torch.rand(3)
            opt_fn = torch.compile(backend="eager", fullgraph=True)(fn)
            ref = fn(x)
            res = opt_fn(x)
            self.assertEqual(ref, res)

    def test_func_inlinelist_third_party_function(self):
        mod, func = create_dummy_module_and_function()

        def fn(x):
            return func(x)

        func_inlinelist = torch._dynamo.skipfiles.FUNC_INLINELIST.copy()
        func_inlinelist.add(f"{mod.__name__}.{func.__name__}")

        with unittest.mock.patch(
            "torch._dynamo.skipfiles.get_func_inlinelist",
            gen_get_func_inlinelist(func_inlinelist),
        ), unittest.mock.patch(
            "torch._dynamo.skipfiles.SKIP_DIRS",
            torch._dynamo.skipfiles.SKIP_DIRS.copy(),
        ):
            # First adding the module to SKIP_DIRS so that it will be skipped.
            torch._dynamo.skipfiles.add(mod.__name__)
            x = torch.rand(3)
            opt_fn = torch.compile(backend="eager", fullgraph=True)(fn)
            ref = fn(x)
            res = opt_fn(x)
            self.assertEqual(ref, res)


if __name__ == "__main__":
    from torch._dynamo.test_case import run_tests

    run_tests()
