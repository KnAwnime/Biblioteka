# Copyright (c) Meta Platforms, Inc. and affiliates
from dataclasses import dataclass
from typing import Callable, cast, Dict, List, Optional, Tuple

import torch

import torch.distributed._tensor.api as dtensor
from torch.distributed._tensor.placement_types import DTensorSpec
from torch.distributed._tensor.redistribute import redistribute_dtensor
from torch.distributed._tensor.utils import (
    ArgKwargsType,
    OutputSpecType,
    unwrap_local_tensor,
    unwrap_schema,
    wrap,
)
from torch.utils._pytree import tree_flatten, tree_map, tree_unflatten


"""
If _ENABLE_FALLBACK set to False, dispatch will fail when an op doesn't
have a sharding rule registered.
"""
_ENABLE_FALLBACK = False


"""
Print information on ops input shape and sharding for debugging purposes.
"""
_DEBUG_VERBOSE = False


@dataclass
class OpSchema(object):
    """
    OpSchema is a data class that describes an operator input schemas, it
    includes DTensor DTensorSpecs and non-tensor args/kwargs (positional order
    preserved). It is mainly used by the dispatching logic below to run things like
    sharding propagation.

    Sharding propagation rules registered could utilize this data class and
    do inplace update some fields (when necessary, i.e shape related ops) to make
    sure the args/kwargs are legit before passing to the local tensor operator.
    This is the main reason that we don't freeze this dataclass.

    NOTE: greater access to the operator inputs comes with greater responsibility.
    Here are some basic rules about what can be used and what can be changed.

    Args:
        func_schema: the function schema of the operator
        args_schema: contains args except that the DTensor args have been replaced
            with its DTensorSpec
        kwargs_schema: contains kwargs except that the DTensor kwargs have been replaced
            with its DTensorSpec

    What can be used:
        - every attribute within this class could be read to conduct
          sharding propagation.
    What can be changed:
        - only the args_schema and kwargs_schema could be changed.
        - every non-tensor args could be changed to accomodate for local tensor
          operations (i.e. for ops like view/reshape/...)
        - every "DTensorSpec" attribute inside `args_schema`, `kwargs_schema` and
          `args_spec` SHOULD NOT be updated! DTensorSpec are read only and sharding
          propagation shouldn't inplace update them, otherwise the input DTensor
          placements will get implicitly changed and it's error-prone.
    """

    func_schema: torch._C.FunctionSchema
    args_schema: Tuple[object, ...]
    kwargs_schema: Dict[str, object]

    is_inplace: bool = False
    is_out_variant: bool = False

    def __post_init__(self) -> None:
        # simple analysis of function schema to determine
        # if this is an inplace/out variant, it might not
        # be entirely correct, but it's good enough for now.
        self.is_inplace = self.func_schema.name[-1] == "_"
        self.is_out_variant = "out" in self.func_schema.overload_name

    @property
    def args_spec(self) -> Tuple[DTensorSpec, ...]:
        """
        args_spec: Tuple[DTensorSpec, ...]: contains a clean list of args spec list
            with NO non-DTensor positional arguments (i.e. int/float/tuple, etc)
            mainly used by sharding propagation to propagate the output spec
        """
        # filter out non-relavant values from args schema to get a clean spec list
        # this would mainly be used by sharding propagation rules
        return tuple(item for item in self.args_schema if isinstance(item, DTensorSpec))

    def __repr__(self) -> str:
        return (
            f"OpSchema(func_schema={self.func_schema},"
            f" args_schema={self.args_schema},"
            f" kwargs_schema={self.kwargs_schema})"
        )


@dataclass
class OutputSharding:
    """
    OutputSharding is a data class that is used by the sharding propagation
    rules, it could set the output_spec upon successful propagation, and if
    it failed, output_spec would become None and sharding propagation rules
    could give a list of suggestions for inputs to reshard.

    NOTE: the schema_suggestion generated by sharding propagation should be
    exactly the same as the operator OpSchema, except the DTensor DTensorSpecs
    """

    output_spec: OutputSpecType
    schema_suggestions: Optional[List[OpSchema]] = None
    failed_reason: Optional[str] = None


def pack_args_kwargs_with_local_tensor(
    args: ArgKwargsType,
    args_schema: ArgKwargsType,
    redistribute_with_schema: bool = False,
) -> ArgKwargsType:
    flatten_args, args_tree_spec = tree_flatten(args)
    flatten_args_schema, _ = tree_flatten(args_schema)

    for i, arg in enumerate(flatten_args):
        if isinstance(arg, dtensor.DTensor):
            if redistribute_with_schema:
                target_spec = flatten_args_schema[i]
                arg = redistribute_dtensor(
                    arg, target_spec.mesh, target_spec.placements
                )

            # reuse the schema list and update it with local tensor
            flatten_args_schema[i] = arg._local_tensor

    return tree_unflatten(flatten_args_schema, args_tree_spec)


def _reshape_alias(
    x: torch.Tensor, shape: Tuple[int, ...], strides: Tuple[int, ...]
) -> torch.Tensor:
    return torch.ops.aten.view(x, shape)


_CURRENT_DECOMPOSITION_TABLE: Dict[Callable[..., object], Callable[..., object]] = {
    torch.ops.aten._reshape_alias.default: _reshape_alias,
}


