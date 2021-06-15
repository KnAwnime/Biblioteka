from functools import partial, wraps
import warnings
from numbers import Number

import torch
import numpy as np

from torch.testing import \
    (FileCheck, floating_and_complex_types_and)
from torch.testing._internal.common_utils import \
    (TestCase, compare_with_reference, is_iterable_of_tensors, run_tests, IS_SANDCASTLE, clone_input_helper, make_tensor,
     gradcheck, gradgradcheck, suppress_warnings, torch_to_numpy_dtype_dict)
from torch.testing._internal.common_methods_invocations import \
    (op_db, _NOTHING)
from torch.testing._internal.common_device_type import \
    (instantiate_device_type_tests, ops, onlyOnCPUAndCUDA, skipCUDAIfRocm, OpDTypes)
from torch.testing._internal.common_jit import JitCommonTestCase, check_against_reference

from torch.testing._internal.jit_metaprogramming_utils import create_script_fn, create_traced_fn, \
    check_alias_annotation
from torch.testing._internal.jit_utils import disable_autodiff_subgraph_inlining
from collections.abc import Sequence


# Get names of all the operators which have ref in their entry in OpInfo (testing infra)
# TODO: Add None to _NOTHING?
ref_test_ops = list(filter(lambda op: op.ref is not None and op.ref is not _NOTHING, op_db))


