import itertools
from typing import Optional, List, Sequence, Union

from tools.codegen.api.types import *
import tools.codegen.api.cpp as cpp
from tools.codegen.code_template import CodeTemplate
from tools.codegen.context import with_native_function
from tools.codegen.utils import mapMaybe
from tools.codegen.gen import parse_native_yaml, FileManager
from tools.codegen.model import *

# Note [Manual Backend kernels]
# For these ops, we want to manually register to dispatch key Backend and
# skip codegen-ed registeration to all keys before Backend.
# For codegen this means:
#   - op set below must match ops with manual_kernel_registration=True in native_functions.yaml
#     where we skip codegen backend kernels
#   - all ops below are part of MANUAL_AUTOGRAD to skip codegen Autograd kernel registration
#   - all ops below are part of MANUAL_TRACER to skip codegen Tracer kernel registration
# Note: we still register to dispatch key Profiler for these ops, keeping it untouched for now.
# You can find the manual registration in torch/csrc/autograd/VariableTypeManual.cpp
MANUAL_BACKEND = set([
    'options', 'data', 'set_data', 'is_leaf', 'output_nr', '_version', 'retain_grad',
    '_backward', 'requires_grad_',
])

# For these ops we want to skip the codegen-ed registration to both Autograd and Tracer keys.
# You can find the manual registration in torch/csrc/autograd/VariableTypeManual.cpp
MANUAL_AUTOGRAD_AND_TRACER = set([
    'resize_', 'resize_as_', 'detach', 'detach_', 'copy_', '_fw_primal',
])

# Currently MANUAL_AUTOGRAD and MANUAL_TRACER share the same set of ops:
#   union(MANUAL_BACKEND, MANUAL_AUTOGRAD_AND_TRACER)
# You can find the manual registration in torch/csrc/autograd/VariableTypeManual.cpp
MANUAL_AUTOGRAD = MANUAL_TRACER = MANUAL_BACKEND | MANUAL_AUTOGRAD_AND_TRACER

# These functions we don't want to record for tracing, because we always want
# to trace their constituent parts.  This is a temporary hack in lieue
# of proper scopes, where subsequent compilation passes can ask for the unfolding
# on demand.  Only concrete ATen methods can be disabled this way; it will have
# NO EFFECT otherwise.
DONT_RECORD_TRACE = {
    'convolution', 'conv1d', 'conv2d', 'conv3d', 'conv_transpose1d',
    'conv_transpose2d', 'conv_transpose3d', 'lstm_cell', 'gru_cell',
    'rnn_tanh_cell', 'rnn_relu_cell',
    # FIXME: figure out a better way when we support sparse tensors in jit
    '_coalesced',
}

def should_trace(f: NativeFunction) -> bool:
    # Operations involving Storage or Type are not traceable at the moment
    if any(str(arg.type) in {'Storage', 'Type', 'ConstQuantizerPtr'}
           for arg in f.func.schema_order_arguments()):
        return False
    # We can't trace functions which don't have any Tensor or TensorList returns
    if not any(r.type.is_tensor_like() for r in f.func.returns):
        return False
    return f.func.name.name.base not in DONT_RECORD_TRACE

SELECT = CodeTemplate("""\

if (${cond}) {
  ${true}
} else {
  ${false}
}
""")

OP_NAME = CodeTemplate("""\
op_name = jit::Symbol::fromQualString("aten::${trace_name}");
""")

# These functions have their names recorded under trace renamed,
RENAME_TRACE = {
    'zero': 'zeros_like',  # replacing aten::zero_ with aten::zeros_like
    'fill': 'full_like',  # replacing aten::fill_ with aten::full_like
}

