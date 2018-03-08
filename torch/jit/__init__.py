import torch._C
from torch import Tensor
from torch.autograd import Variable, function
from torch.nn import Module, ParameterList, Parameter
from torch.jit.frontend import get_jit_ast
from torch._six import raise_from, with_metaclass
from collections import defaultdict, OrderedDict, namedtuple
import sys
import warnings
import itertools
import weakref
import types
import contextlib
import os
import functools
import inspect
import copy


_flatten = torch._C._jit_flatten
_unflatten = torch._C._jit_unflatten
_jit_script_compile = torch._C._jit_script_compile

# This global variable is set when we are tracing a *forwards* computation.
# It is intended to be a cheap way to test if tracing has occurred, before
# doing the slower path using `get_tracing_state` (below.)
_tracing = False


def get_tracing_state(args):
    if not torch._C._is_tracing(args):
        return None
    return torch._C._get_tracing_state(args)


@contextlib.contextmanager
def scope(scope_name, *vars):
    tracing_state = get_tracing_state(vars)
    if tracing_state:
        tracing_state.push_scope(scope_name)
    try:
        yield
    finally:
        if tracing_state:
            tracing_state.pop_scope()


def compile(arg=None, nderivs=1, optimize=True, enabled=True):
    """
    Decorator which marks a function or module class as eligible for
    just-in-time compilation.  The next time the function/module is executed, it
    is traced, and the trace is compiled into an optimized representation which
    is run in lieu of the original Python code upon subsequent invocations of
    the function/module.

    .. note::

        A JIT compiled function/module may be compiled multiple times, as
        different inputs can result in different traces.  Currently, the
        JIT compiler conservatively assumes the trace may change if the
        `size` or `requires_grad` of `Variable` inputs change, or if
        any of the non-Variable inputs change.  For example, if you JIT
        compile an RNN which takes the number of hidden units as a parameter,
        we will compile a trace for every RNN length you use at runtime.

        When a module class is JIT compiled, each instantiation of the module
        gets a separate trace cache.

    .. warning::

        Just-in-time compilation currently only works for functions/modules
        which are not data dependent (e.g., have conditionals on data in
        tensors) and do not have any untracked external dependencies (e.g.,
        perform input/output or access global variables). If you trace such
        models, you will silently get incorrect results on subsequent
        invocations of the model.

    Keyword arguments:
        nderivs (int, optional): the number of derivatives which this function/module
            will be used with.  You MUST accurately specify this number: set it too
            low and you will see an error when you attempt to run `backward`;
            set it too high, and the function/module will never be compiled
            (as we always wait to see all derivatives before compiling.)
            Default: 1 (i.e., we will compile forwards and backwards, but not
            double-backwards).
        optimize (bool, optional): whether or not to apply optimizations.  Default: ``True``.

    Debug arguments:
        time (bool, optional): if ``True``, whenever we execute the model in question, we
            will also print out some timing information for how long the model
            took to execute.  At the moment, there are three types of timings we
            emit:

                - unoptimized: the time it took to execute the vanilla Python
                  model.  This only occurs when tracing is disabled, e.g., via
                  `enabled=False`

                - tracing: the time it took to execute the vanilla Python model
                  with tracing enabled.

                - optimized: the time it took to execute the optimized model.

            At the moment, all of these timings are for the forward pass only.
            Default: ``False``.
        enabled (bool, optional): if ``False``, compilation is disabled and you
            will get back your original model.  This is a convenient way to
            disable tracing without having to delete the annotation. Default: ``True``.

    Example: Compile as class decorator.

        >>> @jit.compile
        >>> class MyModel(nn.Module):
        >>>     ...
        >>> model = MyModel()
        >>> out1 = model(x)        # interpreted run
        >>> out1.sum().backward()  # won't compile without this line
        >>> out2 = model(x)        # compiled run
        >>> out2.sum().backward()  # also compiled

    Example: Compile forward pass only as class decorator.

        >>> @jit.compile(nderivs=0)
        >>> class MyModel(nn.Module):
        >>>     ...
        >>> model = MyModel()
        >>> out1 = model(x)        # interpreted run
        >>> out2 = model(x)        # compiled run

    Example: Compile as function decorator.  The same modes of use for the class
    decorator are also supported for functions; however, the decorated
    function must declare *all* Variable inputs in its arguments.

        >>> @jit.compile
        >>> def f(x):
        >>>     return x * 2
    """
    def _compile(arg):
        if inspect.isclass(arg):
            # NB: It might seem natural to create a subclass here, rather than
            # make a copy of the class to insert the mixin.  Unfortunately, this
            # will break many user classes.  Suppose you have:
            #
            #     @torch.jit.compile
            #     class Foo(Module):
            #         def __init__(self):
            #             super(Foo, self).__init__() # Python 2 syntax!
            #
            # within the class definition, 'Foo' refers to the *decorated*
            # class, not the undecorated class.  This is bad juju if the
            # decorator returns a subclass, since super(Foo, self) is going to
            # refer to the *undecorated* Foo (and thus you have an infinite
            # loop.)  Python 3's argument-less super() does not have this
            # problem, but in general we cannot ask users to rewrite their code.
            #
            # If we create a *copy* of the class (unrelated to the class the
            # user passed in), this problem goes away, because the class
            # __init__ is a part of is indeed Foo.

            old_init = arg.__init__
            # Python 2 has a concept of unbound methods, which are returned when
            # you take a method form a class. They behave just like regular functions,
            # but check the type of the first argument (self). We don't want this here,
            # because self in our __init__ will be an instance of this new class.
            # Python 3 already returns a plain function, so nothing has to be done.
            if sys.version_info[0] == 2:
                old_init = old_init.im_func

            def __init__(self, *args, **kwargs):
                torch._C.CompiledFunction.__init__(self,
                                                   nderivs, optimize, enabled,
                                                   self.forward,
                                                   arg.__name__)
                try:
                    old_init(self, *args, **kwargs)
                except TypeError as e:
                    # If this fails here, the user probably didn't use this as a class decorator
                    if "super" in str(e):
                        raise_from(TypeError("torch.jit.compile must be used as a class decorator; "
                                             "using it on an already defined class is not valid."
                                             "\n\nOriginal error: {}".format(str(e))), e)
                    else:
                        raise
                # NOTE: This can't be done in CompiledFunction constructor,
                # because self.parameters() isn't well defined by then
                # (Module constructor hasn't run yet).
                self.set_captured_vars(list(self.parameters()))

            new_dict = dict(arg.__dict__)
            new_dict['__init__'] = __init__
            new_dict['__call__'] = torch._C.CompiledFunction.__call__
            # NOTE: we don't need to override casting methods, because we only capture
            # parameters, and they mutate their data in-place.
            return type(arg.__name__,
                        arg.__bases__ + (torch._C.CompiledFunction,),
                        new_dict)
        elif isinstance(arg, Module):
            # It requires work to compile module instances, because you would
            # like the resulting compiled module to look just like the uncompiled
            # version; actually achieving this requires a bit of fanciness.
            # So for now, we just only support the class mechanism.
            raise TypeError("Compiling model instances is not supported.  "
                            "Use @torch.jit.compile on a class instead.")
        elif callable(arg):
            compiled_fn = torch._C.CompiledFunction(nderivs, optimize, enabled,
                                                    arg, arg.__name__)
            return compiled_fn
        else:
            raise TypeError("Cannot handle arg with type {}".format(type(arg)))
    # Make empty parenthesis optional
    if arg is None:
        return _compile
    else:
        return _compile(arg)


