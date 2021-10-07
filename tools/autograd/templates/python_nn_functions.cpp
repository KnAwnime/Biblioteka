// ${generated_comment}

#include "torch/csrc/Device.h"
#include "torch/csrc/DynamicTypes.h"
#include "torch/csrc/Exceptions.h"
#include "torch/csrc/autograd/python_nn_functions.h"
#include "torch/csrc/autograd/python_variable.h"
#include "torch/csrc/autograd/utils/wrap_outputs.h"
#include "torch/csrc/autograd/utils/python_arg_parsing.h"
#include "torch/csrc/utils/pycfunction_helpers.h"
#include "torch/csrc/utils/python_arg_parser.h"
#include "torch/csrc/utils/structseq.h"

using at::Tensor;
using at::Scalar;
using at::MemoryFormat;
using at::Generator;
using at::IntArrayRef;
using at::ArrayRef;

using namespace torch::autograd::utils;

namespace torch { namespace autograd {

// generated return_types start here
namespace {
  ${py_return_types}

  // hold onto generated return type.
  PyTypeObject* return_types[] = {
    ${py_return_types_array}
  };
}

static PyObject* THPNNVariableFunctionsModule = NULL;

static PyObject * THPVariable__parse_to(PyObject* module, PyObject* args, PyObject* kwargs)
{
  HANDLE_TH_ERRORS
  static PythonArgParser parser({
    "to(Device device=None, ScalarType dtype=None, bool non_blocking=False, bool copy=False, *, MemoryFormat? memory_format=None)",
    "to(ScalarType dtype, bool non_blocking=False, bool copy=False, *, MemoryFormat? memory_format=None)",
    "to(Tensor tensor, bool non_blocking=False, bool copy=False, *, MemoryFormat? memory_format=None)",
  });
  ParsedArgs<5> parsed_args;
  auto r = parser.parse(args, kwargs, parsed_args);
  if (r.has_torch_function()) {
    return handle_torch_function(r, args, kwargs, THPNNVariableFunctionsModule, "torch.nn");
  }
  auto parsed = parse_to_conversion(r, /*allow_copy*/ false); // we don't want copy for nn.Module.to
  auto& device = std::get<0>(parsed);
  auto& scalarType = std::get<1>(parsed);
  auto non_blocking = std::get<2>(parsed);
  auto opt_memory_format = std::get<4>(parsed);
  auto tuple = THPObjectPtr{PyTuple_New(4)};
  if (!tuple) throw python_error();
  if (device) {
    PyTuple_SET_ITEM(tuple.get(), 0, THPDevice_New(*device));
  } else {
    Py_INCREF(Py_None);
    PyTuple_SET_ITEM(tuple.get(), 0, Py_None);
  }
  if (scalarType) {
    PyTuple_SET_ITEM(tuple.get(), 1, torch::autograd::utils::wrap(torch::getTHPDtype(*scalarType)));
  } else {
    Py_INCREF(Py_None);
    PyTuple_SET_ITEM(tuple.get(), 1, Py_None);
  }
  PyTuple_SET_ITEM(tuple.get(), 2, torch::autograd::utils::wrap(non_blocking));
  if (opt_memory_format.has_value()) {
    PyTuple_SET_ITEM(tuple.get(), 3, THPMemoryFormat_New(opt_memory_format.value(), "unused_name"));
  } else {
    Py_INCREF(Py_None);
    PyTuple_SET_ITEM(tuple.get(), 3, Py_None);
  }
  return tuple.release();
  END_HANDLE_TH_ERRORS
}

// generated forward declarations start here

${py_forwards}

static PyMethodDef nn_functions[] = {
  {"_parse_to", castPyCFunctionWithKeywords(THPVariable__parse_to),
    METH_VARARGS | METH_KEYWORDS, nullptr},
  ${py_method_defs}
  {NULL}
};

void initNNFunctions(PyObject* module) {
  static struct PyModuleDef def = {
     PyModuleDef_HEAD_INIT,
     "torch._C._nn",
     NULL,
     -1,
     nn_functions
  };
  PyObject* nn = PyModule_Create(&def);
  THPNNVariableFunctionsModule = nn;
  if (!nn) {
    throw python_error();
  }
  // steals a reference to nn
  if (PyModule_AddObject(module, "_nn", nn) != 0) {
    throw python_error();
  }

  auto return_module = PyObject_GetAttrString(module, "_return_types");
  constexpr size_t num_returns = sizeof(return_types) / sizeof(return_types[0]);
  for (int i = 0; i < num_returns; i++) {
    PyModule_AddType(return_module, return_types[i]);
  }
}

// generated methods start here

${py_methods}

}} // namespace torch::autograd