def format_trace_op_name(f: NativeFunction) -> str:
    # TODO: byte-for-byte compatible with old codegen behavior - should clean up
    if f.func.kind() in (SchemaKind.functional, SchemaKind.out) or f.func.name.name.dunder_method:
        # special case for *_out functions: the in-place and out-of-place ops
        # are overloaded with the same name in the JIT
        trace_name = str(f.func.name.name)
        trace_name = RENAME_TRACE.get(trace_name, trace_name)
        return OP_NAME.substitute(trace_name=trace_name)

    # otherwise, this is an in-place op and we need to emit both in- and
    # out-of-place versions
    outplace_trace_name = f.func.name.name.base
    inplace_trace_name = cpp.name(f.func)
    outplace_trace_name = RENAME_TRACE.get(outplace_trace_name, outplace_trace_name)
    inplace_trace_name = RENAME_TRACE.get(inplace_trace_name, inplace_trace_name)

    return SELECT.substitute(
        cond='tracer_state->force_outplace',
        true=OP_NAME.substitute(trace_name=outplace_trace_name),
        false=OP_NAME.substitute(trace_name=inplace_trace_name),
    )

ADD_TRACE_INPUT = CodeTemplate("""jit::tracer::addInputs(node, "${name}", ${input});""")

def format_trace_inputs(f: NativeFunction) -> str:

    def dispatch_trace_input(arg: Union[Argument, TensorOptionsArguments]) -> Sequence[str]:
        if isinstance(arg, TensorOptionsArguments):
            name = 'options'
            return [
                ADD_TRACE_INPUT.substitute(name=name, input='optTypeMetaToScalarType(options.dtype_opt())'),
                ADD_TRACE_INPUT.substitute(name=name, input='options.layout()'),
                ADD_TRACE_INPUT.substitute(name=name, input='options.device()'),
                ADD_TRACE_INPUT.substitute(name=name, input='options.pinned_memory()'),
            ]
        else:
            name = arg.name
            if str(arg.type) == 'Tensor?[]':
                return [f'jit::tracer::addInputs(node, "{name}", {name});']
            else:
                return [ADD_TRACE_INPUT.substitute(name=name, input=name)]

    args: List[Union[Argument, TensorOptionsArguments]] = list(f.func.schema_order_arguments())

    if f.func.is_out_fn():
        # *_out functions take the result as a separate argument, but we don't want to
        # trace that argument directly. Instead, we trace its TensorOptions.
        # So first, we need to remove the out argument from the list of arguments to trace.
        # TODO: byte-for-byte compatible with old codegen behavior - it's incorrect to assume
        # there is only one output argument.
        args = args[:-1]

    trace_inputs = itertools.chain.from_iterable(dispatch_trace_input(arg) for arg in args)

    if f.func.is_out_fn():
        # for *_out functions, handle the result argument differently for inplace/outplace.
        # For inplace: just add the input to the end to confirm with the JIT schema
        name = f.func.arguments.out[0].name  # TODO: old codegen behavior - should fix
        inplace = ADD_TRACE_INPUT.substitute(name=name, input=name)

        # for outplace: do nothing, except if the function is a factory.
        # Factories are a bit special because their out-of-place overloads
        # take an extra TensorOptions argument, which is missing in the _out function
        has_tensor_return = any(r.type.is_tensor_like() for r in f.func.returns)
        has_tensor_input_arg = any(a.type.is_tensor_like() for a in f.func.arguments.flat_non_out)
        is_factory_method = f.category_override == 'factory' or (has_tensor_return and not has_tensor_input_arg)

        # HACK: preserve old codegen behavior - the old codegen set the `is_factory_method`
        # flag for the whole family of ops with the same basename if any of them is a
        # factory method. For most cases the whole family of ops are indeed all factory
        # method - 'normal' is the only exception. So we handle it specially here to avoid
        # cloning the old logic.
        if f.func.name.name.base == 'normal':
            is_factory_method = True

        if is_factory_method:
            outplace = [
                ADD_TRACE_INPUT.substitute(name='out', input='optTypeMetaToScalarType(out.options().dtype_opt())'),
                ADD_TRACE_INPUT.substitute(name='out', input='out.options().layout()'),
                ADD_TRACE_INPUT.substitute(name='out', input='out.options().device()'),
                ADD_TRACE_INPUT.substitute(name='out', input='out.options().pinned_memory()'),
            ]
        else:
            outplace = []

        trace_inputs = itertools.chain(
            trace_inputs,
            [SELECT.substitute(cond='tracer_state->force_outplace', true='\n'.join(outplace), false=inplace)])

    return '\n'.join(trace_inputs)