def get_trace_graph(f, args=tuple(), kwargs=None, nderivs=0):
    """
    Trace a function or model, returning a tuple consisting of the both the
    *trace* of an execution, as well as the original return value.

    Tracing is guaranteed not to change the semantics of the function/module
    that is traced.

    Arguments:
        f (torch.nn.Module or function): the function or module
            to be traced.
        args (tuple or Variable): the positional arguments to pass to the
            function/module to be traced.  A non-tuple is assumed to
            be a single positional argument to be passed to the model.
        kwargs (dict): the keyword arguments to pass to the function/module
            to be traced.
        nderivs (int, default 0): the number of derivatives to trace.
            Traces of derivatives are recorded into the same trace returned
            after executing the `forward` of the resulting module, but
            are not present until you run `backward()` (an appropriate
            number of times) on the resulting model.

    Example: Trace the forwards pass only.

        >>> trace, out = jit.trace(nn.LSTMCell(), (input, hidden))
        >>> print(trace)

    Example: Trace the backwards pass too.

        >>> trace, out = jit.trace(nn.LSTMCell(), (input, hidden), nderivs=1)
        >>> out.sum().backward()
        >>> print(trace)
    """
    if kwargs is None:
        kwargs = {}
    if not isinstance(args, tuple):
        args = (args,)
    return LegacyTracedModule(f, nderivs=nderivs)(*args, **kwargs)


