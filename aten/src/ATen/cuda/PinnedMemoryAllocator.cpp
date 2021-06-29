#include <ATen/cuda/PinnedMemoryAllocator.h>
#include <ATen/Context.h>
#include <ATen/Config.h>
#include <ATen/TensorUtils.h>
#include <c10/core/Storage.h>
#include <ATen/ATen.h>
#include <ATen/NativeFunctions.h>

#include <THC/THC.h>
#include <THC/THCGeneral.hpp>

#include <stdexcept>

namespace at {

namespace cuda {

at::Allocator* getPinnedMemoryAllocator() {
  auto state = globalContext().lazyInitCUDA();
  return state->cudaHostAllocator;
}

} // namespace cuda

namespace native {

bool is_pinned_cuda(const Tensor& self, c10::optional<Device> device) {
  TORCH_INTERNAL_ASSERT_DEBUG_ONLY(!device.has_value() || device->is_cuda());
  // TODO: unhook this
  return detail::getCUDAHooks().isPinnedPtr(self.storage().data());
}

Tensor pin_memory_cuda(const Tensor& self, c10::optional<Device> device) {
  TORCH_INTERNAL_ASSERT_DEBUG_ONLY(!device.has_value() || device->is_cuda());
  if (is_pinned_cuda(self, device)) {
    // NB: just want to prevent resizing to catch bugs where people expected
    // to be able to resize after pin_memory
    return self.variable_data();
  }
  auto* allocator = at::cuda::getPinnedMemoryAllocator();
  auto storage = Storage(
      Storage::use_byte_size_t(),
      detail::computeStorageNbytes(
          self.sizes(), self.strides(), self.dtype().itemsize()),
      allocator,
      /*resizable=*/false);
  auto tensor = at::empty({0}, self.options()).set_(storage, 0, self.sizes(), self.strides());
  tensor.copy_(self);
  return tensor;
}


} // namespace native
} // namespace at