# Tests that apply to all operators
class TestOpInfo(TestCase):
    exact_dtype = True

    # Verifies that ops have their unsupported dtypes
    #   registered correctly by testing that each claimed unsupported dtype
    #   throws a runtime error
    @skipCUDAIfRocm
    @onlyOnCPUAndCUDA
    @ops(op_db, dtypes=OpDTypes.unsupported)
    def test_unsupported_dtypes(self, device, dtype, op):
        # sample_inputs can have a function for generating the input that doesn't work for specified dtype
        # https://github.com/pytorch/pytorch/issues/49024
        with self.assertRaises(RuntimeError):
            samples = op.sample_inputs(device, dtype)
            for sample in samples:
                op(sample.input, *sample.args, **sample.kwargs)

    # TODO: This whole code will be removed once the new design is approved and tested!
    """
    # Helper for comparing torch tensors and numpy arrays
    def assertEqualHelper(self, actual, expected, msg, *, dtype, exact_dtype=True, **kwargs):
        assert isinstance(actual, torch.Tensor)

        # Some NumPy functions return scalars, not arrays
        if isinstance(expected, Number):
            self.assertEqual(actual.item(), expected, **kwargs)
        elif isinstance(expected, np.ndarray):
            # Handles exact dtype comparisons between arrays and tensors
            if exact_dtype:
                # Allows array dtype to be float32 when comparing with bfloat16 tensors
                # since Numpy doesn't support the bfloat16 dtype
                # Also ops like scipy.special.erf, scipy.special.erfc etc. promote float16
                # to float32
                if expected.dtype == np.float32:
                    assert actual.dtype in (torch.float16, torch.bfloat16, torch.float32)
                else:
                    assert expected.dtype == torch_to_numpy_dtype_dict[actual.dtype]

            self.assertEqual(actual,
                             torch.from_numpy(expected).to(actual.dtype),
                             msg,
                             exact_device=False,
                             **kwargs)
        else:
            self.assertEqual(actual, expected, msg, exact_device=False, **kwargs)

    # Tests that the function and its (array-accepting) reference produce the same
    #   values on given tensors
    def _test_reference_numerics(self, dtype, op, tensors, equal_nan=True):
        def _helper_reference_numerics(expected, actual, msg, exact_dtype, equal_nan=True):
            if not torch.can_cast(numpy_to_torch_dtype_dict[expected.dtype.type], dtype):
                exact_dtype = False

            if dtype in [torch.uint8, torch.int8, torch.bool]:
                # NOTE: For these dtypes, PyTorch computes in the default scalar type (float)
                # while NumPy computes in float16
                self.assertEqualHelper(actual, expected, msg, dtype=dtype,
                                       exact_dtype=exact_dtype, rtol=1e-3, atol=1e-2)

            elif dtype is torch.bfloat16:
                # TODO: This file will dynamically change, ideally not good to share link to the line number
                # Ref: https://github.com/pytorch/pytorch/blog/master/torch/testing/_internal/common_utils.py#L1149
                self.assertEqualHelper(actual, expected, msg, dtype=dtype,
                                       exact_dtype=exact_dtype, rtol=16e-3, atol=1e-5)

            else:
                self.assertEqualHelper(actual, expected, msg, dtype=dtype, equal_nan=equal_nan, exact_dtype=exact_dtype)

        for sample_input in tensors:
            sample_args = sample_input.args
            sample_kwargs = sample_input.kwargs
            sample_torch_input = sample_input.input
            torch_kwargs, numpy_kwargs = op.sample_kwargs(sample_torch_input.device, dtype, sample_torch_input)
            numpy_args = []
            out_numpy_dtype = None

            if dtype is torch.bfloat16:
                for sample_arg in sample_args:
                    if isinstance(sample_arg, torch.Tensor):
                        numpy_arg = sample_arg.cpu().to(torch.float32).numpy()
                    else:
                        return
                    numpy_args.append(numpy_arg)
                sample_numpy_input = sample_torch_input.cpu().clone().to(torch.float32).numpy()
            else:
                for sample_arg in sample_args:
                    if isinstance(sample_arg, torch.Tensor):
                        numpy_arg = sample_arg.cpu().to(dtype).numpy()
                    else:
                        return
                    numpy_args.append(numpy_arg)
                sample_numpy_input = sample_torch_input.cpu().to(dtype).numpy()

            actual = op(sample_torch_input, *sample_args, **torch_kwargs)
            expected = op.ref(sample_numpy_input, *numpy_args, **numpy_kwargs)

            # Crafts a custom error message for smaller, printable tensors
            if sample_torch_input.numel() < 10:
                msg = ("Failed to produce expected results! Input tensor was"
                       " {0}, torch result is {1}, and reference result is"
                       " {2}.").format(sample_torch_input, actual, expected)
            else:
                msg = None

            exact_dtype = True
            if isinstance(actual, torch.Tensor):
                _helper_reference_numerics(expected, actual, msg, exact_dtype, equal_nan)
            else:
                for x, y in zip(expected, actual):
                    # Testing multi-outputs results
                    _helper_reference_numerics(x, y, msg, exact_dtype, equal_nan)
    """

    # Tests that the function and its (array-accepting) reference produce the same
    #   values on the tensors from sample_inputs func for the corresponding op.
    @onlyOnCPUAndCUDA
    @suppress_warnings
    @ops(ref_test_ops, allowed_dtypes=(torch.float32, torch.long))
    def test_reference_testing(self, device, dtype, op):
        sample_inputs = op.sample_inputs(device, dtype)
        for sample_input in sample_inputs:
            compare_with_reference(op, op.ref, sample_input)

    # Verifies that ops have their supported dtypes
    #   registered correctly by testing that each claimed supported dtype
    #   does NOT throw a runtime error
    # In addition verifies that the generated sample_inputs have the requested device and dtype
    @onlyOnCPUAndCUDA
    @ops(op_db, dtypes=OpDTypes.supported)
    def test_supported_dtypes(self, device, dtype, op):
        for sample in op.sample_inputs(device, dtype):
            op(sample.input, *sample.args, **sample.kwargs)
            # NOTE: only check the first tensor in the iterable of tensors
            sample_input = sample.input[0] if is_iterable_of_tensors(sample.input) else sample.input
            self.assertTrue(sample_input.dtype == dtype)
            self.assertTrue(sample_input.device.type == self.device_type)

    # Verifies that backward for each unsupported floating or complex dtype
    #   throw a runtime error.
    @onlyOnCPUAndCUDA
    @ops(op_db, dtypes=OpDTypes.unsupported_backward,
         allowed_dtypes=floating_and_complex_types_and(torch.float16, torch.bfloat16))
    def test_unsupported_backward(self, device, dtype, op):
        if not op.supports_autograd:
            self.skipTest("Skipped! Autograd not supported.")

        try:
            samples = op.sample_inputs(device, dtype, requires_grad=True)
        except RuntimeError as e:
            self.skipTest(f"Skipped! unable to generate sample. {e}")

        if len(samples) == 0:
            self.skipTest("Skipped! No sample inputs!")

        # NOTE: assert exception raised on ANY sample input
        with self.assertRaises(RuntimeError):
            for sample in op.sample_inputs(device, dtype, requires_grad=True):
                result = op(sample.input, *sample.args, **sample.kwargs)
                # TODO: handle non-tensor outputs
                if not isinstance(result, torch.Tensor):
                    self.skipTest("Skipped! Test does not handle non-tensor outputs")
                if sample.output_process_fn_grad is not None:
                    result = sample.output_process_fn_grad(result)
                result.sum().backward()

    # Verifies that backward for each supported floating or complex dtype
    #   does NOT throw a runtime error.
    # TODO: support multi-tensor outputs
    @onlyOnCPUAndCUDA
    @ops(op_db, dtypes=OpDTypes.supported_backward,
         allowed_dtypes=floating_and_complex_types_and(torch.float16, torch.bfloat16))
    def test_supported_backward(self, device, dtype, op):
        if not op.supports_autograd:
            self.skipTest("Skipped! Autograd not supported.")

        for sample in op.sample_inputs(device, dtype, requires_grad=True):
            result = op(sample.input, *sample.args, **sample.kwargs)
            if not isinstance(result, torch.Tensor):
                continue
            if sample.output_process_fn_grad is not None:
                result = sample.output_process_fn_grad(result)
            result.sum().backward()


# gradcheck requires double precision
_gradcheck_ops = partial(ops, dtypes=OpDTypes.supported,
                         allowed_dtypes=[torch.double, torch.cdouble])


