#include <ATen/Context.h>
#include <ATen/Dispatch.h>
#include <ATen/native/cuda/Loops.cuh>
#include <ATen/native/DispatchStub.h>
#include <ATen/native/TensorIterator.h>
#include <ATen/native/BinaryOps.h>
#include <limits>


// NOTE: CUDA on Windows requires that the enclosing function
// of a __device__ lambda not have internal linkage.

namespace at { namespace native {

void add_kernel_cuda(TensorIterator& iter, Scalar alpha_scalar) {
  AT_DISPATCH_ALL_TYPES_AND(kHalf, iter.dtype(), "add_cuda", [&]() {
    auto alpha = alpha_scalar.to<scalar_t>();
    gpu_kernel_with_scalars(iter, [alpha]GPU_LAMBDA(scalar_t a, scalar_t b) -> scalar_t {
      return a + alpha * b;
    });
  });
}

static void sub_kernel_cuda(TensorIterator& iter, Scalar alpha_scalar) {
  return add_kernel_cuda(iter, -alpha_scalar);
}

void div_kernel_cuda(TensorIterator& iter) {
  if (isIntegralType(iter.dtype())) {
    AT_DISPATCH_INTEGRAL_TYPES(iter.dtype(), "div_cuda", [&]() {
      gpu_kernel_with_scalars(iter, []GPU_LAMBDA(scalar_t a, scalar_t b) -> scalar_t {
        return a / b;
      });
    });
  } else if (iter.is_cpu_scalar(2)) {
    // optimization for floating-point types: if the second operand is a CPU
    // scalar, compute a * reciprocal(b). Note that this may lose one bit of
    // precision compared to computing the division.
    AT_DISPATCH_FLOATING_TYPES_AND_HALF(iter.dtype(), "div_cuda", [&]() {
      auto inv_b = scalar_t(1.0 / iter.scalar_value<scalar_t>(2));
      iter.remove_operand(2);
      gpu_kernel(iter, [inv_b]GPU_LAMBDA(scalar_t a) -> scalar_t {
        return a * inv_b;
      });
    });
  } else {
    AT_DISPATCH_FLOATING_TYPES_AND_HALF(iter.dtype(), "div_cuda", [&]() {
      gpu_kernel_with_scalars(iter, []GPU_LAMBDA(scalar_t a, scalar_t b) -> scalar_t {
        return a / b;
      });
    });
  }
}

void mul_kernel_cuda(TensorIterator& iter) {
  if (iter.dtype() == ScalarType::Bool) {
    // Workaround for the error: '*' in boolean context, suggest '&&' instead [-Werror=int-in-bool-context]
    gpu_kernel_with_scalars(iter, []GPU_LAMBDA(bool a, bool b) -> bool {
      return a && b;
    });
  } else {
    AT_DISPATCH_ALL_TYPES_AND(kHalf, iter.dtype(), "mul_cuda", [&]() {
      gpu_kernel_with_scalars(iter, []GPU_LAMBDA(scalar_t a, scalar_t b) -> scalar_t {
        return a * b;
      });
    });
  }
}

REGISTER_DISPATCH(add_stub, &add_kernel_cuda);
REGISTER_DISPATCH(sub_stub, &sub_kernel_cuda);
REGISTER_DISPATCH(div_stub, &div_kernel_cuda);
REGISTER_DISPATCH(mul_stub, &mul_kernel_cuda);

}} // namespace at::native
