#pragma once

#ifdef USE_VULKAN_API

#include <ATen/ATen.h>
#include <ATen/native/vulkan/api/api.h>
#include <ATen/native/vulkan/VulkanOpaqueTensorImpl.h>
#include <c10/util/accumulate.h>

namespace at {
namespace native {
namespace vulkan {
namespace ops {

//
// This class represents a Vulkan tensor and provides an abstraction layer
// that allows both the CPU, and the GPU, to view a Vulkan (buffer, image)
// pair as one coherent, synchronized unit of storage on both UMA and discrete
// systems.  Expanding on the previous sentence, this class tries to address
// two orthogonal implementation complexities that arise as a result of the
// aforementioned goal of memory coherence:
//
// 1) First, synchronization across processors; CPUs and GPUs are separate
//    processors, and even though they share the same address space in a system
//    with a unified memory architecture, their address spaces only partially
//    overlap on systems with a discrete GPU.  Consequently on discrete systems,
//    while it is still technically possible to take advantage of this shared
//    address space to maintain one single copy of the data, different access
//    latencies from CPU and GPU to this shared location usually necessitates
//    maintaining two copies each in processor-local memory, otherwise memory
//    access latency will hurt from the processor to which this data is not
//    close.  This shared memory is more often than not located in system memory,
//    making for slow GPU read and write access over the PCI-e bus on discrete.
//    Maintaining two separate copies on the other hand, requires synchronization
//    to guarantee coherence.  This is not an issue on UMA and this implementation
//    accounts for that optimization.
//
// 2) Second, synchronization across resources (i.e. buffers and images); GPU
//    drivers pack images in proprietory formats for better locality of access
//    and to enable lossless compression.  These conversions are both expensive
//    (in general) and manual (in Vulkan.)  This requires a second order of
//    synchronization to guarantee coherence between the contents of the buffer
//    and image otherwise they will go out of sync.
//
// It is extremely important to keep in mind that the functionality this class
// provides is generally expensive.  For optimal performance, the user of this
// class should:
//
// 1) Avoid frequent CPU <=> GPU transfers which will be triggered if data is
//    write accessed on one processor and read / write accessed on the other.
//
// 2) Avoid frequent buffer <=> image conversions which will be trigerred if
//    data is write accessed as a buffer (image) and read accessed as an
//    image (buffer).
//
// 3) When and if a synchronization is unavoidable, place as much distance
//    between the synchronization is triggered and the data is accessed since
//    all synchronizations this class provides are async.
//
// For optimal performance, access the data as images, and keep the data on GPU,
// and above all understand the expensive data flow that this class abstracts
// away.
//
// vTensor tries to address a specific concern and intentionally does not expose
// GPU tensor memory directly.  Please keep that behavior intact as the whole
// data model fundamentally depends on limiting what the user can achieve through
// the interface to guarantee performance and coherence.
//
// A vTensor is associated with an api::Context as preparation for multi-GPU
// support.
//

class vTensor final {
 public:
  vTensor() = default;
  vTensor(
      api::Context* context,
      IntArrayRef sizes,
      const TensorOptions& options);

  /*
    Types
  */

  typedef api::PipelineStage Stage;

  /*
    Host access
  */


  inline void wait_for_fence() const {
    view_->wait_for_fence();
  }

  api::VulkanBuffer& host_buffer(
      api::Command::Buffer&, const api::MemoryAccessFlags) &;

  /*
    Device access - these functions will be expensive if they trigger a buffer
    <-> image or CPU -> GPU sync due to pending writes.  These functions are
    non-blocking on the host as the copy operation is carried out by the GPU
    asynchronously.  Regardless, they result in extra work that could have been
    avoided or at least minimized if all data access had occured through one
    single processor (GPU in this case) and on one type of resource (image for
    best performance.)  Consequently, for optimal performance, avoid mixed reads
    and writes across processor boundaries, and do your best to minimize layout
    transitions as a result of working with images only (as opposed to mixed
    buffer - image usage.)
    This implementation intentionally restricts user access to the buffer and
    image objects only, as opposed to their underlying memory, for the sake of
    predictability of usage and efficiency.
  */

  api::VulkanBuffer::Package buffer(api::Command::Buffer&, Stage::Flags) const &;
  api::VulkanBuffer::Package buffer(
      api::Command::Buffer&, Stage::Flags, api::MemoryAccessFlags) &;

  bool has_image() const;

  api::VulkanImage::Package image(api::Command::Buffer&, Stage::Flags) const &;
  api::VulkanImage::Package image(
      api::Command::Buffer&, Stage::Flags, api::MemoryAccessFlags) &;

