#include <torch/csrc/jit/backends/backend.h>
#include <torch/csrc/jit/backends/backend_preprocess.h>

#include <torch/csrc/jit/tensorexpr/graph_opt.h>
#include <torch/torch.h>
#include <xnnpack.h>

#include <ATen/core/List.h>
#include <torch/csrc/jit/backends/xnnpack/xnnpack_graph_builder.h>

namespace torch {
namespace jit {
namespace xnnpack {
namespace delegate {

// Expected method_compile_spec should look something like this:
// {
//     "forward" : {"inputs" : at::Tensor}
// }
// or
// {
//     "forward" : {"inputs" : c10::List<at::Tensor>}
// }
// in which the value for "inputs" is the input shape to the module.
// The module fed to the xnnpack backend must first be traced in order
// to propagate input shapes through the module. This is important
// for building the xnnpack_subgraph_t object.
c10::IValue preprocess(
    const Module& mod,
    const c10::Dict<c10::IValue, c10::IValue>& method_compile_spec,
    const BackendDebugHandleGenerator& generate_debug_handles) {
  auto output_min = -std::numeric_limits<float>::infinity();
  auto output_max = std::numeric_limits<float>::infinity();

  c10::Dict<IValue, IValue> compiled(StringType::get(), TensorType::get());

  c10::IValue inp;
  c10::IValue out;

  TORCH_CHECK(
      method_compile_spec.contains("forward"),
      "method_compile_spec does not contain the \"forward\" key.");
  auto innerDict = method_compile_spec.at("forward");

  TORCH_CHECK(
      innerDict.isGenericDict() &&
          innerDict.toGenericDict().contains("inputs") &&
          innerDict.toGenericDict().contains("outputs"),
      "method_compile_spec does not contain a dictionary with an \"inputs\" key, under \"forward\" key.");

  inp = innerDict.toGenericDict().at("inputs");
  out = innerDict.toGenericDict().at("outputs");

  TORCH_CHECK(
      inp.isTensor() || inp.isTensorList(),
      "method_compile_spec does not contain either a Tensor or TensorList, under it's \"inputs\" key.");
  TORCH_CHECK(
      out.isTensor() || out.isTensorList(),
      "method_compile_spec does not contain either a Tensor or TensorList, under it's \"outputs\" key.");

  // Graph preprocessing
  const auto& forward_method = mod.get_method("forward");

  auto graph = toGraphFunction(forward_method.function()).graph()->copy();
  graph = tensorexpr::removeUnusedSelfArgument(graph);
  std::vector<c10::IValue> example_inputs;
  if (inp.isTensorList()) {
    c10::List<at::Tensor> inp_list = inp.toTensorList();
    TORCH_CHECK(
        graph->inputs().size() == inp_list.size(),
        "method_compile_spec inputs do not match expected number of forward inputs");

    example_inputs.reserve(inp_list.size());
    for (const auto i : c10::irange(inp_list.size())) {
      graph->inputs()[i]->setType(TensorType::create(inp_list[i]));
      example_inputs.emplace_back(inp_list[i]);
    }
  } else {
    TORCH_CHECK(
        graph->inputs().size() == 1,
        "method_compile_spec inputs do not match expected number of forward inputs");

    graph->inputs()[0]->setType(TensorType::create(inp.toTensor()));
    example_inputs.emplace_back(inp.toTensor());
  }

  // inp above has been confirmed to be either Tensor or TensorList
  XNNGraph graph_builder;
  graph_builder.buildXNNGraph(graph, example_inputs);
  // at this point graph is complete, for the sake of testing preprocess at this
  // point we will do runtime setup and run with some default values

  // grabbing the inputs from compile spec for testing

  std::vector<at::Tensor> inputs;
  auto input_list = inp.toList();

  for (int i = 0; i < input_list.size(); i++) {
    inputs.push_back(input_list.get(i).toTensor());
  }
  std::vector<at::Tensor> outputs;
  outputs.push_back(out.toList().get(0).toTensor());

  graph_builder.runGraphOnInputs(inputs, outputs);

  c10::List<at::Tensor> output_list(outputs);

  compiled.insert("Answer", output_list);

  return compiled;
}
constexpr auto backend_name = "xnnpack";
static auto pre_reg = backend_preprocess_register(backend_name, preprocess);

} // namespace delegate
} // namespace xnnpack
} // namespace jit
} // namespace torch