def propagate_input_sharding(
    op_call: torch._ops.OpOverload,
    args: Tuple[object, ...],
    kwargs: Dict[str, object],
    op_to_rules: Dict[str, Callable[[OpSchema], OutputSharding]],
) -> Tuple[OpSchema, bool, Optional[OutputSharding]]:
    # unwrap the args/kwargs schema
    args_schema = tree_map(unwrap_schema, args)
    kwargs_schema = tree_map(unwrap_schema, kwargs)

    op_schema = OpSchema(op_call._schema, args_schema, kwargs_schema)

    if _DEBUG_VERBOSE and torch.distributed.get_rank() == 0:
        print(f"{op_call}({op_schema})")
        local_shapes = tree_map(
            lambda t: t.to_local().shape if isinstance(t, dtensor.DTensor) else None,
            args,
        )
        print(f"    local shapes: {local_shapes}")

    op_key = str(op_call)
    sharding_prop_func = op_to_rules.get(op_key, None)

    if sharding_prop_func is None:
        # step 1. If there's not even one sharding rule
        # implemented for the operator, we fall back to
        # local tensor compute, this is wront currently
        # we will change the behavior to reshard to full
        # replicate and do the computatation
        if not _ENABLE_FALLBACK:
            raise NotImplementedError(
                f"Operator {op_key} does not have a DistributedTensor rule registered."
            )
        else:
            return op_schema, False, None

    # step 2. there's sharding propagation rule, run
    # sharding propagation to get output sharding
    try:
        output_sharding = sharding_prop_func(op_schema)
    except Exception as e:
        raise RuntimeError(
            f"Sharding propagation failed on op {op_key}.\n"
            f"Input schema: {op_schema}.\n"
            f"Error: {e}"
        ) from e

    # step 3. if can't get output_spec from sharding
    # propagation (i.e. no rules apply for input
    # placements), we do auto redistribute on inputs
    # to get an eligble input, which we will pick a
    # target schema base on the redistribute cost
    # TODO: implement full auto distribute with a
    # simple cost estimation model
    if output_sharding.output_spec is None:
        # do auto distributed/boxing here
        if output_sharding.schema_suggestions is not None:
            # pick the first suggestion for now,
            target_schema = output_sharding.schema_suggestions[0]
            # run sharding propagation again with target schema
            output_sharding = sharding_prop_func(target_schema)

            return target_schema, True, output_sharding

        else:
            raise RuntimeError(
                f"Sharding propagation failed on op {op_key}!"
                f"Input schema: {op_schema}."
                f"Failed reason: {output_sharding.failed_reason}"
            )
    else:
        return op_schema, False, output_sharding


def operator_dispatch(
    op_call: torch._ops.OpOverload,
    args: Tuple[object, ...],
    kwargs: Dict[str, object],
    op_to_rules: Dict[str, Callable[[OpSchema], OutputSharding]],
    custom_dispatch_ops: Dict[str, Callable[..., object]],
) -> object:
    # first we need to lift some private aten aliases to public calls
    if op_call in _CURRENT_DECOMPOSITION_TABLE:
        return _CURRENT_DECOMPOSITION_TABLE[op_call](*args, **kwargs)

    # STEP 0. See if threre're user defined custom aten operator
    # implementations. Custom operators take the highest priority
    if str(op_call) in custom_dispatch_ops:
        # dispatch to user defined custom distributed tensor ops
        return custom_dispatch_ops[str(op_call)](*args, **kwargs)

    target_schema, redistribute, output_sharding = propagate_input_sharding(
        op_call, args, kwargs, op_to_rules
    )

    if output_sharding is None:
        # default to local tensor ops, this is wrong
        # but we use it now to enable more tensor point-wise ops
        # TODO: delete this and use replicate (all_gather) as
        # the default fallback.
        tensor_args = tree_map(unwrap_local_tensor, args)
        tensor_kwargs = tree_map(unwrap_local_tensor, kwargs)
        local_results = op_call(*tensor_args, **tensor_kwargs)
        return wrap(local_results, target_schema.args_spec[0])

    local_tensor_args = pack_args_kwargs_with_local_tensor(
        args,
        target_schema.args_schema,
        redistribute_with_schema=redistribute,
    )
    local_tensor_kwargs = pack_args_kwargs_with_local_tensor(
        kwargs,
        target_schema.kwargs_schema,
        redistribute_with_schema=redistribute,
    )

    # run local op computation with potentially modified args/kwargs
    local_tensor_args = cast(Tuple[object, ...], local_tensor_args)
    local_tensor_kwargs = cast(Dict[str, object], local_tensor_kwargs)
    local_results = op_call(*local_tensor_args, **local_tensor_kwargs)

    if target_schema.is_inplace:
        # inplace op should return self instead of re-wrapping
        self = cast(dtensor.DTensor, args[0])
        self._spec = cast(DTensorSpec, output_sharding.output_spec)
        return self
    elif target_schema.is_out_variant:
        # out variant could possibly have multiple out args (i.e. lu_unpack.out)
        output_specs = (
            (output_sharding.output_spec,)
            if not isinstance(output_sharding.output_spec, tuple)
            else output_sharding.output_spec
        )
        out_dts = []
        spec_idx = 0
        for arg in target_schema.func_schema.arguments:
            if arg.is_out:
                out_dt = cast(dtensor.DTensor, kwargs[arg.name])
                out_dt._spec = cast(DTensorSpec, output_specs[spec_idx])
                out_dts.append(out_dt)
                spec_idx += 1

        assert len(out_dts) >= 1, "out variant should have at least one out arg"
        return tuple(out_dts) if len(out_dts) > 1 else out_dts[0]
    else:
        return wrap(local_results, output_sharding.output_spec)