  /*
    Metadata
  */

  const api::utils::uvec3& extents() const;
  const TensorOptions& options() const;
  IntArrayRef sizes() const;
  IntArrayRef strides() const;
  size_t nbytes() const;

 private:
  /*
    Device
  */

  api::VulkanBuffer::Package buffer(
      api::Command::Buffer&, Stage::Flags) const && = delete;
  api::VulkanBuffer::Package buffer(
      api::Command::Buffer&, Stage::Flags, api::MemoryAccessFlags) && = delete;

  api::VulkanImage::Package image(
      api::Command::Buffer&, Stage::Flags) const && = delete;
  api::VulkanImage::Package image(
      api::Command::Buffer&, Stage::Flags, api::MemoryAccessFlags) && = delete;

 private:
  class View final {
   public:
    View();
    View(
        api::Context* context,
        IntArrayRef sizes,
        const TensorOptions& options);
    View(const View&) = delete;
    View& operator=(const View&) = delete;
    View(View&&) = default;
    View operator=(View&&) = delete;
    ~View();

    void release();

    /*
      Buffer
    */

    api::VulkanBuffer& buffer(
        api::Command::Buffer&, Stage::Flags, api::MemoryAccessFlags) const;

    /*
      Image
    */

    bool has_image() const;
    api::VulkanImage& image(
        api::Command::Buffer&, Stage::Flags, api::MemoryAccessFlags) const;

    /*
      Host
    */

    api::VulkanBuffer& staging(
        api::Command::Buffer&, Stage::Flags, api::MemoryAccessFlags) const;

    void wait_for_fence() const;

    /*
      Metadata
    */

    const api::utils::uvec3& extents() const;
    const TensorOptions& options() const;
    IntArrayRef sizes() const;
    IntArrayRef strides() const;

   private:
    class CMD;

    class State final {
     public:
      State();
      State(const api::Adapter*, IntArrayRef);

      struct Bundle final {
        struct Buffer final {
          VkPipelineStageFlags stage;
          VkAccessFlags access;

          operator bool() const;
        } staging, buffer;

        struct Image final {
          VkPipelineStageFlags stage;
          VkAccessFlags access;
          VkImageLayout layout;

          operator bool() const;
        } image;
      };

      struct Component final {
        typedef uint8_t Flags;

        enum Type : Flags {
          Buffer = 1u << 0u,
          Image = 1u << 1u,
          Staging = 1u << 2u,
          All = Buffer | Image | Staging,
        };
      };

      // Availability
      bool is_available(Component::Flags) const;
      bool is_discrete() const;
      bool is_uma() const;

      // Clean / Dirty
      bool is_clean(Component::Flags) const;
      bool is_dirty(Component::Flags) const;
      void set_clean(Component::Flags);
      void set_dirty(Component::Flags);

      // Transition
      typedef std::pair<Bundle, Bundle> Transition;
      Transition transition(Bundle to);

     private:
      Component::Flags available_;
      Component::Flags dirty_;
      Bundle bundle_;

     private:
     #ifdef VULKAN_TENSOR_DEBUG
      friend class View;
     #endif /* VULKAN_TENSOR_DEBUG */
    };

    typedef State::Component Component;

   private:
    // Accessors / Lazy Allocation
    api::VulkanBuffer& buffer() const;
    api::VulkanBuffer& buffer(CMD&, Stage::Flags, api::MemoryAccessFlags) const;
    api::VulkanImage& image() const;
    api::VulkanImage& image(CMD&, Stage::Flags, api::MemoryAccessFlags) const;
    api::VulkanBuffer& staging() const;
    api::VulkanBuffer& staging(CMD&, Stage::Flags, api::MemoryAccessFlags) const;
    api::VulkanFence& fence(api::MemoryAccessFlags) const;

    // Validation
    void verify() const;

   private:
    // Resources
    mutable api::VulkanBuffer buffer_;
    mutable api::VulkanImage image_;
    mutable api::VulkanBuffer staging_;
    mutable api::VulkanFence fence_;

    // Context
    api::Context* context_;

    // State
    mutable State state_;

    // Metadata
    api::utils::uvec3 extents_;
    TensorOptions options_;
    c10::SmallVector<int64_t, 6u> sizes_;
    c10::SmallVector<int64_t, 6u> strides_;