class TestGradients(TestCase):
    exact_dtype = True

    # Copies inputs to inplace operations to avoid inplace modifications
    #   to leaves requiring gradient
    def _get_safe_inplace(self, inplace_variant):
        @wraps(inplace_variant)
        def _fn(t, *args, **kwargs):
            return inplace_variant(t.clone(), *args, **kwargs)

        return _fn

    def _check_helper(self, device, dtype, op, variant, check, *, check_forward_ad=False):
        if variant is None:
            self.skipTest("Skipped! Variant not implemented.")
        if not op.supports_dtype(dtype, torch.device(device).type):
            self.skipTest(f"Skipped! {op.name} does not support dtype {str(dtype)}")

        def is_inplace(variant):
            if hasattr(variant, "__wrapped__"):
                return variant.__wrapped__ is op.get_inplace()
            return variant is op.get_inplace()

        include_conjugated_inputs = op.test_conjugated_samples and dtype.is_complex
        samples = op.sample_inputs(device, dtype, requires_grad=True, include_conjugated_inputs=include_conjugated_inputs)

        for sample in samples:
            if sample.broadcasts_input and is_inplace(variant):
                continue

            # Note on TensorList inputs
            #
            # gradcheck does not support TensorList inputs so here we pass TensorList
            # inputs of size n as n single Tensor inputs to gradcheck and wrap the op
            # in a function that puts the n Tensor inputs back into a TensorList
            def fn(*inputs):
                # Put tensors back into TensorList since we splat them when passing to gradcheck
                if is_iterable_of_tensors(sample.input):
                    n = len(sample.input)
                    inputs = (inputs[:n], *inputs[n:])
                output = op.gradcheck_wrapper(variant, *inputs, **sample.kwargs)
                if sample.output_process_fn_grad is not None:
                    return sample.output_process_fn_grad(output)
                return output

            # Splat TensorList inputs into single Tensor inputs
            gradcheck_args = (sample.input,) if isinstance(sample.input, torch.Tensor) else tuple(sample.input)
            gradcheck_args += sample.args

            if check == 'gradcheck':
                self.assertTrue(gradcheck(fn, gradcheck_args,
                                          check_batched_grad=op.check_batched_grad,
                                          check_grad_dtypes=True,
                                          nondet_tol=op.gradcheck_nondet_tol,
                                          fast_mode=op.gradcheck_fast_mode,
                                          check_forward_ad=check_forward_ad))
            elif check == 'gradgradcheck':
                self.assertFalse(check_forward_ad, msg="Cannot run forward AD check for gradgradcheck")
                self.assertTrue(gradgradcheck(fn, gradcheck_args,
                                              gen_non_contig_grad_outputs=False,
                                              check_batched_grad=op.check_batched_gradgrad,
                                              check_grad_dtypes=True,
                                              nondet_tol=op.gradcheck_nondet_tol,
                                              fast_mode=op.gradcheck_fast_mode))
                self.assertTrue(gradgradcheck(fn, gradcheck_args,
                                              gen_non_contig_grad_outputs=True,
                                              check_batched_grad=op.check_batched_gradgrad,
                                              check_grad_dtypes=True,
                                              nondet_tol=op.gradcheck_nondet_tol,
                                              fast_mode=op.gradcheck_fast_mode))
            else:
                self.assertTrue(False, msg="Unknown check requested!")

    def _grad_test_helper(self, device, dtype, op, variant, *, check_forward_ad=False):
        return self._check_helper(device, dtype, op, variant, 'gradcheck', check_forward_ad=check_forward_ad)

    def _gradgrad_test_helper(self, device, dtype, op, variant):
        return self._check_helper(device, dtype, op, variant, 'gradgradcheck')

    def _skip_helper(self, op, device, dtype):
        if not op.supports_autograd:
            self.skipTest("Skipped! autograd not supported.")
        if not op.supports_complex_autograd(torch.device(device).type) and dtype.is_complex:
            self.skipTest("Skipped! Complex autograd not supported.")

    # Tests that gradients are computed correctly
    @_gradcheck_ops(op_db)
    def test_fn_grad(self, device, dtype, op):
        self._skip_helper(op, device, dtype)
        self._grad_test_helper(device, dtype, op, op.get_op())

    # Method grad (and gradgrad, see below) tests are disabled since they're
    #   costly and redundant with function grad (and gradgad) tests
    # @_gradcheck_ops(op_db)
    # def test_method_grad(self, device, dtype, op):
    #     self._skip_helper(op, device, dtype)
    #     self._grad_test_helper(device, dtype, op, op.get_method())

    @_gradcheck_ops(op_db)
    def test_inplace_grad(self, device, dtype, op):
        self._skip_helper(op, device, dtype)
        if not op.inplace_variant or not op.supports_inplace_autograd:
            self.skipTest("Skipped! Operation does not support inplace autograd.")
        self._grad_test_helper(device, dtype, op, self._get_safe_inplace(op.get_inplace()))

    # Test that gradients of gradients are computed correctly
    @_gradcheck_ops(op_db)
    def test_fn_gradgrad(self, device, dtype, op):
        self._skip_helper(op, device, dtype)
        if not op.supports_gradgrad:
            self.skipTest("Skipped! Operation does not support gradgrad")
        self._gradgrad_test_helper(device, dtype, op, op.get_op())

    # Test that gradients of gradients are properly raising
    @_gradcheck_ops(op_db)
    def test_fn_fail_gradgrad(self, device, dtype, op):
        self._skip_helper(op, device, dtype)
        if op.supports_gradgrad:
            self.skipTest("Skipped! Operation does support gradgrad")

        err_msg = r"derivative for .* is not implemented"
        with self.assertRaisesRegex(RuntimeError, err_msg):
            self._gradgrad_test_helper(device, dtype, op, op.get_op())

    # Method gradgrad (and grad, see above) tests are disabled since they're
    #   costly and redundant with function gradgrad (and grad) tests
    # @_gradcheck_ops(op_db)
    # def test_method_gradgrad(self, device, dtype, op):
    #     self._skip_helper(op, device, dtype)
    #     self._gradgrad_test_helper(device, dtype, op, op.get_method())

    @_gradcheck_ops(op_db)
    def test_inplace_gradgrad(self, device, dtype, op):
        self._skip_helper(op, device, dtype)
        if not op.inplace_variant or not op.supports_inplace_autograd:
            self.skipTest("Skipped! Operation does not support inplace autograd.")
        self._gradgrad_test_helper(device, dtype, op, self._get_safe_inplace(op.get_inplace()))

    @_gradcheck_ops(op_db)
    def test_forward_mode_AD(self, device, dtype, op):
        self._skip_helper(op, device, dtype)

        if op.supports_forward_ad:
            self._grad_test_helper(device, dtype, op, op.get_op(), check_forward_ad=True)
        else:
            err_msg = r"Trying to use forward AD with .* that does not support it\."
            hint_msg = ("Running forward AD for an OP that has does not support it did not "
                        "raise any error. If your op supports forward AD, you should set supports_forward_ad=True")
            with self.assertRaisesRegex(NotImplementedError, err_msg, msg=hint_msg):
                self._grad_test_helper(device, dtype, op, op.get_op(), check_forward_ad=True)


