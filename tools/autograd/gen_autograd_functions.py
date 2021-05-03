# Generates C++ autograd functions for the derivatives of ATen operations
#
# This writes two files:
#  Functions.h/cpp: subclasses of autograd::Node
#  python_functions.h/cpp: Python bindings for the above classes
#
from .gen_inplace_or_view_type import VIEW_FUNCTIONS

from typing import List, Sequence, Tuple

from tools.codegen.api.autograd import (Derivative, DifferentiabilityInfo,
                                        SavedAttribute, uses_retain_variables,
                                        uses_single_grad)
from tools.codegen.api.types import (Binding, BaseCType, OptionalCType, tensorT, intT,
                                     doubleT, scalarT, stringT, boolT, intArrayRefT,
                                     tensorListT, MutRefCType, ListCType, ArrayRefCType)
from tools.codegen.code_template import CodeTemplate
from tools.codegen.gen import FileManager
from tools.codegen.model import Argument

FUNCTION_DECLARATION = CodeTemplate("""\
struct TORCH_API ${op} : public ${superclass} {
  using ${superclass}::${superclass};
  variable_list apply(variable_list&& grads) override;
  std::string name() const override { return "${op}"; }
  void release_variables() override {
    ${thread_lock}
    ${release_variables}
  }
  ${will_release_variables}
  ${saved_variables}
  ${saved_list_sizes}
};
""")

WILL_RELEASE_VARIABLES = CodeTemplate("""\
bool retain_variables = true;
void will_release_variables() override {
  retain_variables = false;
}
""")

FUNCTION_DEFINITION = CodeTemplate("""\
variable_list ${op}::apply(variable_list&& grads) {
  ${thread_lock}
  ${asserts}
  IndexRangeGenerator gen;
  ${compute_index_ranges}
  variable_list grad_inputs(gen.size());
  ${body}
  return grad_inputs;
}
""")

GRAD_INPUT_MASK = CodeTemplate("""\
  auto grad_input_mask = std::array<bool, ${n}>{
    ${masks}
  };\
""")

DERIVATIVE_SINGLE = CodeTemplate("""\
if (should_compute_output({ ${name}_ix })) {
  auto grad_result = ${derivative};
  copy_range(grad_inputs, ${name}_ix, grad_result);
}
""")

DERIVATIVE_MULTI_COPY_RANGE = CodeTemplate("""\
  if (should_compute_output({ ${name}_ix })) {
    copy_range(grad_inputs, ${name}_ix, std::get<${i}>(grad_result));
  }
""")

DERIVATIVE_MULTI = CodeTemplate("""\
if (should_compute_output({ ${idx_ranges} })) {
  ${grad_input_mask}
  auto grad_result = ${derivative};
  ${copy_ranges}
}
""")

# Generates python bindings
#
# This generates the definitions for:
#   (1) The PyTypeObject for each backward grad_fn subclassing Node
#   (2) The entry for PyTypeObject's tp_getset slot (an array of PyGetSetDef structs)
#       We generate one PyGetSetDef struct for each of grad_fn's saved inputs and outputs
#       Each PyGetSetDef has a function ptr to a getter, also defined here (3).
#   (3) Getters for each of grad_fn's saved inputs and outputs.
#
PY_FUNCTION_DEFINITION = CodeTemplate("""\
static PyTypeObject ${op}Class;
addClass<${op}>(${op}Class, "${op}", ${op}_properties);
""")

PY_FUNCTION_PROPS_AND_GETTERS = CodeTemplate("""\
${all_getter_definitions}

static struct PyGetSetDef ${op}_properties[] = {
  THP_FUNCTION_DEFAULT_PROPERTIES,
  ${all_getsetdef_structs}
  {nullptr} /* sentinel */
};

""")

PY_GETSETDEF_STRUCT = CodeTemplate("""\
{(char*)"_saved_${name}", (getter)THP${op}_${name}_getter, nullptr, nullptr, nullptr}""")

# Getter templates
GETTER_DEFINITION = CodeTemplate("""\
PyObject* THP${op}_${name}_getter(THPCppFunction *self, void *_unused) {
  HANDLE_TH_ERRORS
  auto prop = static_cast<${op}*>(self->cdata.get())->${name};
  ${body}
  END_HANDLE_TH_ERRORS
}
""")