   private:
   #ifdef VULKAN_TENSOR_DEBUG
    friend class vTensor;
    friend std::ostream& operator<<(
      std::ostream&,
      const View::State::Bundle&);
   #endif /* VULKAN_TENSOR_DEBUG */
  };

  // Even at the cost of a heap allocation plus the resulting negative impact
  // on cache locality due to the subsequent pointer chasing, it is still
  // critcal to share the view across vTensor implementations to minimize
  // programmer errors.  Ideally this class should have been only made movable,
  // and non-copyable - something we cannot do unfortunately due to the inner
  // workings of at::TensorImpl requiring copy semantics in
  // at::TensorImpl::release_resources() to function as expected.  Now that this
  // class is made copyable though, a new door to a whole new class of bugs is
  // opened, in that there now is a chance of two [shallow] copies, have their
  // State objects go out of sync as a result of an operation being performed on
  // one shallow copy that is not reflected in the other.  Technically, if the
  // programmer is very careful, it is possible to avoid this trap and not pay
  // the cost of indirection, but the resulting bugs of missing memory barriers
  // will be so frustrating to hunt down for those unfamiliar with the internal
  // mechanics of this class, that I decided to take the performance pentalty
  // of this extra layer of indirection in favor of making this class easier
  // to use.

  std::shared_ptr<View> view_;

 private:
 #ifdef VULKAN_TENSOR_DEBUG
  friend std::ostream& operator<<(
      std::ostream&,
      const View::State::Bundle&);
 #endif /* VULKAN_TENSOR_DEBUG */
};

vTensor& convert(const Tensor& tensor);
Tensor convert(const vTensor& tensor);

using vTensorImpl = VulkanOpaqueTensorImpl<vTensor>;
void verify(const TensorOptions& options);

//
// Impl
//

inline bool vTensor::has_image() const {
  return view_->has_image();
}

inline const api::utils::uvec3& vTensor::extents() const {
  return view_->extents();
}

inline const TensorOptions& vTensor::options() const {
  return view_->options();
}

inline IntArrayRef vTensor::sizes() const {
  return view_->sizes();
}

inline size_t vTensor::nbytes() const {
  return c10::elementSize(c10::typeMetaToScalarType(options().dtype())) *
         c10::multiply_integers(sizes());
}

inline IntArrayRef vTensor::strides() const {
  return view_->strides();
}

inline bool vTensor::View::has_image() const {
  return state_.is_available(View::Component::Image);
}

inline const api::utils::uvec3& vTensor::View::extents() const {
  return extents_;
}

inline const TensorOptions& vTensor::View::options() const {
  return options_;
}

inline IntArrayRef vTensor::View::sizes() const {
  return sizes_;
}

inline IntArrayRef vTensor::View::strides() const {
  return strides_;
}

inline vTensor::View::State::Bundle::Buffer::operator bool() const {
  return (0u != stage) &&
         (0u != access);
}

inline vTensor::View::State::Bundle::Image::operator bool() const {
  return (0u != stage) &&
         (0u != access) &&
         (VK_IMAGE_LAYOUT_UNDEFINED != layout);
}

inline bool vTensor::View::State::is_available(
    const Component::Flags components) const {
  return available_ & components;
}

inline bool vTensor::View::State::is_discrete() const {
  return is_available(Component::Staging);
}

inline bool vTensor::View::State::is_uma() const {
  return !is_discrete();
}

inline bool vTensor::View::State::is_clean(
    const Component::Flags components) const {
  return !is_dirty(components);
}

inline bool vTensor::View::State::is_dirty(
    const Component::Flags components) const {
  return dirty_ & components;
}

inline void vTensor::View::State::set_clean(
    const Component::Flags components) {
  dirty_ &= ~components;
}

inline void vTensor::View::State::set_dirty(
    const Component::Flags components) {
  dirty_ |= components;
}

inline vTensor& convert(const Tensor& tensor) {
  TORCH_INTERNAL_ASSERT(
      tensor.is_vulkan(),
      "Vulkan tensor expected!");

  vTensorImpl* const impl =
      static_cast<vTensorImpl*>(tensor.unsafeGetTensorImpl());

  return impl->unsafe_opaque_handle();
}

inline Tensor convert(const vTensor& tensor) {
  return at::detail::make_tensor<vTensorImpl>(
      DispatchKeySet(DispatchKey::Vulkan),
      tensor.options().dtype(),
      at::Device(at::kVulkan),
      tensor,
      tensor.sizes(),
      tensor.strides());
}

} // namespace ops
} // namespace vulkan
} // namespace native
} // namespace at

#endif /* USE_VULKAN_API */