# Tests operators for consistency between JIT and eager, also checks
#   correctness of JIT specific alias schemas and intended
#   autodifferentiation behavior.
# Inherits from JitCommonTestCase instead of TestCase directly to share
#   functionality with original test_jit.py method operator tests
class TestCommon(JitCommonTestCase):
    exact_dtype = True

    # variant testing is only done with torch.float and torch.cfloat to avoid
    #   excessive test times and maximize signal to noise ratio
    _variant_ops = partial(ops, dtypes=OpDTypes.supported,
                           allowed_dtypes=(torch.float, torch.cfloat))

    # alias testing is only done with troch.float for the same reason
    _alias_ops = partial(ops, dtypes=OpDTypes.supported,
                         allowed_dtypes=(torch.float,))

    # Tests that the forward and backward passes of operations produce the
    #   same values for the cross-product of op variants (method, inplace)
    #   against eager's gold standard op function variant
    @_variant_ops(op_db)
    def test_variant_consistency_eager(self, device, dtype, op):
        # Acquires variants (method variant, inplace variant, aliases)

        method = op.method_variant
        inplace = op.inplace_variant

        # list of all inplace ops: inplace variant + alias inplace variants if exist
        inplace_ops = [inplace, ]
        variants = [method, inplace]

        for a_op in op.aliases:
            variants.append(a_op.op)
            variants.append(a_op.method_variant)
            variants.append(a_op.inplace_variant)
            inplace_ops.append(a_op.inplace_variant)

        inplace_variants = tuple(filter(None, inplace_ops))
        variants = tuple(filter(None, variants))

        _requires_grad = (op.supports_autograd and
                          (dtype.is_floating_point or op.supports_complex_autograd(torch.device(device).type)))

        include_conjugated_inputs = op.test_conjugated_samples and dtype.is_complex
        samples = op.sample_inputs(device, dtype, requires_grad=_requires_grad, include_conjugated_inputs=include_conjugated_inputs)

        def _test_consistency_helper(samples, variants):
            for sample in samples:
                # TODO: Check grad for all Tensors requiring grad if sample.input is TensorList
                tensor = sample.input if isinstance(sample.input, torch.Tensor) else sample.input[0]

                # Computes function forward and backward values
                tensor.grad = None
                expected_forward = op(sample.input, *sample.args, **sample.kwargs)
                expected_grad = None

                output_process_fn_grad = sample.output_process_fn_grad if sample.output_process_fn_grad \
                    else lambda x: x

                # Skips inplace variants if the output dtype is not the same as
                #   the input dtype
                skip_inplace = False
                if (isinstance(expected_forward, torch.Tensor) and
                        expected_forward.dtype is not tensor.dtype):
                    skip_inplace = True

                # TODO: backward consistency only supported for single tensor outputs
                # TODO: backward consistency only checked on sample.input, not all
                #   tensor inputs
                # TODO: update to handle checking grads of all tensor inputs as
                #   derived from each tensor output
                if (op.supports_autograd and isinstance(expected_forward, torch.Tensor)
                        and (dtype.is_floating_point or op.supports_complex_autograd(torch.device(device).type))):
                    output_process_fn_grad(expected_forward).sum().backward()
                    expected_grad = tensor.grad

                # Test eager consistency
                for variant in variants:
                    # Skips inplace ops
                    if variant in inplace_ops and skip_inplace:
                        continue

                    # Compares variant's forward
                    # Note: copies the to-be-modified input when testing the inplace variant
                    tensor.grad = None
                    cloned = clone_input_helper(sample.input) if variant in inplace_ops else sample.input

                    if variant in inplace_ops and sample.broadcasts_input:
                        with self.assertRaises(RuntimeError,
                                               msg=('inplace variant either incorrectly allowed '
                                                    'resizing or you have marked the sample {}'
                                                    ' incorrectly with `broadcasts_self=True'.format(sample.summary()))):
                            variant_forward = variant(cloned,
                                                      *sample.args,
                                                      **sample.kwargs)
                        continue

                    variant_forward = variant(cloned,
                                              *sample.args,
                                              **sample.kwargs)
                    self.assertEqual(expected_forward, variant_forward)

                    # Compares variant's backward
                    if expected_grad is not None and \
                            (variant not in inplace_ops or op.supports_inplace_autograd):
                        output_process_fn_grad(variant_forward).sum().backward()
                        self.assertEqual(expected_grad, tensor.grad)

        _test_consistency_helper(samples, variants)

        def _test_inplace_preserve_storage(samples, variants):
            for sample in samples:
                # Skips inplace variants if the output dtype is not the same as
                #   the input dtype
                expected_forward = op(sample.input, *sample.args, **sample.kwargs)
                tensor = sample.input if isinstance(sample.input, torch.Tensor) else sample.input[0]
                skip_inplace = False
                if (isinstance(expected_forward, torch.Tensor) and
                        expected_forward.dtype is not tensor.dtype):
                    skip_inplace = True
                if skip_inplace:
                    return
                for variant in variants:
                    cloned = clone_input_helper(sample.input) if variant in inplace_ops else sample.input
                    inp_tensor = cloned if isinstance(cloned, torch.Tensor) else cloned[0]
                    data_ptr = inp_tensor.data_ptr()
                    variant_forward = variant(cloned,
                                              *sample.args,
                                              **sample.kwargs)
                    # TODO Support non-tensor outputs if they exist for inplace ops
                    if (isinstance(variant_forward, torch.Tensor)):
                        self.assertEqual(data_ptr, variant_forward.data_ptr(), atol=0, rtol=0)
                    else:
                        self.assertTrue(False, "Non-tensor outputs for inplace ops are not supported")

        if len(inplace_ops) > 0:
            inplace_samples = list(filter(lambda sample: not sample.broadcasts_input, samples))
            _test_inplace_preserve_storage(inplace_samples, inplace_variants)

    # Tests that the forward and backward passes of operations produce the
    #   same values for the cross-product of op variants (function, method, inplace)
    #   and runtimes (eager, traced, scripted).
    # TODO WARNING: inplace x {traced, scripted} not currently tested
    @_variant_ops(op_db)
    def test_variant_consistency_jit(self, device, dtype, op):
        _requires_grad = op.supports_autograd and (dtype.is_floating_point or
                                                   op.supports_complex_autograd(torch.device(device).type))

        include_conjugated_inputs = op.test_conjugated_samples and dtype.is_complex
        samples = op.sample_inputs(device, dtype, requires_grad=_requires_grad, include_conjugated_inputs=include_conjugated_inputs)

        for sample in samples:
            # Acquires variants to test
            func = op.get_op()
            method = op.get_method()
            variants = {
                # TODO: inplace tests currently fail, fix and add inplace variant
                'function': func, 'method': method,
            }

            # Test traced and scripted consistency
            for func_type, variant in variants.items():
                if variant is None:
                    continue

                # Create accessor for script function variant
                name = op.name + '_' if func_type == 'inplace' else op.name

                # run with disable_autodiff_subgraph_inlining(True) to test
                #   autodiff support. Context manager forces the graph to contain
                #   DifferentiableGraph nodes if they are present
                with disable_autodiff_subgraph_inlining():
                    # Check scripted forward, grad, and grad grad
                    script_fn = create_script_fn(self, name, func_type)

                    def out_fn(output):
                        # Processes the output for autograd
                        if sample.output_process_fn_grad is not None:
                            return sample.output_process_fn_grad(output)
                        return output

                    check_against_reference(self,
                                            script_fn,
                                            func,
                                            out_fn,
                                            (sample.input,) + sample.args,
                                            sample.kwargs,
                                            no_grad=not _requires_grad, no_gradgrad=not op.supports_gradgrad)

                    # Check traced forward, grad, and grad grad
                    traced_fn = create_traced_fn(self, variant)
                    check_against_reference(self,
                                            traced_fn,
                                            func,
                                            out_fn,
                                            (sample.input,) + sample.args,
                                            sample.kwargs,
                                            no_grad=not _requires_grad, no_gradgrad=not op.supports_gradgrad)

                    # Check alias annotation schema for correctness (make
                    #   sure inputs that aren't supposed to be modified aren't)
                    # Note: only runs in float32 and int64 because schema isn't affected by dtype,
                    #   so running it on all dtypes is would be excessive
                    if dtype in [torch.float32, torch.int32]:
                        check_alias_annotation(name, (sample.input,) + sample.args, sample.kwargs,
                                               func_type=func_type, aten_name=op.aten_name)

                    # Check autodifferentiation of nodes for traced and scripted graphs, only need to check once per sample
                    if dtype is torch.float32:
                        # Sandcastle doesn't fuse nodes
                        if IS_SANDCASTLE:
                            # fusible nodes are expected to be found in FusionGroups in the DifferentiableGraphs
                            nonfusible_nodes = op.autodiff_nonfusible_nodes + op.autodiff_fusible_nodes
                            fusible_nodes = []
                        else:
                            nonfusible_nodes = op.autodiff_nonfusible_nodes
                            fusible_nodes = op.autodiff_fusible_nodes

                        self.assertAutodiffNode(traced_fn.last_graph, op.assert_autodiffed, nonfusible_nodes, fusible_nodes)
                        self.assertAutodiffNode(script_fn.last_graph, op.assert_autodiffed, nonfusible_nodes, fusible_nodes)

    @_alias_ops((op for op in op_db if op.aliases))
    def test_jit_alias_remapping(self, device, dtype, op):
        samples = op.sample_inputs(device, dtype, requires_grad=True)
        if len(samples) == 0:
            self.skipTest("Skipped! No sample inputs!")

        # NOTE: only tests on first sample
        sample = samples[0]

        # [Scripting Data Preparation]
        # Prepare data for test scripting
        # Below we prepare strings of args/kwargs with and without type annotations.
        # These strings are inserted into function template strings which is then torch scripted.
        # - args string is ["t0"] corresponding to the "input" tensor required by the op
        # - args_annot_kw is the string for the template function signature, for example,
        # ["t0", "s0: float", "s1: bool", "max: float = 1.0", "min: float = 0.0"] ->
        #    def fn(t0, s0: float, s1: bool, max: float = 1.0, min: float = 0.0)
        # - args_kw is the string of args/kwargs used to call the op, same as args_annot_kw but
        # without type annotations
        args = ["t0"]

        def quote_strs(v):
            if isinstance(v, str):
                return f"'{v}'"

            return str(v)

        args_annot_kw = args + \
            [f"s{i}: {type(v).__name__}" for i, v in enumerate(sample.args)] + \
            [f"{k}: {type(v).__name__} = {quote_strs(v)}" for k, v in sample.kwargs.items()]
        args_kw = args + \
            [f"s{i}" for i in range(len(sample.args))] + \
            [f"{k}={quote_strs(v)}" for k, v in sample.kwargs.items()]

        # Prepare data for test tracing
        sample_args_kwargs = ()
        if len(sample.args) > 0:
            sample_args_kwargs += (sample.args, )
        if len(sample.kwargs) > 0:
            sample_args_kwargs += (sample.kwargs, )

        original_name = op.aten_name
        original_name_inplace = original_name + "_"
        expected_dtype = op(sample.input, *sample.args, **sample.kwargs).dtype

        for a_op in op.aliases:
            inplace = a_op.inplace_variant
            method_or_inplace = [a_op.inplace_variant, a_op.method_variant]
            variants = (v for v in (a_op.op, a_op.method_variant, a_op.inplace_variant) if v is not None)

            # Test scripting:
            for variant in variants:
                variant_name = variant.__name__
                op_name = original_name_inplace if variant is inplace else original_name

                if variant in method_or_inplace:
                    fn_template = '''
                        def _fn(t0{c}{args_annot_kw}):
                            return t0.{alias_name}({args_kw})
                    '''
                    # remove the first input tensor
                    script = fn_template.format(
                        c=", " if len(args_kw[1:]) > 1 or len(args_annot_kw[1:]) >= 1 else "",
                        args_annot_kw=", ".join(args_annot_kw[1:]),
                        args_kw=", ".join(args_kw[1:]),
                        alias_name=variant_name,
                    )
                else:
                    fn_template = '''
                        def _fn({args_annot_kw}):
                            return variant({args_kw})
                    '''
                    script = fn_template.format(
                        args_annot_kw=", ".join(args_annot_kw),
                        args_kw=", ".join(args_kw),
                    )
                scripted = torch.jit.CompilationUnit(script)._fn

                if (variant is inplace and not torch.can_cast(expected_dtype, dtype)):
                    try:
                        inp = clone_input_helper(sample.input)
                        scripted(inp, *sample.args, **sample.kwargs)
                    except Exception as e:
                        continue
                    self.fail("Inplace operation on integer tensor that should be promoted to float didn't fail!")

                inp = clone_input_helper(sample.input)
                scripted(inp, *sample.args, **sample.kwargs)
                inp = clone_input_helper(sample.input)
                graph = scripted.graph_for(inp, *sample.args, **sample.kwargs)
                FileCheck().check(op.aten_name).check_not(variant_name).run(graph)

            # Test tracing:
            for variant in variants:
                variant_name = variant.__name__
                op_name = original_name_inplace if variant is inplace else original_name

                def _fn(*sample_args, **sample_kwargs):
                    return variant(*sample_args, **sample_kwargs)

                inp = (clone_input_helper(sample.input),) + sample_args_kwargs
                traced = torch.jit.trace(_fn, *inp)
                inp = (clone_input_helper(sample.input),) + sample_args_kwargs
                traced(*inp)
                inp = (clone_input_helper(sample.input),) + sample_args_kwargs
                graph = traced.graph_for(*inp)
                FileCheck().check(op_name).check_not(variant_name).run(graph)

    # Validates ops implement the correct out= behavior
    # See https://github.com/pytorch/pytorch/wiki/Developer-FAQ#how-does-out-work-in-pytorch
    #   for a description of the correct behavior
    # TODO: operations that support out= but don't support float
    #   are not covered by this test.
    @ops(op_db, allowed_dtypes=(torch.float,))
    def test_out(self, device, dtype, op):
        # TODO: verify the op doesn't support the out= kwarg
        if not op.supports_out:
            self.skipTest("Skipped! Op doesn't support out= kwarg.")

        # NOTE: only tests on first sample
        samples = op.sample_inputs(device, dtype)
        sample = samples[0]

        # calls it normally to get the expected result
        expected = op(sample.input, *sample.args, **sample.kwargs)
        op_out = partial(op, sample.input, *sample.args, **sample.kwargs)

        # Short-circuits if output is not a single tensor or an
        #   iterable of tensors

        if not isinstance(expected, torch.Tensor) and not is_iterable_of_tensors(expected, include_empty=True):
            self.skipTest("Skipped! Only supports single tensor or iterable of tensor outputs.")

        # A wrapper around map that works with single tensors and always
        #   instantiates the map. Used below to apply transforms to
        #   single tensor and iterable tensor outputs.
        def _apply_out_transform(fn, out):
            if isinstance(out, torch.Tensor):
                return fn(out)

            # assumes (see above) that out is an iterable of tensors
            return tuple(map(fn, out))

        # Case 0: out= with the correct shape, dtype, and device
        #   but NaN values for floating point and complex tensors, and
        #   maximum values for integer tensors.
        #   Expected behavior: out= values have no effect on the computation.
        def _case_zero_transform(t):
            try:
                info = torch.iinfo(t.dtype)
                return torch.full_like(t, info.max)
            except TypeError as te:
                # for non-integer types fills with NaN
                return torch.full_like(t, float('nan'))

        out = _apply_out_transform(_case_zero_transform, expected)
        result = op_out(out=out)
        self.assertEqual(expected, out)

        # Checks that the returned value shares storage with out
        # NOTE: only checks on the CPU and CUDA device types since some
        #   device types don't have storage
        if self.device_type == 'cpu' or self.device_type == 'cuda':
            if isinstance(out, torch.Tensor):
                self.assertEqual(out.storage().data_ptr(), result.storage().data_ptr())
            else:
                for out_t, result_t in zip(out, result):
                    self.assertEqual(out_t.storage().data_ptr(), result_t.storage().data_ptr())

        # Case 1: out= with the correct shape, dtype, and device,
        #   but noncontiguous.
        #   Expected behavior: strides are respected and `out` storage is not changed.
        def _case_one_transform(t):
            return make_tensor(t.shape,
                               dtype=t.dtype,
                               device=t.device,
                               noncontiguous=True)

        # Extracts strides from a tensor or iterable of tensors into a tuple
        def _extract_strides(out):
            if isinstance(out, torch.Tensor):
                return (out.stride(),)

            # assumes (see above) that out is an iterable of tensors
            return tuple(map(lambda t: t.stride(), out))

        def _extract_data_ptrs(out):
            if isinstance(out, torch.Tensor):
                return (out.data_ptr(),)

            # assumes (see above) that out is an iterable of tensors
            return tuple(map(lambda t: t.data_ptr(), out))


        out = _apply_out_transform(_case_one_transform, expected)
        original_strides = _extract_strides(out)
        original_ptrs = _extract_data_ptrs(out)

        op_out(out=out)
        final_strides = _extract_strides(out)
        final_ptrs = _extract_data_ptrs(out)

        self.assertEqual(expected, out)
        self.assertEqual(original_strides, final_strides)
        self.assertEqual(original_ptrs, final_ptrs)

        # Case 2: out= with the correct dtype and device, but the wrong shape
        #   Expected behavior: resize with a warning.
        def _case_two_transform(t):
            wrong_shape = list(t.shape)

            if len(wrong_shape) == 0:
                # Handles scalar tensor case (empty list)
                wrong_shape = [2]
            else:
                wrong_shape[-1] = wrong_shape[-1] + 1
            return make_tensor(wrong_shape, dtype=t.dtype, device=t.device)

        out = _apply_out_transform(_case_two_transform, expected)
        msg_fail = "Resized a non-empty tensor but did not warn about it."
        with self.assertWarnsRegex(UserWarning, "An output with one or more elements", msg=msg_fail):
            op_out(out=out)
        self.assertEqual(expected, out)

        # Case 3: out= with the correct dtype and device, but an empty
        #   tensor.
        #   Expected behavior: resize without warning.
        def _case_three_transform(t):
            return make_tensor((0,),
                               dtype=t.dtype,
                               device=t.device)

        out = _apply_out_transform(_case_three_transform, expected)
        with warnings.catch_warnings(record=True) as caught:
            warnings.simplefilter("always")
            op_out(out=out)

        # Verifies no warning is a resize warning
        for w in caught:
            if "An output with one or more elements" in str(w.message):
                self.fail("Resizing an out= argument with no elements threw a resize warning!")

        self.assertEqual(expected, out)

        # Case 4: out= with correct shape and dtype, but wrong device.
        wrong_device = None
        if torch.device(device).type != 'cpu':
            wrong_device = 'cpu'
        elif torch.cuda.is_available():
            wrong_device = 'cuda'

        if wrong_device is not None:
            def _case_four_transform(t):
                return make_tensor(t.shape, dtype=t.dtype, device=wrong_device)

            out = _apply_out_transform(_case_four_transform, expected)
            msg_fail = f"Expected RuntimeError when calling with input.device={device} and out.device={wrong_device}"
            with self.assertRaises(RuntimeError, msg=msg_fail):
                op_out(out=out)

        # Case 5: out= with correct shape and device, but a dtype
        #   that output cannot be "safely" cast to (long).
        #   Expected behavior: error.
        # NOTE: this case is filtered by dtype since some ops produce
        #   bool tensors, for example, which can be safely cast to any
        #   dtype. It is applied when single tensors are floating point or complex
        #   dtypes, or if an op returns multiple tensors when at least one such
        #   tensor is a floating point or complex dtype.
        _dtypes = floating_and_complex_types_and(torch.float16, torch.bfloat16)
        if (isinstance(expected, torch.Tensor) and expected.dtype in _dtypes or
                (not isinstance(expected, torch.Tensor) and any(t.dtype in _dtypes for t in expected))):
            def _case_five_transform(t):
                return make_tensor(t.shape, dtype=torch.long, device=t.device)

            out = _apply_out_transform(_case_five_transform, expected)
            msg_fail = "" if not isinstance(expected, torch.Tensor) else \
                       ("Expected RuntimeError when doing an unsafe cast from a result of dtype "
                        f"{expected.dtype} into an out= with dtype torch.long")
            with self.assertRaises(RuntimeError, msg=msg_fail):
                op_out(out=out)

    # Tests that
    # 1. The operator's output for physically conjugated tensors and conjugate view tensors
    # produces the same value
    # 2. The gradients are same in both cases mentioned in (1)
    # 3. If the operator's inplace variant is supported, tests that the inplace operation
    #    produces the correct value when called on a conjugate view tensor and that the output
    #    has its conj bit set to true
    # This test only runs for C -> R and C -> C functions
    # TODO: add tests for `R->C` functions
    # Note: This test runs for functions that take both tensors and tensorlists as input.
    @ops(op_db, allowed_dtypes=(torch.cfloat,))
    def test_conj_view(self, device, dtype, op):
        if not op.test_conjugated_samples:
            self.skipTest("Operation doesn't support conjugated inputs.")
        _requires_grad = (op.supports_autograd and op.supports_complex_autograd(torch.device(device).type))
        samples = op.sample_inputs(device, dtype, requires_grad=_requires_grad)
        inplace_variant = op.inplace_variant

        # helper function to physically conjugate the tensor
        def conjugate_physical(input):
            if isinstance(input, torch.Tensor):
                tensor_requires_grad = input.requires_grad
                with torch.no_grad():
                    input = input.conj_physical()
                return input.requires_grad_(tensor_requires_grad)

            if isinstance(input, Sequence):
                out = list(map(clone_input_helper, input))
                out[0] = conjugate_physical(out[0])
                return tuple(out)

        # helper function to clone and conjugate the input if its a tensor
        # else clone the sequence and conjugate the first element in the sequence
        # If a requires_grad argument is provided the tensor being conjugated will
        # have its requires_grad set to that value.
        def clone_conj_input_helper(input, **kwargs):
            if isinstance(input, torch.Tensor):
                requires_grad = kwargs.get('requires_grad', input.requires_grad)
                with torch.no_grad():
                    input = input.clone()
                # Note: .conj() is not called under no_grad mode since it's not allowed to modify a
                # view created in no_grad mode. Here it's ok to do so, so as a workaround we call conj
                # before resetting the requires_grad field for input
                input = input.conj()
                assert input.is_leaf
                return input.requires_grad_(requires_grad)

            if isinstance(input, Sequence):
                out = list(map(clone_input_helper, input))
                out[0] = clone_conj_input_helper(out[0])
                return tuple(out)

        for sample in samples:
            tensor = sample.input if isinstance(sample.input, torch.Tensor) else sample.input[0]
            cloned1 = clone_conj_input_helper(sample.input)
            sample.input = conjugate_physical(sample.input)

            # Computes function forward value with a physically conjugated tensor and
            # a conj view tensor and verifies that the output in both case are equal.
            expected_forward = op(sample.input, *sample.args, **sample.kwargs)
            forward_with_conjview = op(cloned1, *sample.args, **sample.kwargs)
            self.assertEqual(expected_forward, forward_with_conjview)

            # If the op has an inplace variant, and the input doesn't require broadcasting
            # and has the same dtype as output, verify that the inplace operation on a conjugated
            # input produces correct output, and the output tensor has the conj bit set to True
            if inplace_variant is not None and not sample.broadcasts_input:
                cloned2 = clone_conj_input_helper(tensor, requires_grad=False)
                if (isinstance(expected_forward, torch.Tensor) and
                        expected_forward.dtype is tensor.dtype):
                    inplace_forward = inplace_variant(cloned2, *sample.args, **sample.kwargs)
                    self.assertTrue(inplace_forward.is_conj())
                    self.assertEqual(inplace_forward, expected_forward)

            # TODO: backward consistency only supported for single tensor outputs
            # TODO: backward consistency only checked on sample.input, not all
            #   tensor inputs
            # TODO: update to handle checking grads of all tensor inputs as
            #   derived from each tensor output
            if isinstance(expected_forward, torch.Tensor) and expected_forward.requires_grad:
                tensor = sample.input if isinstance(sample.input, torch.Tensor) else sample.input[0]
                expected_forward.sum().backward(retain_graph=True)
                forward_with_conjview.sum().backward(retain_graph=True)
                if tensor.grad is not None:
                    cloned1_tensor = cloned1 if isinstance(cloned1, torch.Tensor) else cloned1[0]
                    self.assertEqual(tensor.grad, cloned1_tensor.grad)

                    tensor.grad, cloned1_tensor.grad = None, None

                    # a repeat of the above test if output is not complex valued
                    if (expected_forward.is_complex()):
                        grad = torch.randn_like(expected_forward)
                        expected_forward.backward(grad.conj_physical())
                        forward_with_conjview.backward(grad.conj())

                        self.assertEqual(tensor.grad, cloned1_tensor.grad)


instantiate_device_type_tests(TestOpInfo, globals())
instantiate_device_type_tests(TestGradients, globals())
instantiate_device_type_tests(TestCommon, globals())

if __name__ == '__main__':
    run_tests()
