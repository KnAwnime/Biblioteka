#pragma once

#include <torch/csrc/jit/ir/ir.h>

namespace torch {
namespace jit {

// Inline function and method calls.
// use_graph argument is used to preclude inlining functions for ONNX conversion
TORCH_API void Inline(Graph& graph, bool use_graph=false);

} // namespace jit
} // namespace torch