# `torch.jit.trace` have undocumented keyword argument `_force_outplace`,
# which force jit to replace functions with outplace variants (for
# example `aten::add_` becomes `aten::add`).
#
# This replacement implemented in-place with minimum modifications of
# arguments stack (as it assumes that outplace call has the same arguments
# as inplace version).
#
# However there are no such substitutions available for `aten::fill_`
# and `aten::zero_` operators, as we never implemented `aten::fill`
# and `aten::zero`. So jit tracing hack replacing `aten::zero_` with
# `aten::zeros_like` and replacing `aten::fill_` with `aten::full_like`.
#
# But as they potentially can have different arguments, we also have
# to hack into the stack and add missing ones.
#
# A possible alternative would be:
#
#  - Add `aten::fill` and `aten::zero`
#
#  - Or keep `aten::zeros_like` arguments aligned with `aten::zero_`
# arguments (inside of the `native_functions.yaml`)
RENAME_TRACE_ADD_ARGS = {
    'fill': '''\
    jit::tracer::addInputs(node, "options", c10::optional<ScalarType>());
    jit::tracer::addInputs(node, "options", layout_or_default(c10::nullopt));
    jit::tracer::addInputs(node, "options", device_or_default(c10::nullopt));
    jit::tracer::addInputs(node, "options", pinned_memory_or_default(c10::nullopt));
    c10::optional<MemoryFormat> memory_format = c10::MemoryFormat::Preserve;
    jit::tracer::addInputs(node, "memory_format", memory_format);
''',
    'zero': '''\
    jit::tracer::addInputs(node, "options", c10::optional<ScalarType>());
    jit::tracer::addInputs(node, "options", layout_or_default(c10::nullopt));
    jit::tracer::addInputs(node, "options", device_or_default(c10::nullopt));
    jit::tracer::addInputs(node, "options", pinned_memory_or_default(c10::nullopt));
    c10::optional<MemoryFormat> memory_format = c10::MemoryFormat::Preserve;
    jit::tracer::addInputs(node, "memory_format", memory_format);
''',
}

INPLACE_GUARD = CodeTemplate("""\
jit::tracer::ensureUniqueIfOutOfPlaced("${name}", ${mutable_input});
""")

PRE_RECORD_TRACE = CodeTemplate("""\
torch::jit::Node* node = nullptr;
std::shared_ptr<jit::tracer::TracingState> tracer_state;
if (jit::tracer::isTracing()) {
  tracer_state = jit::tracer::getTracingState();
  at::Symbol op_name;
  ${set_op_name}
  node = tracer_state->graph->create(op_name, /*num_outputs=*/0);
  jit::tracer::recordSourceLocation(node);
  ${add_trace_inputs}
  tracer_state->graph->insertNode(node);
  ${inplace_guard}
  jit::tracer::setTracingState(nullptr);
}
""")

def format_prerecord_trace(f: NativeFunction) -> str:
    if not should_trace(f):
        return ''

    # TODO: clean up old codegen behavior
    is_inplace = f.func.kind() in (SchemaKind.inplace, SchemaKind.out) and not f.func.name.name.dunder_method
    add_args = RENAME_TRACE_ADD_ARGS.get(f.func.name.name.base, '') if is_inplace else ''
    additional_inputs = SELECT.substitute(
        cond='tracer_state->force_outplace',
        true=add_args,
        false='',
    ) if add_args else ''

    return PRE_RECORD_TRACE.substitute(
        set_op_name=format_trace_op_name(f),
        add_trace_inputs=format_trace_inputs(f) + additional_inputs,
        inplace_guard=INPLACE_GUARD.substitute(
            name=cpp.name(f.func),
            mutable_input=f.func.arguments.out[0].name if f.func.arguments.out else 'self',
        ) if is_inplace else '',
    )