GETTER_DEFINITION_SAVEDVAR = CodeTemplate("""\
PyObject* THP${op}_${name}_getter(THPCppFunction *self, void *_unused) {
  HANDLE_TH_ERRORS
  const auto& prop = static_cast<${op}*>(self->cdata.get())->${name}_;
  ${body}
  END_HANDLE_TH_ERRORS
}
""")

GETTER_DEFINITION_VEC_SAVEDVAR = CodeTemplate("""\
PyObject* THP${op}_${name}_getter(THPCppFunction *self, void *_unused) {
  HANDLE_TH_ERRORS
  const auto *node = static_cast<${op}*>(self->cdata.get());
  const auto& prop = node->${name}_;
  if (node->${name}_released_) {
    PyErr_SetString(PyExc_RuntimeError, ERR_BACKWARD_TWICE);
    return nullptr;
  }
  ${body}
  END_HANDLE_TH_ERRORS
}
""")

GETTER_DEFINITION_OPT = CodeTemplate("""\
PyObject* THP${op}_${name}_getter(THPCppFunction *self, void *_unused) {
  HANDLE_TH_ERRORS
  auto opt_prop = static_cast<${op}*>(self->cdata.get())->${name};
  if (!opt_prop.has_value()) {
    Py_RETURN_NONE;
  }
  auto prop = opt_prop.value();
  ${body}
  END_HANDLE_TH_ERRORS
}
""")

GETTER_DEFINITION_OPT_ARRAYREF = CodeTemplate("""\
PyObject* THP${op}_${name}_getter(THPCppFunction *self, void *_unused) {
  HANDLE_TH_ERRORS
  auto opt_prop = static_cast<${op}*>(self->cdata.get())->${name};
  if (!opt_prop.list.has_value()) {
    Py_RETURN_NONE;
  }
  auto prop = opt_prop.list.value();
  ${body}
  END_HANDLE_TH_ERRORS
}
""")

# Getter body
GETTER_BODY_SAVEDVAR = """\
return THPVariable_Wrap(prop.unpack(self->cdata));
"""

GETTER_BODY_VEC_SAVEDVAR = """\
PyObject* tup = PyTuple_New((Py_ssize_t) prop.size());
for (int i = 0; i < prop.size(); i++) {
  PyTuple_SetItem(tup, (Py_ssize_t) i, THPVariable_Wrap(prop[i].unpack(self->cdata)));
}
return tup;
"""

GETTER_BODY_ARRAYREF_LONG = """\
PyObject* tup = PyTuple_New((Py_ssize_t) prop.size());
for (int i = 0; i < prop.size(); i++) {
  PyTuple_SetItem(tup, (Py_ssize_t) i, PyLong_FromUnsignedLong((uint64_t) prop[i]));
}
return tup;
"""

GETTER_BODY_ARRAYREF_DOUBLE = """\
PyObject* tup = PyTuple_New((Py_ssize_t) prop.size());
for (int i = 0; i < prop.size(); i++) {
  PyTuple_SetItem(tup, (Py_ssize_t) i, PyFloat_FromDouble((double) prop[i]));
}
return tup;
"""

GETTER_BODY_INT64_T = """\
return PyLong_FromUnsignedLong((int64_t) prop);
"""

GETTER_BODY_DOUBLE = """\
return PyFloat_FromDouble((double) prop);
"""

GETTER_BODY_BOOL = """\
if (prop) {
  Py_RETURN_TRUE;
} else {
  Py_RETURN_FALSE;
}
"""

GETTER_BODY_STRING = """\
return PyUnicode_FromStringAndSize(prop.data(), prop.size());
"""

GETTER_BODY_SCALAR = """\
if (prop.isComplex()) {
  auto cprop = prop.to<c10::complex<double>>();
  return PyComplex_FromDoubles(cprop.real(), cprop.imag());
} else if (prop.isFloatingPoint()) {
  return PyFloat_FromDouble(prop.to<double>());
} else if (prop.isIntegral(/*includeBool=*/false)) {
  return PyLong_FromLong(prop.to<int64_t>());
} else if (prop.isBoolean()) {
  if (prop.to<bool>()) {
    Py_RETURN_TRUE;
  } else {
    Py_RETURN_FALSE;
  }
} else {
  PyErr_SetString(PyExc_RuntimeError, "Unknown scalar type");
  return nullptr;
}
"""