def _unique_state_dict(module, keep_vars=False):
    state_dict = module.state_dict(keep_vars=keep_vars)
    filtered_dict = type(state_dict)()
    seen_ids = set()
    for k, v in state_dict.items():
        if id(v) in seen_ids:
            continue
        seen_ids.add(id(v))
        filtered_dict[k] = v
    return filtered_dict


class LegacyTracedModule(Module):
    def __init__(self, inner, nderivs=0):
        super(LegacyTracedModule, self).__init__()
        # inner may be a Module, or it may be an arbitrary callable
        # If it's a Module, we get its parameters automatically, which lets
        # us avoid a special casing functions versus modules.
        self.inner = inner
        self.nderivs = nderivs

    def forward(self, *args):
        global _tracing
        in_vars, in_desc = _flatten(args)
        # NOTE: use full state, because we need it for BatchNorm export
        # This differs from the compiler path, which doesn't support it at the moment.
        module_state = list(_unique_state_dict(self, keep_vars=True).values())
        trace, all_trace_inputs = torch._C._tracer_enter(in_vars + module_state, self.nderivs)
        _tracing = True
        trace_inputs = _unflatten(all_trace_inputs[:len(in_vars)], in_desc)
        out = self.inner(*trace_inputs)
        out_vars, _ = _flatten(out)
        _tracing = False
        torch._C._tracer_exit(out_vars)
        return trace, out


def _clone_inputs(args):
    def clone_input(a):
        if a is None:
            return None
        elif isinstance(a, Variable):
            v = Variable(a.data.clone(), requires_grad=a.requires_grad)
            if a.grad is not None:
                v.grad = clone_input(v.grad)
            return v
        else:
            return a.clone()
    return function._nested_map(lambda o: isinstance(o, Variable) or torch.is_tensor(o),
                                clone_input, condition_msg="Variables")(args)


# This is purely for developer debugging.  We are not going to advertise it.
_JIT_DUMP = os.environ.get('PYTORCH_JIT_DUMP', False)
_JIT_TIME = os.environ.get('PYTORCH_JIT_TIME', False)  # CUDA-only timing
_JIT_DISABLE = os.environ.get('PYTORCH_JIT_DISABLE', False)
_JIT_STATS = os.environ.get('PYTORCH_JIT_STATS', False)


def _dump_trace(trace_name, pass_name, input_key, trace):
    if not _JIT_DUMP:
        return

    import torch.contrib._graph_vis as graph_vis

    filename = "{}_{}".format(trace_name, pass_name)
    # TODO: Also paste out the backtrace when the trace was compiled
    # (and maybe also when it was run?)
    with open(filename + ".ir", "w") as f:
        f.write("Input key: {}\n\n{}".format(input_key, str(trace)))
    graph_vis.write(trace.graph(), filename + ".html")


@contextlib.contextmanager
def _time(trace_name, name, time=True):
    if (not _JIT_TIME and not time) or not torch.cuda.is_available():
        yield
        return
    stream = torch.cuda.current_stream()
    start = torch.cuda.Event(enable_timing=True)
    end = torch.cuda.Event(enable_timing=True)
    stream.record_event(start)
    try:
        yield
    finally:
        stream.record_event(end)
        end.synchronize()
        print("{} {} time: {} ms".format(trace_name, name, start.elapsed_time(end)))