POST_RECORD_TRACE = CodeTemplate("""\
if (tracer_state) {
  jit::tracer::setTracingState(std::move(tracer_state));
  ${add_trace_outputs}
}
""")

def format_postrecord_trace(f: NativeFunction) -> str:
    if not should_trace(f):
        return ''

    # For outplacing ops, *_out overloads require special handling to move the
    # output *argument* to a return value
    if f.func.is_out_fn():
        output_names_outplace = [arg.name for arg in f.func.arguments.out]
        output_names_inplace = cpp.return_names(f)

        # Code size optimization: the common case is that the return value is
        # the same for both variants
        if output_names_outplace == output_names_inplace:
            outputs = [f'jit::tracer::addOutput(node, {n});' for n in output_names_outplace]
            return POST_RECORD_TRACE.substitute(add_trace_outputs=outputs)

        selection = SELECT.substitute(
            cond='force_outplace',
            true='\n'.join(f'jit::tracer::addOutput(node, {n});' for n in output_names_outplace),
            false='\n'.join(f'jit::tracer::addOutput(node, {n});' for n in output_names_inplace),
        )
        return POST_RECORD_TRACE.substitute(add_trace_outputs=selection)
    else:
        output_names = cpp.return_names(f)
        outputs = [f'jit::tracer::addOutput(node, {n});' for n in output_names]
        return POST_RECORD_TRACE.substitute(add_trace_outputs=outputs)

def declare_returned_variables(f: NativeFunction) -> str:
    modifies_arguments = f.func.kind() in (SchemaKind.inplace, SchemaKind.out)
    if modifies_arguments:
        return ''
    if len(f.func.returns) == 1:
        return ''
    types = map(cpp.return_type, f.func.returns)
    names = cpp.return_names(f)
    return '\n'.join(f'{type} {name};' for type, name in zip(types, names))

def tie_return_values(f: NativeFunction) -> str:
    if len(f.func.returns) == 1:
        return f'auto {f.func.returns[0].name or "result"}'
    names = cpp.return_names(f)
    return f'std::tie({", ".join(names)})'

def get_return_value(f: NativeFunction) -> str:
    names = cpp.return_names(f)
    if len(f.func.returns) == 1:
        return names[0]
    if f.func.kind() == SchemaKind.out:
        return f'std::forward_as_tuple({", ".join(names)})'
    else:
        moved = ", ".join(f'std::move({name})' for name in names)
        return f'std::make_tuple({moved})'

TRACE_DISPATCH = CodeTemplate("""\
${assign_return_values}at::redispatch::${api_name}(${unpacked_args});""")

def emit_trace_body(f: NativeFunction) -> List[str]:
    trace_body: List[str] = []

    trace_body.append(format_prerecord_trace(f))
    trace_body.append(declare_returned_variables(f))

    dispatcher_sig = DispatcherSignature.from_schema(f.func)
    dispatcher_exprs = dispatcher_sig.exprs()

    # code-generated tracing kernels plumb and recompute dispatch keys directly through the kernel for performance.
    # See Note [Plumbing Keys Through The Dispatcher] for details.
    dispatch_key_set = 'ks & c10::DispatchKeySet(c10::DispatchKeySet::FULL_AFTER, c10::DispatchKey::Tracer)'
    redispatch_args = ', '.join([dispatch_key_set] + [a.expr for a in dispatcher_exprs])

    assign_return_values = f'{tie_return_values(f)} = ' \
                           if f.func.kind() == SchemaKind.functional and f.func.returns else ''

    # Note that this calls the slow, dispatching variants of manual_cpp_binding ops.
    # We could probably work harder to ensure that the fast variants are called instead, but the perf benefit would be minimal.
    sig_group = CppSignatureGroup.from_native_function(f, method=False, fallback_binding=f.manual_cpp_binding)
    if sig_group.faithful_signature is not None:
        api_name = sig_group.faithful_signature.name()
    else:
        api_name = sig_group.signature.name()

    trace_body.append(TRACE_DISPATCH.substitute(
        assign_return_values=assign_return_values,
        api_name=api_name,
        unpacked_args=redispatch_args,
    ))

    trace_body.append(format_postrecord_trace(f))
    if f.func.returns:
        trace_body.append(f'return {get_return_value(f)};')
    return trace_body

