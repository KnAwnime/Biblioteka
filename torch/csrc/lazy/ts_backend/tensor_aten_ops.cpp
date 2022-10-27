#include <torch/csrc/lazy/ts_backend/tensor_aten_ops.h>

#include <ATen/InferSize.h>
#include <c10/util/Optional.h>
#include <torch/csrc/autograd/variable.h>
#include <torch/csrc/lazy/core/helpers.h>
#include <torch/csrc/lazy/core/ir_builder.h>
#include <torch/csrc/lazy/core/ir_util.h>
#include <torch/csrc/lazy/core/lazy_graph_executor.h>
#include <torch/csrc/lazy/core/metrics.h>
#include <torch/csrc/lazy/core/ops/arithmetic_ir_ops.h>
#include <torch/csrc/lazy/core/ops/utils.h>
#include <torch/csrc/lazy/core/tensor.h>
#include <torch/csrc/lazy/core/util.h>
#include <torch/csrc/lazy/generated/LazyIr.h>
#include <torch/csrc/lazy/ts_backend/ops/random_ops.h>
#include <algorithm>
#include <functional>

namespace torch {
namespace lazy {
namespace {

// to enable operator+-*/ for Value
using namespace torch::lazy;

torch::lazy::Value MaybeExpand(
    const torch::lazy::Value& input,
    const torch::lazy::Shape& target_shape) {
  if (input.shape().sizes() == target_shape.sizes()) {
    return input;
  }
  return torch::lazy::MakeExpand(
      input,
      target_shape.sizes().vec(),
      /*is_scalar_expand=*/false);
}

std::vector<int64_t> GetExpandDimensions(
    const torch::lazy::Shape& shape,
    std::vector<int64_t> dimensions) {
  TORCH_CHECK_GE(dimensions.size(), shape.dim()) << shape;
  int64_t base = dimensions.size() - shape.dim();
  for (size_t i = 0; i < shape.dim(); ++i) {
    if (dimensions[base + i] == -1) {
      dimensions[base + i] = shape.size(i);
    }
  }
  return dimensions;
}

} // namespace

//////////////////////////////////////////////////////////////////////////////
// ATEN operators follows here, listed in alphabetical order.
//////////////////////////////////////////////////////////////////////////////
torch::lazy::LazyTensorPtr expand(
    const torch::lazy::LazyTensorPtr& input,
    std::vector<int64_t> size) {
  auto input_shape = input->shape();
  return torch::lazy::LazyTensor::Create(
      torch::lazy::MakeExpand(
          input->GetIrValue(),
          GetExpandDimensions(input_shape.Get(), std::move(size)),
          /*is_scalar_expand=*/false),
      input->GetDevice());
}

void fill_(torch::lazy::LazyTensorPtr& input, const at::Scalar& value) {
  torch::lazy::Value constant =
      torch::lazy::LazyGraphExecutor::Get()->GetIrValueForExpandedScalar(
          value, input->shape(), input->GetDevice());
  input->SetInPlaceIrValue(std::move(constant));
}

void copy_(torch::lazy::LazyTensorPtr& input, torch::lazy::LazyTensorPtr& src) {
  if (input->GetDevice() == src->GetDevice()) {
    torch::lazy::Value copy_value;
    if (input->dtype() == src->dtype()) {
      copy_value = src->GetIrValue();
    } else {
      copy_value = torch::lazy::MakeCast(
          src->GetIrValue(), input->dtype(), src->dtype());
    }
    input->SetIrValue(MaybeExpand(copy_value, input->shape()));
  } else {
    auto input_shape = input->shape();
    at::Tensor src_tensor = src->ToTensor(/*detached=*/true);
    if (src_tensor.sizes() != input_shape.Get().sizes()) {
      src_tensor = src_tensor.expand(input_shape.Get().sizes().vec());
    }
    input->UpdateFromTensor(std::move(src_tensor), /*sync=*/false);
  }
}

} // namespace lazy
} // namespace torch