MISC_GETTER_DEFS = {
    OptionalCType(BaseCType(intT)): (GETTER_DEFINITION_OPT, GETTER_BODY_INT64_T),
    BaseCType(doubleT): (GETTER_DEFINITION, GETTER_BODY_DOUBLE),
    OptionalCType(BaseCType(doubleT)): (GETTER_DEFINITION_OPT, GETTER_BODY_DOUBLE),
    BaseCType(boolT): (GETTER_DEFINITION, GETTER_BODY_BOOL),
    BaseCType(stringT): (GETTER_DEFINITION, GETTER_BODY_STRING),
    OptionalCType(BaseCType(stringT)): (GETTER_DEFINITION_OPT, GETTER_BODY_STRING),
    BaseCType(scalarT): (GETTER_DEFINITION, GETTER_BODY_SCALAR),
    OptionalCType(BaseCType(scalarT)): (GETTER_DEFINITION_OPT, GETTER_BODY_SCALAR),
}

# These functions have backwards which cannot be traced, and so must have
# their backward functions traced opaquely.
# VIEW_FUNCTIONS are not traceable because they use as_strided, which
# has an untraceable backwards, see
# https://github.com/pytorch/pytorch/issues/4250
# TODO: This is probably not exhaustive, but it's a start
UNTRACEABLE_FUNCTIONS = VIEW_FUNCTIONS

def gen_autograd_functions_lib(
    out: str,
    differentiability_infos: Sequence[DifferentiabilityInfo],
    template_path: str,
) -> None:
    gen_autograd_functions(out, differentiability_infos, template_path, "Functions")

def gen_autograd_functions_python(
    out: str,
    differentiability_infos: Sequence[DifferentiabilityInfo],
    template_path: str,
) -> None:
    gen_autograd_functions(out, differentiability_infos, template_path, "python_functions")

def gen_autograd_functions(
    out: str,
    differentiability_infos: Sequence[DifferentiabilityInfo],
    template_path: str,
    file_basename: str,
) -> None:
    """Functions.h and Functions.cpp body

    These contain the auto-generated subclasses of torch::autograd::Node
    for each every differentiable torch function.
    """

    # only create an autograd function if we are actually going to calculate a derivative
    infos = list(filter(lambda info: info.args_with_derivatives, differentiability_infos))
    declarations = list(map(lambda f: process_function(f, FUNCTION_DECLARATION), infos))
    definitions = list(map(lambda f: process_function(f, FUNCTION_DEFINITION), infos))
    py_function_initializers = list(map(lambda f: process_function(f, PY_FUNCTION_DEFINITION), infos))
    py_function_props_and_getters = list(map(lambda f: process_function(f, PY_FUNCTION_PROPS_AND_GETTERS), infos))

    fm = FileManager(install_dir=out, template_dir=template_path, dry_run=False)
    for suffix in ['.h', '.cpp']:
        fname = file_basename + suffix
        fm.write_with_template(fname, fname, lambda: {
            'generated_comment': '@' + f'generated from {fm.template_dir}/' + fname,
            'autograd_function_declarations': declarations,
            'autograd_function_definitions': definitions,
            'py_function_initializers': py_function_initializers,
            'py_function_props_and_getters': py_function_props_and_getters
        })

