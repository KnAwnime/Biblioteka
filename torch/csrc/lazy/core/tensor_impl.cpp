#include <torch/csrc/lazy/core/tensor_impl.h>

#include <c10/core/Allocator.h>
#include <c10/core/ScalarType.h>
#include <c10/core/impl/DeviceGuardImplInterface.h>
#include <c10/macros/Macros.h>
#include <c10/util/irange.h>
#include <torch/csrc/lazy/core/ir_builder.h>
#include <torch/csrc/lazy/core/tensor_util.h>

namespace torch {
namespace lazy {
namespace {

// LTCGuardImpl is used by CompositeExplicitAutograd ops or eager fallbacks to
// make sure that some particular tensors within the life scope of the guard are
// on the same device. For example, in RegisterCompositeExplicitAutograd.cpp,
// outputs of each op are examined if they are on same device as the supplied
// TensorOptions. For more information, see DeviceGuard.h. For ops that have LTC
// native function implementations, this guard is omitted.
thread_local c10::Device g_device(c10::DeviceType::Lazy);

struct LTCGuardImpl : public c10::impl::DeviceGuardImplInterface {
  at::DeviceType type() const override {
    return at::DeviceType::Lazy;
  }

  c10::Device exchangeDevice(c10::Device device) const override {
    TORCH_INTERNAL_ASSERT(device.type() == c10::DeviceType::Lazy);
    auto old_device = g_device;
    g_device = device;
    return old_device;
  }

  c10::Device getDevice() const override {
    return g_device;
  }

  void setDevice(c10::Device device) const override {
    TORCH_INTERNAL_ASSERT(device.type() == c10::DeviceType::Lazy);
    g_device = device;
  }

  void uncheckedSetDevice(c10::Device device) const noexcept override {
    TORCH_INTERNAL_ASSERT(device.type() == c10::DeviceType::Lazy);
    g_device = device;
  }

  c10::Stream getStream(c10::Device device) const noexcept override {
    TORCH_INTERNAL_ASSERT(device.type() == c10::DeviceType::Lazy);
    return c10::Stream(c10::Stream::DEFAULT, device);
  }

  c10::Stream exchangeStream(c10::Stream _unused) const noexcept override {
    return c10::Stream(c10::Stream::DEFAULT, g_device);
  }