METHOD_DEFINITION = CodeTemplate("""\
${return_type} ${type_wrapper_name}(${formals}) {
  ${type_definition_body}
}
""")

def type_wrapper_name(f: NativeFunction) -> str:
    if f.func.name.overload_name:
        return f'{cpp.name(f.func)}_{f.func.name.overload_name}'
    else:
        return cpp.name(f.func)

@with_native_function
def method_definition(f: NativeFunction) -> Optional[str]:
    if cpp.name(f.func) in MANUAL_TRACER:
        return None

    formals = ', '.join(
        # code-generated tracing kernels plumb and recompute dispatch keys directly through the kernel for performance.
        # See Note [Plumbing Keys Through The Dispatcher] for details.
        ['c10::DispatchKeySet ks'] +
        [f'{cpp.argument_type(a, binds="__placeholder__").cpp_type()} {a.name}'
            for a in f.func.schema_order_arguments()]
    )

    return METHOD_DEFINITION.substitute(
        return_type=cpp.returns_type(f.func.returns),
        type_wrapper_name=type_wrapper_name(f),
        formals=formals,
        type_definition_body=emit_trace_body(f),
    )

WRAPPER_REGISTRATION = CodeTemplate("""\
m.impl("${name}",
       TORCH_FN(${class_type}::${type_wrapper_name})
);
""")

@with_native_function
def method_registration(f: NativeFunction) -> Optional[str]:
    if cpp.name(f.func) in MANUAL_TRACER:
        return None

    return WRAPPER_REGISTRATION.substitute(
        name=f.func.name,
        type_wrapper_name=type_wrapper_name(f),
        class_type='TraceType',
    )

def gen_trace_type_shard(
    fm: FileManager, native_functions: Sequence[NativeFunction], suffix: str
) -> None:
    fm.write_with_template('TraceType%s.cpp' % suffix, 'TraceType.cpp', lambda: {
        'generated_comment': '@' + f'generated from {fm.template_dir}/TraceType.cpp',
        'trace_method_definitions': list(mapMaybe(method_definition, native_functions)),
        'trace_wrapper_registrations': list(mapMaybe(method_registration, native_functions)),
    })

def gen_trace_type(out: str, native_yaml_path: str, template_path: str) -> None:
    # NOTE: see Note [Sharded File] at the top of the VariableType.cpp
    # template regarding sharding of the generated files.
    num_shards = 5
    shards: List[List[NativeFunction]] = [[] for _ in range(num_shards)]

    # functions are assigned arbitrarily but stably to a file based on hash
    native_functions = list(sorted(parse_native_yaml(native_yaml_path), key=lambda f: cpp.name(f.func)))
    for f in native_functions:
        x = sum(ord(c) for c in cpp.name(f.func)) % num_shards
        shards[x].append(f)

    fm = FileManager(install_dir=out, template_dir=template_path, dry_run=False)
    for i, shard in enumerate(shards):
        gen_trace_type_shard(fm, shard, '_%d' % i)
    gen_trace_type_shard(fm, native_functions, 'Everything')