def process_function(info: DifferentiabilityInfo, template: CodeTemplate) -> str:
    saved_variables: List[str] = []
    release_variables: List[str] = []
    saved_list_sizes: List[str] = []
    unpack: List[str] = []
    asserts: List[str] = []
    compute_index_ranges: List[str] = []
    getter_definitions: List[str] = []
    py_getsetdef_structs: List[str] = []

    for arg in info.args_with_derivatives:
        if arg.type == 'at::TensorList' or arg.type == 'const c10::List<c10::optional<at::Tensor>> &':
            size = f'{arg.name}_size_'
            saved_list_sizes.append(f'size_t {arg.name}_size_;')
        else:
            size = '1'
        compute_index_ranges.append(f'auto {arg.name}_ix = gen.range({size});')

    def save_var(var: SavedAttribute, is_output: bool) -> None:
        name = var.nctype.name
        type = var.nctype.type
        should_append_getsetdef = True

        if type == BaseCType(tensorT) or type == OptionalCType(BaseCType(tensorT)) or \
                type == MutRefCType(OptionalCType(BaseCType(tensorT))) or \
                (type == BaseCType(scalarT) and is_output):
            saved_variables.append(f'SavedVariable {name}_;')
            release_variables.append(f'{name}_.reset_data();')
            release_variables.append(f'{name}_.reset_grad_function();')
            ptr = 'shared_from_this()' if is_output else ''
            unpack.append(f'auto {name} = {name}_.unpack({ptr});')
            getter_definitions.append(GETTER_DEFINITION_SAVEDVAR.substitute(
                op=info.op, name=name, body=GETTER_BODY_SAVEDVAR))
        elif type == BaseCType(tensorListT):
            saved_variables.append(f'std::vector<SavedVariable> {name}_;')
            saved_variables.append(f'bool {name}_released_ = false;')
            # Just clear() is sufficient, we don't need to loop and clear each variable.
            # Because the SavedVariable owns a tensor and a grad_fn, removing the SavedVariable makes them go away as well.
            release_variables.append(f'{name}_.clear();')
            release_variables.append(f'{name}_released_ = true;')
            unpack.append(f'auto {name} = unpack_list({name}_);')
            asserts.append(f'TORCH_CHECK(!{name}_released_, ERR_BACKWARD_TWICE);')
            getter_definitions.append(GETTER_DEFINITION_VEC_SAVEDVAR.substitute(
                op=info.op, name=name, body=GETTER_BODY_VEC_SAVEDVAR))
        elif type == ListCType(OptionalCType(BaseCType(tensorT))):
            saved_variables.append(f'std::vector<SavedVariable> {name}_;')
            saved_variables.append(f'bool {name}_released_ = false;')
            # Just clear() is sufficient, we don't need to loop and clear each variable.
            # Because the SavedVariable owns a tensor and a grad_fn, removing the SavedVariable makes them go away as well.
            release_variables.append(f'{name}_.clear();')
            release_variables.append(f'{name}_released_ = true;')
            unpack.append(f'auto {name} = unpack_opt_list({name}_);')
            asserts.append(f'TORCH_CHECK(!{name}_released_, ERR_BACKWARD_TWICE);')
            getter_definitions.append(GETTER_DEFINITION_VEC_SAVEDVAR.substitute(
                op=info.op, name=name, body=GETTER_BODY_VEC_SAVEDVAR))
        elif type == BaseCType(intArrayRefT):
            saved_variables.append(f'std::vector<int64_t> {name};')
            getter_definitions.append(GETTER_DEFINITION.substitute(
                op=info.op, name=name, body=GETTER_BODY_ARRAYREF_LONG))
        elif type == OptionalCType(BaseCType(intArrayRefT)):
            saved_variables.append(f'c10::OptionalArray<int64_t> {name};')
            getter_definitions.append(GETTER_DEFINITION_OPT_ARRAYREF.substitute(
                op=info.op, name=name, body=GETTER_BODY_ARRAYREF_LONG))
        elif type == OptionalCType(ArrayRefCType(BaseCType(doubleT))):
            saved_variables.append(f'c10::OptionalArray<double> {name};')
            getter_definitions.append(GETTER_DEFINITION_OPT_ARRAYREF.substitute(
                op=info.op, name=name, body=GETTER_BODY_ARRAYREF_DOUBLE))
        elif type == BaseCType(intT):
            saved_variables.append(f'{type.cpp_type()} {name} = 0;')
            getter_definitions.append(GETTER_DEFINITION.substitute(
                op=info.op, name=name, body=GETTER_BODY_INT64_T))
        else:
            saved_variables.append(f'{type.cpp_type()} {name};')

            if type in MISC_GETTER_DEFS:
                getter_def, body = MISC_GETTER_DEFS[type]
                getter_definitions.append(getter_def.substitute(op=info.op, name=name, body=body))
            else:
                # Types we don't expose python bindings to yet:
                #   TypeAndSize, at::ScalarType, TensorOptions, TensorGeometry,
                #   std::vector<std::vector<int64_t>>, std::vector<at::ScalarType>
                should_append_getsetdef = False

        if should_append_getsetdef:
            py_getsetdef_structs.append(PY_GETSETDEF_STRUCT.substitute(op=info.op, name=name))

    for var in info.all_saved_inputs:
        save_var(var, is_output=False)
    for var in info.all_saved_outputs:
        save_var(var, is_output=True)

    # lock the mutex when we release variables and in Node::apply to protect thread safety
    # see Note [Thread Safety on Autograd Node]
    if len(release_variables) > 0:
        thread_lock = 'std::lock_guard<std::mutex> lock(mutex_);'
    else:
        thread_lock = ''

    if uses_retain_variables(info):
        will_release_variables = WILL_RELEASE_VARIABLES.substitute()
    else:
        will_release_variables = ''

    body: List[str] = []

    if uses_single_grad(info):
        body.append('auto& grad = grads[0];')

    def emit_derivative(
        derivative: Derivative,
        args_with_derivatives: Sequence[Binding],
    ) -> Tuple[bool, str]:
        formula = derivative.formula
        var_names = derivative.var_names
        if len(var_names) == 1:
            checks_any_grad_defined = False
            if 'not_implemented' not in formula:
                matching_args = [
                    arg for arg in args_with_derivatives
                    if arg.name == var_names[0]]
                if len(matching_args) == 1:
                    # We can add undefined grad support if the input variable is a Tensor
                    arg = matching_args[0]
                    if isinstance(arg.argument, Argument) and str(arg.argument.type) == 'Tensor':
                        formula = 'any_grad_defined ? (' + formula + ') : Tensor()'
                        checks_any_grad_defined = True
            return (checks_any_grad_defined,
                    DERIVATIVE_SINGLE.substitute(name=var_names[0], derivative=formula))
        else:
            if 'grad_input_mask' in formula:
                masks = [f'should_compute_output({{ {n}_ix }}),' for n in var_names]
                grad_input_mask = GRAD_INPUT_MASK.substitute(masks=masks, n=len(var_names))
            else:
                grad_input_mask = ''
            idx_ranges = ', '.join(f'{n}_ix' for n in var_names)
            copy_ranges: List[str] = []
            for i, n in enumerate(var_names):
                copy_ranges.append(DERIVATIVE_MULTI_COPY_RANGE.substitute(name=n, i=i))
            return False, DERIVATIVE_MULTI.substitute(
                idx_ranges=idx_ranges, copy_ranges=copy_ranges,
                derivative=formula,
                grad_input_mask=grad_input_mask)

    body.extend(unpack)
    need_any_grad_defined_var = False
    for derivative in info.derivatives:
        checks_any_grad_defined, derivative_text = emit_derivative(derivative, info.args_with_derivatives)
        body.append(derivative_text)
        need_any_grad_defined_var |= checks_any_grad_defined
    # Since single-output derivative formulas need to check if grads are
    # defined, only perform the check once, before all the formulas
    if need_any_grad_defined_var:
        body.insert(-len(info.derivatives),
                    'bool any_grad_defined = any_variable_defined(grads);')

    if info.name in UNTRACEABLE_FUNCTIONS:
        superclass = 'Node'
    else:
        superclass = 'TraceableFunction'

    all_getsetdef_structs = ",\n".join(py_getsetdef_structs) + "," if len(py_getsetdef_structs) != 0 else ""
    all_getter_definitions = "\n".join(getter_definitions)

    return template.substitute(
        op=info.op,
        compute_index_ranges=compute_index_ranges,
        saved_variables=saved_variables,
        release_variables=release_variables,
        saved_list_sizes=saved_list_sizes,
        asserts=asserts,
        thread_lock=thread_lock,
        will_release_variables=will_release_variables,
        body=body,
        superclass=superclass,
        all_getter_definitions=all_getter_definitions,
        all_getsetdef_structs=all_getsetdef_structs
    )