  c10::DeviceIndex deviceCount() const noexcept override {
    // This will get called when autograd initializes its device pool
    // regardless whether we have a backend registered aforehand.
    if (!hasBackend()) {
      return 0;
    }

    return getBackend()->GetBackendDevices().size();
  }
};

C10_REGISTER_GUARD_IMPL(Lazy, LTCGuardImpl);

} // namespace

// TODO(whc) when do we want to clone vs share?
LTCTensorImpl::LTCTensorImpl(const LazyTensorPtr& tensor)
    : LTCTensorImpl(LazyTensor(*tensor)) {}

LTCTensorImpl::LTCTensorImpl(const LazyTensor& tensor)
    : LTCTensorImpl(LazyTensor(tensor)) {}

LTCTensorImpl::LTCTensorImpl(LazyTensor&& tensor)
    : c10::TensorImpl(
          c10::DispatchKeySet{
              c10::DispatchKey::Lazy,
              c10::DispatchKey::AutogradLazy},
          c10::scalarTypeToTypeMeta(tensor.dtype()),
          backendDeviceToAtenDevice(tensor.GetDevice())),
      tensor_(c10::make_intrusive<LazyTensor>(std::move(tensor))) {
  // This is a temporary fix for a PyTorch core issue,
  // according to https://github.com/pytorch/xla/pull/2682.
  is_non_overlapping_and_dense_ = false;
  set_sizes_strides_policy(SizesStridesPolicy::CustomSizes);

  auto rank = tensor_->shape().Get().sizes().size();
  sym_sizes_.reserve(rank);
  for (auto i : c10::irange(rank)) {
    auto dim_node = getBackend()->GetIrBuilder()->MakeSizeNode(
        this->tensor_->GetIrValue(), i);
    auto sn = std::make_shared<torch::lazy::SymIntNodeImpl>(dim_node);
    sym_sizes_.push_back(sn->toSymInt());
  }
}

void LTCTensorImpl::set_tensor(const LazyTensorPtr& lazy_tensor) {
  tensor_ = c10::make_intrusive<LazyTensor>(*lazy_tensor);
  generation_ = 0;
}

c10::intrusive_ptr<c10::TensorImpl> LTCTensorImpl::shallow_copy_and_detach(
    const c10::VariableVersion& version_counter,
    bool allow_tensor_metadata_change) const {
  auto impl = c10::make_intrusive<LTCTensorImpl>(tensor_);
  copy_tensor_metadata(
      /*src_impl=*/this,
      /*dest_impl=*/impl.get(),
      /*version_counter=*/version_counter,
      /*allow_tensor_metadata_change=*/allow_tensor_metadata_change);
  return impl;
}

c10::intrusive_ptr<c10::TensorImpl> LTCTensorImpl::shallow_copy_and_detach(
    c10::VariableVersion&& version_counter,
    bool allow_tensor_metadata_change) const {
  auto impl = c10::make_intrusive<LTCTensorImpl>(tensor_);
  copy_tensor_metadata(
      /*src_impl=*/this,
      /*dest_impl=*/impl.get(),
      /*version_counter=*/std::move(version_counter),
      /*allow_tensor_metadata_change=*/allow_tensor_metadata_change);
  return impl;
}

void LTCTensorImpl::shallow_copy_from(
    const c10::intrusive_ptr<TensorImpl>& impl) {
  LTCTensorImpl* ltc_impl = dynamic_cast<LTCTensorImpl*>(impl.get());
  TORCH_INTERNAL_ASSERT(ltc_impl);
  copy_tensor_metadata(
      /*src_impl=*/ltc_impl,
      /*dest_impl=*/this,
      /*version_counter=*/version_counter(),
      /*allow_tensor_metadata_change=*/allow_tensor_metadata_change());
  ltc_impl->tensor_->ShallowCopyTo(tensor_);
  generation_ = 0;
}

c10::SymIntArrayRef LTCTensorImpl::sym_sizes_custom() const {
  if (FLAGS_ltc_enable_symbolic_shapes) {
    return c10::SymIntArrayRef(sym_sizes_.data(), sym_sizes_.size());
  }

  // return upper bound
  const_cast<LTCTensorImpl*>(this)->setup_size_properties();
  return TensorImpl::sym_sizes_default();
}

c10::SymIntArrayRef LTCTensorImpl::sym_sizes() const {
  return sym_sizes_custom();
}

void LTCTensorImpl::setup_size_properties() {
  size_t generation = tensor_->generation();
  if (generation != generation_) {
    // Fill up the basic dimension data members which the base class
    // implementation uses in its APIs.
    auto shape = tensor_->shape();
    // We can't call refresh_numel() given we override sizes() too.
    numel_ = shape.Get().numel();
    sizes_and_strides_.set_sizes(shape.Get().sizes());
    // We can't call empty_tensor_restride(c10::MemoryFormat::Contiguous) given
    // we override sizes() too.
    std::vector<int64_t> updated_strides;
    updated_strides = ComputeArrayStrides(shape.Get().sizes());
    for (const auto i : c10::irange(updated_strides.size())) {
      sizes_and_strides_.stride_at_unchecked(i) = updated_strides[i];
    }
    generation_ = generation;
  }
}

at::IntArrayRef LTCTensorImpl::sizes_custom() const {
  // NOLINTNEXTLINE(cppcoreguidelines-pro-type-const-cast)
  const_cast<LTCTensorImpl*>(this)->setup_size_properties();
  return sizes_default();
}

at::IntArrayRef LTCTensorImpl::strides_custom() const {
  // NOLINTNEXTLINE(cppcoreguidelines-pro-type-const-cast)
  const_cast<LTCTensorImpl*>(this)->setup_size_properties();
  return strides_default();
}

int64_t LTCTensorImpl::dim_custom() const {
  // NOLINTNEXTLINE(cppcoreguidelines-pro-type-const-cast)
  const_cast<LTCTensorImpl*>(this)->setup_size_properties();
  return dim_default();
}

int64_t LTCTensorImpl::numel_custom() const {
  // NOLINTNEXTLINE(cppcoreguidelines-pro-type-const-cast)
  const_cast<LTCTensorImpl*>(this)->setup_size_properties();
  return numel_default();
}

bool LTCTensorImpl::is_contiguous_custom(c10::MemoryFormat _unused) const {
  if (tensor_->CurrentTensorData()) {
    return tensor_->CurrentTensorData()->is_contiguous();
  }
  // Only check that the storage is already contiguous.
  CHECK(is_contiguous_) << "Non-contiguous storage for lazy tensor";
  return true;
}

} // namespace lazy
} // namespace torch