def verify(model, args, loss_fn=torch.sum, devices=None):
    """
    Verify that a JIT compiled model has the same behavior as its uncompiled
    version along with its backwards pass.  If your model returns multiple
    outputs, you must also specify a `loss_fn` to produce a loss for which
    the backwards will be computed.

    This function has side-effects (e.g., it executes your model / saves and loads
    parameters), so don't expect the model to come out exactly the same as what
    you passed in.

    Arguments:
        model (compiled torch.nn.Module or function): the module/function to be
            verified.  The module/function definition MUST have been decorated with
            `@torch.jit.compile`.
        args (tuple or Variable): the positional arguments to pass to the
            compiled function/module to be verified.  A non-tuple is assumed to
            be a single positional argument to be passed to the model.
        loss_fn (function, optional): the loss function to be applied to
            the output of the model, before backwards is invoked.  By default,
            we assume that a model returns a single result, and we :func:`torch.sum`
            before calling backwards; if this is inappropriate, you can pass your
            own loss function.  Note that if a model returns a tuple of results,
            these are passed as separate positional arguments to `loss_fn`.
        devices (iterable of device IDs, optional): the GPU devices which the
            compiled module will be run on.  This determines the RNG state we
            must save when running both compiled and uncompiled versions of the model.
    """
    # TODO: In principle, we track device information in our trace, so it
    # should be possible to check if our execution actually obeyed the 'devices'
    # the user provided.

    # TODO: Consider adding a utility function to torch.jit to test
    # for this case
    if not isinstance(model, torch._C.CompiledFunction):
        raise TypeError("Cannot verify an uncompiled module.  Add @torch.jit.compile to compile it")
    is_module = isinstance(model, Module)

    if not isinstance(args, tuple):
        args = (args,)

    saved_args = _clone_inputs(args)
    if is_module:
        saved_state = copy.deepcopy(model.state_dict())

    def run_fwd_bwd(args, force_trace=False, assert_compiled=False):
        params = list(model.parameters()) if is_module else []
        in_vars, _ = _flatten((args, params))
        # We use a special API to reset the trace and compile it from scratch.
        compiled_fn = model
        if force_trace:
            compiled_fn.clear_cache()
        if assert_compiled:
            hits = compiled_fn.hits
        out = model(*args)
        if assert_compiled and compiled_fn.hits == hits:
            raise RuntimeError("failed to use the compiled function")
        if not isinstance(out, tuple):
            out = (out, )
        if loss_fn == torch.sum and len(out) != 1:
            raise ValueError(("Model returns {} outputs, but default loss function "
                             "(torch.sum) can only handle a single output").format(len(out)))
        out_vars, _ = _flatten(out)
        saved_outs = [v.data.clone() for v in out_vars]
        loss = loss_fn(*out)
        grads = torch.autograd.grad([loss], in_vars)
        # TODO: I'm not sure if the clone here is necessary but it is safer
        saved_grads = [v.data.clone() for v in grads]
        return (saved_outs, saved_grads)

    with torch.random.fork_rng(devices, _caller="torch.jit.verify"):
        uncompiled_outs, uncompiled_grads = run_fwd_bwd(args, force_trace=True)
        assert model.has_trace_for(*args)

    if is_module:
        model.load_state_dict(saved_state)
    compiled_outs, compiled_grads = run_fwd_bwd(args, assert_compiled=True)

    _verify_equal(uncompiled_outs, compiled_outs)
    _verify_equal(uncompiled_grads, compiled_grads)


def _verify_equal(xs, ys):
    for x, y in zip(xs, ys):
        if x.sub(y).abs().max() > 1e-6:
            raise RuntimeError("JIT and real computation mismatch")


