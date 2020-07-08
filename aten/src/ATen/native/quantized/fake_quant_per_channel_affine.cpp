#include <ATen/ATen.h>
#include <ATen/Dispatch.h>
#include <ATen/NativeFunctions.h>
#include <ATen/native/TensorIterator.h>
#include <ATen/native/cpu/Loops.h>
#include <ATen/native/quantized/fake_quant_affine.h>

// FakeQuantize Op for PerChannelAffine quantization scheme.
namespace at {
namespace native {

// Use REGISTER_DISPATCH to run CPU and CUDA backend.
DEFINE_DISPATCH(fake_quant_per_channel_stub);
DEFINE_DISPATCH(fake_quant_grad_per_channel_stub);
DEFINE_DISPATCH(fake_quant_grad_learnable_sc_channel_stub);
DEFINE_DISPATCH(fake_quant_grad_learnable_z_point_channel_stub);

/* Per channel fake-quantizes the 'inputs' tensor.
Args:
  X: Forward input tensor.
  dY: Backward input tensor (_backward op only).
  scale: scale of per channel affine quantization
  zero_point: zero_point of per channel affine quantization
  axis: int specifying the axis to be quantized
  quant_min: minimum quantized value
  quant_max: maximum quantized value
Returns:
  Fake quantized tensor (double dtype).

*/

Tensor fake_quantize_per_channel_affine(
    const Tensor& self,
    const Tensor& scale,
    const Tensor& zero_point,
    int64_t axis,
    int64_t quant_min,
    int64_t quant_max) {
  TORCH_CHECK(self.scalar_type() == ScalarType::Float);
  TORCH_CHECK(scale.dim() == 1, "scale should be a 1-D tensor");
  TORCH_CHECK(zero_point.dim() == 1, "zero point should be a 1-D tensor");
  TORCH_CHECK(
      scale.numel() == zero_point.numel(),
      "scale and zero-point need to have the same dimensions");
  TORCH_CHECK(
      scale.numel() == self.size(axis),
      "dimensions of scale and zero-point are not consistent with input tensor")

  TORCH_CHECK(
      quant_min <= quant_max,
      "`quant_min` should be less than or \
        equal to `quant_max`.");

  TORCH_CHECK(
      at::min(zero_point).item().toLong() >= quant_min &&
          at::max(zero_point).item().toLong() <= quant_max,
      "`zero_point` must be between `quant_min` and `quant_max`.");

  TORCH_CHECK(
      axis >= 0 && axis <= self.dim(),
      "`axis` must be between 0 and number of dimensions of input");

  auto Y = at::empty_like(self, self.options(), MemoryFormat::Preserve);

  std::vector<int64_t> expected_shape(self.dim(), 1);
  expected_shape[axis] = self.size(axis);

  TensorIterator iter = TensorIteratorConfig()
    .check_all_same_dtype(false)
    .add_output(Y)
    .add_input(self)
    .add_input(native::_unsafe_view(scale, expected_shape))
    .add_input(native::_unsafe_view(zero_point, expected_shape))
    .build();

  fake_quant_per_channel_stub(iter.device_type(), iter, quant_min, quant_max);

  return Y;
}

/* Backward path for per-channel fake-quantization of the 'inputs' tensor.

Args:
  X: Forward input tensor.
  dY: Backward input tensor.
  scale: scale of per tensor affine quantization
  zero_point: zero_point of per tensor affine quantization
  axis: int ,the axis over which quantization parameters vary
  quant_min: int, minimum quantized value
  quant_max: int, maximum quantized value

Returns:
  Gradient for per channel fake quant (double dtype).

*/
Tensor fake_quantize_per_channel_affine_backward(
    const Tensor& dY,
    const Tensor& X,
    const Tensor& scale,
    const Tensor& zero_point,
    int64_t axis,
    int64_t quant_min,
    int64_t quant_max) {
  TORCH_CHECK(dY.scalar_type() == ScalarType::Float);
  TORCH_CHECK(X.scalar_type() == ScalarType::Float);

  TORCH_CHECK(X.sizes() == dY.sizes(), "`X` and `dY` are not the same size");
  TORCH_CHECK(
      quant_min <= quant_max,
      "`quant_min` should be less than or \
        equal to `quant_max`.");
  TORCH_CHECK(scale.dim() == 1, "scale should be a 1-D tensor");
  TORCH_CHECK(zero_point.dim() == 1, "zero point should be a 1-D tensor");
  TORCH_CHECK(
      scale.numel() == zero_point.numel(),
      "scale and zero-point need to have the same dimensions");
  TORCH_CHECK(
      scale.numel() == X.size(axis),
      "dimensions of scale and zero-point are not consistent with input tensor")

  TORCH_CHECK(
      quant_min <= quant_max,
      "`quant_min` should be less than or \
        equal to `quant_max`.");

  TORCH_CHECK(
      at::min(zero_point).item().toLong() >= quant_min &&
          at::max(zero_point).item().toLong() <= quant_max,
      "`zero_point` must be between `quant_min` and `quant_max`.");

  TORCH_CHECK(
      axis >= 0 && axis <= X.dim(),
      "`axis` must be between 0 and number of dimensions of input");

  if (X.numel() <= 0) {
    return X;
  }

  auto dX = at::empty_like(X, X.options(), MemoryFormat::Preserve);

  std::vector<int64_t> expected_shape(X.dim(), 1);
  expected_shape[axis] = X.size(axis);

  TensorIterator iter = TensorIteratorConfig()
    .check_all_same_dtype(false)
    .add_output(dX)
    .add_input(X)
    .add_input(dY)
    .add_input(native::_unsafe_view(scale, expected_shape))
    .add_input(native::_unsafe_view(zero_point, expected_shape))
    .build();

  fake_quant_grad_per_channel_stub(iter.device_type(), iter, quant_min, quant_max);

  return dX;
}

TensorIterator build_iterator(
    const Tensor& dX,
    const Tensor& X,
    const Tensor& dY,
    const Tensor& scale,
    const Tensor& zero_point,
    std::vector<int64_t> expected_shape) {
  TensorIterator iter = TensorIteratorConfig()
    .check_all_same_dtype(false)
    .add_output(dX)
    .add_input(X)
    .add_input(dY)
    .add_input(native::_unsafe_view(scale, expected_shape))
    .add_input(native::_unsafe_view(zero_point, expected_shape))
    .build();
  return iter;
}

Tensor fake_quantize_learnable_per_channel_affine(
    const Tensor& self,
    const Tensor& scale,
    const Tensor& zero_point,
    int64_t axis,
    int64_t quant_min,
    int64_t quant_max) {
  return native::fake_quantize_per_channel_affine(
    self, scale, zero_point, axis, quant_min, quant_max);
}

std::tuple<Tensor, Tensor, Tensor> fake_quantize_learnable_per_channel_affine_backward(
    const Tensor& dY,
    const Tensor& X,
    const Tensor& scale,
    const Tensor& zero_point,
    int64_t axis,
    int64_t quant_min,
    int64_t quant_max) {
  TORCH_CHECK(dY.scalar_type() == ScalarType::Float);
  TORCH_CHECK(X.scalar_type() == ScalarType::Float);
  TORCH_CHECK(scale.scalar_type() == ScalarType::Float);
  TORCH_CHECK(zero_point.scalar_type() == ScalarType::Float);

  TORCH_CHECK(X.sizes() == dY.sizes(), "`X` and `dY` are not the same size");
  TORCH_CHECK(
      quant_min <= 0 && quant_max >= 0,
      "`quant_min` should be less than or \
        equal to `quant_max`, and the quantization range should include 0.");
  TORCH_CHECK(scale.dim() == 1, "scale should be a 1-D tensor");
  TORCH_CHECK(zero_point.dim() == 1, "zero point should be a 1-D tensor");
  TORCH_CHECK(
      scale.numel() == zero_point.numel(),
      "scale and zero-point need to have the same dimensions");
  TORCH_CHECK(
    at::min(zero_point).item().toLong() >= 0,
    "`zero_point` must be at least 0 or greater.");
  TORCH_CHECK(
      scale.numel() == X.size(axis),
      "dimensions of scale and zero-point are not consistent with input tensor")

  TORCH_CHECK(
      at::min(zero_point).item().toLong() >= quant_min &&
          at::max(zero_point).item().toLong() <= quant_max,
      "`zero_point` must be between `quant_min` and `quant_max`.");

  TORCH_CHECK(
      axis >= 0 && axis <= X.dim(),
      "`axis` must be between 0 and number of dimensions of input");

  if (X.numel() <= 0) {
    return std::make_tuple(X, scale, zero_point);
  }

  auto dX = at::empty_like(X, X.options(), MemoryFormat::Preserve);

  std::vector<int64_t> expected_shape_X(X.dim(), 1);
  expected_shape_X[axis] = X.size(axis);

  TensorIterator iter_X = native::build_iterator(
    dX, X, dY, scale, zero_point, expected_shape_X);

  fake_quant_grad_per_channel_stub(iter_X.device_type(), iter_X, quant_min, quant_max);

  auto dScale = at::empty_like(X, X.options(), MemoryFormat::Preserve);

  TensorIterator iter_Scale = native::build_iterator(
    dScale, X, dY, scale, zero_point, expected_shape_X);

  fake_quant_grad_learnable_sc_channel_stub(iter_Scale.device_type(), iter_Scale, quant_min, quant_max);

  auto dZeroPoint = at::empty_like(X, X.options(), MemoryFormat::Preserve);

  TensorIterator iter_ZeroPoint = native::build_iterator(
    dZeroPoint, X, dY, scale, zero_point, expected_shape_X);

  fake_quant_grad_learnable_z_point_channel_stub(iter_ZeroPoint.device_type(), iter_ZeroPoint, quant_min, quant_max);

  return std::make_tuple(dX, dScale, dZeroPoint);
}
} // namespace native
} // namespace at