def trace(*args, **kwargs):
    """
    Trace a function and return an executable trace that will be optimized
    using just-in-time compilation.

    .. warning::

        Just-in-time compilation currently only works for functions/modules
        which are not data dependent (e.g., have conditionals on data in
        tensors) and do not have any untracked external dependencies (e.g.,
        perform input/output or access global variables). If you trace such
        models, you will silently get incorrect results on subsequent
        invocations of the model.

    Arg:
        *args - a list of example tensors that will be passed to the function
                as inputs while tracing. The resulting trace can be run with
                inputs of different types and shapes assuming the traced operations
                support those types and shapes.

    Keyword arguments:
        optimize (bool, optional): whether or not to apply optimizations.  Default: ``True``.

        >>> @jit.trace(torch.autograd.Variable(torch.rand(1)))
        >>> def f(x):
        >>>     return x * 2
    """
    def wrapper(func):
        executor_options = {'optimize': True}
        for name in executor_options:
            executor_options[name] = kwargs.pop(name, executor_options[name])
        if isinstance(func, torch.nn.Module):
            captures = list(func.state_dict(keep_vars=True).values())
            # TODO: support shared parameters
            if len(set(map(id, captures))) != len(list(map(id, captures))):
                raise ValueError("TracedModules don't support parameter sharing between modules")
            executor = torch._C.GraphExecutor(func, args, captures=captures, **executor_options)
            return TracedModule(func, executor)
        else:
            return torch._C.GraphExecutor(func, args, **executor_options)
    return wrapper


class TracedModuleBase(torch.nn.Module):
    __frozen = False

    def __init__(self, orig):
        super(TracedModuleBase, self).__init__()

        self.training = orig.training
        for name, param in orig._parameters.items():
            if param is not None:
                self._parameters[name] = param
        for name, buf in orig._buffers.items():
            if param is not None:
                self._buffers[name] = buf
        self._orig_class = type(orig)

        if orig._backward_hooks or orig._forward_hooks or orig._forward_pre_hooks:
            raise ValueError("Modules that have hooks assigned can't be compiled")

        # XXX: submodules can't be initialized here, because we don't know what the root is

    def _freeze(self):
        self.__frozen = True

    def __setattr__(self, name, value):
        if not self.__frozen:
            return super(TracedModuleBase, self).__setattr__(name, value)
        if name in self._parameters or name in self._buffers:
            result = super(TracedModuleBase, self).__setattr__(name, value)
            self._recompute_captures()
            return result
        raise RuntimeError("Only parameters and buffers of compiled modules can be re-assigned.")

    def __delattr__(self, name):
        raise RuntimeError("Deleting attributes of TracedModules isn't supported")

    def __getstate__(self):
        raise RuntimeError("TracedModules aren't picklable")

    def _recompute_captures(self):
        raise NotImplementedError()

    def register_parameter(self, name, param):
        if name not in self._parameters:
            raise RuntimeError("Can't add new parameters to TracedModules")
        if param is None:
            raise RuntimeError("Can't set parameters to None in TracedModules")
        super(TracedModuleBase, self).register_parameter(name, param)

    def load_state_dict(self, state):
        super(TracedModuleBase, self).load_state_dict(state)
        # NB: this is not strictly necessary, because load_state_dict is copying, but
        # that's an implementation detail that I don't want to depend on
        self._recompute_captures()

    def _get_name(self):
        return 'TracedModule[' + self._orig_class.__name__ + ']'


class TracedSubmodule(TracedModuleBase):

    def __init__(self, orig, root):
        super(TracedSubmodule, self).__init__(orig)
        for name, submodule in orig._modules.items():
            self._modules[name] = TracedSubmodule(submodule, root)
        self._root = weakref.ref(root)
        self._freeze()

    def _recompute_captures(self):
        root = self._root()
        if root is None:
            raise RuntimeError("Submodules of TracedModule can't work without keeping "
                               "the main one in scope")
        root._recompute_captures()

    def __call__(self, *args, **kwargs):
        raise RuntimeError("Only the top-level compiled module can be called")


class TracedModule(TracedModuleBase):

    def __init__(self, orig, executor):
        super(TracedModule, self).__init__(orig)

        for name, submodule in orig._modules.items():
            self._modules[name] = TracedSubmodule(submodule, root=self)

        self._executor = executor
        self._recompute_captures()
        self._freeze()

    def __call__(self, *args):
        return self._executor(*args)

    def _recompute_captures(self):
        self._executor.set_captures(*self.state_dict().values())

    def _apply(self, fn):
        for module in itertools.chain((self,), self.modules()):
            for param in module._parameters.values():
                param.data = fn(param.data)
                if param._grad is not None:
                    param._grad.data = fn(param._grad.data)

            for key, buf in module._buffers.items():
                module._buffers[key] = fn(buf)
        self._recompute_captures()
        return self


def _get_methods(cls):
    import inspect
    # In Python 3 unbound methods are functions, but in Python 2 they are methods
    return inspect.getmembers(cls, predicate=lambda x: inspect.isfunction(x) or inspect.ismethod(x))

_compiled_methods_whitelist = {
    'cpu', 'cuda', 'double', 'float', 'half', 'modules', 'named_children',
    'named_modules', 'named_parameters', 'parameters', 'state_dict', 'type',
    'zero_grad',
}


def _make_fail(name):
    def fail(self, *args, **kwargs):
        raise RuntimeError(name + " is not supported on TracedModules")
    return fail
for name, method in _get_methods(torch.nn.Module):
    if name.startswith('__'):
        continue
    if name not in TracedModuleBase.__dict__ and name not in _compiled_methods_whitelist:
        setattr(TracedModuleBase, method.__name__, _make_fail(name))


def createResolutionCallback(frame_id=2):
    frame = inspect.stack()[frame_id][0]

    def env(key):
        if key in frame.f_locals:
            return frame.f_locals[key]
        elif key in frame.f_globals:
            return frame.f_globals[key]
        else:
            return None

    return env


class CompilationUnit(object):
    def __init__(self, lang=None, optimize=True):
        self.module = torch._C.ScriptModule(optimize)
        if lang is not None:
            self.define(lang, frame_id=3)
        self.optimize = optimize

    def define(self, lang, rcb=None, frame_id=2):
        if not rcb:
            rcb = createResolutionCallback(frame_id)
        self.module._define(lang, rcb, False)

    def __getattr__(self, attr):
        return self.module._get_method(attr)


def script(fn):
    rcb = createResolutionCallback()
    ast = get_jit_ast(fn)
    graph = _jit_script_compile(ast, rcb)
    return torch._C.GraphExecutor(graph, True)


ScriptMethodStub = namedtuple('ScriptMethodStub', ('resolution_callback', 'ast'))


def script_method(fn):
    return ScriptMethodStub(createResolutionCallback(), get_jit_ast(fn))


class ScriptMeta(type(torch._C.ScriptModule)):
    # this has to inherit from pybind11's metaclass otherwise we get
    # issues because ScriptModule inherits from torch._C.ScriptModule,
    # a pybind11 type
    def __init__(cls, name, bases, attrs):
        # find all the script methods
        methods = []
        for k, v in sorted(attrs.items()):
            if isinstance(v, ScriptMethodStub):
                delattr(cls, k)
                methods.append(v)
        if len(methods) > 0 and '__init__' in attrs:
            # after the user's __init__ register all the script methods
            # with the module
            original_init = cls.__init__

            def init_then_register(self, *args, **kwargs):
                original_init(self, *args, **kwargs)
                for m in methods:
                    self._create_method(m.ast, m.resolution_callback)

            cls.__init__ = init_then_register
        return super(ScriptMeta, cls).__init__(name, bases, attrs)


class ScriptModule(with_metaclass(ScriptMeta, torch._C.ScriptModule)):

    def __init__(self, optimize=True):
        super(ScriptModule, self).__init__(optimize)

    def __setattr__(self, name, value):
        if isinstance(value, Parameter):
            self._register_or_set_parameter(name, value)
        elif isinstance(value, ScriptModule):
            self._register_module(name, value)
            # note: script modules are subclassed in python and the
            # C++ script::Module class will not hold references to them
            # to ensure that you always get the same python value here
            # we store it as a native attribute _in addition to_
            # registering it with the C++ script::Module
            object.__setattr__(self, name, value)
        else:
            object.__setattr__(self, name, value)

    def __getattr__(self, attr):
        r = self._get_attribute(attr)
        if r is None:
            raise AttributeError("'{}' object has no attribute '{}'".format(self.__class__.__name__, attr))
        return r

    def define(self, lang):
        rcb = createResolutionCallback()
        self._define(lang, rcb, True)


if not torch._C._jit_init():
    raise RuntimeError("JIT initialization failed")
