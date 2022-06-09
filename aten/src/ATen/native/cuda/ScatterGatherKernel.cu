#define TORCH_ASSERT_ONLY_METHOD_OPERATORS
#include <ATen/native/TensorAdvancedIndexing.h>

#include <ATen/core/Tensor.h>
#include <ATen/Dispatch.h>
#include <ATen/MemoryOverlap.h>

#include <ATen/native/ScatterGatherChecks.h>
#include <ATen/native/ReduceOpsUtils.h>
#include <ATen/native/TensorIterator.h>

#include <ATen/native/cuda/Loops.cuh>
#include <ATen/native/cuda/KernelUtils.cuh>
#include <ATen/cuda/detail/OffsetCalculator.cuh>
#include <ATen/cuda/Atomic.cuh>
#include <ATen/cuda/CUDAContext.h>

namespace at { namespace native {

// Implement as functors since lambdas don't get optimized.
class ReduceMultiply {
public:
  template <typename scalar_t>
  constexpr C10_DEVICE void operator() (scalar_t* self_data_start, int64_t index, int64_t numel, const scalar_t * src_data) const {
    (void)numel; // suppress unused warning
    gpuAtomicMul(self_data_start + index, *src_data);
  }
};
static ReduceMultiply reduce_multiply;

class ReduceAdd {
public:
  template <typename scalar_t>
  constexpr C10_DEVICE void operator() (scalar_t* self_data_start, int64_t index, int64_t numel, const scalar_t * src_data) const {
    fastAtomicAdd(self_data_start, index, numel, *src_data, true);
  }
};
static ReduceAdd reduce_add;

class ReduceMean {
public:
  template <typename scalar_t>
  constexpr C10_DEVICE void operator() (scalar_t* self_data_start, int64_t index, int64_t numel, const scalar_t * src_data) const {
    fastAtomicAdd(self_data_start, index, numel, *src_data, true);
  }
};
static ReduceMean reduce_mean;

class ReduceMinimum {
public:
  template <typename scalar_t>
  constexpr C10_DEVICE void operator() (scalar_t* self_data_start, int64_t index, int64_t numel, const scalar_t * src_data) const {
    (void)numel; // suppress unused warning
    gpuAtomicMin(self_data_start + index, *src_data);
  }
};
static ReduceMinimum reduce_minimum;

class ReduceMaximum {
public:
  template <typename scalar_t>
  constexpr C10_DEVICE void operator() (scalar_t* self_data_start, int64_t index, int64_t numel, const scalar_t * src_data) const {
    (void)numel; // suppress unused warning
    gpuAtomicMax(self_data_start + index, *src_data);
  }
};
static ReduceMaximum reduce_maximum;

class TensorAssign {
public:
  template <typename scalar_t>
  constexpr C10_DEVICE void operator() (scalar_t* self_data_start, int64_t index, int64_t numel, const scalar_t * src_data) const {
    (void)numel; // suppress unused warning
    *(self_data_start + index) = *src_data;
  }
};
static TensorAssign tensor_assign;

// The kernels are implemented on an opaque,
// self-aligned type of the correct size,
// to avoid redundant kernels for different types
// of the same size.
template <int N> struct alignas(N) OpaqueType { char data[N]; };

// essentialy rewritten related to legacy::launch_kernel parts
template <int nt, int vt, typename func_t>
C10_LAUNCH_BOUNDS_2(nt, vt)
__global__ void _scatter_gather_elementwise_kernel(int N, func_t f) {
  constexpr int nv = nt * vt;
  int idx = nv * blockIdx.x + threadIdx.x;

  #pragma unroll
  for (int i = 0; i < vt; ++i) {
    if (idx < N) {
      f(idx);
      idx += nt;
    }
  }
}

template <int nt, int vt, typename func_t>
static void _launch_scatter_gather_kernel(int64_t N, const func_t& f) {
  TORCH_INTERNAL_ASSERT(N >= 0 && N <= std::numeric_limits<int32_t>::max());
  if (N == 0) {
    return;
  }

  const dim3 block(nt);
  const dim3 grid((N + block.x * vt - 1) / (block.x * vt));
  const auto stream = at::cuda::getCurrentCUDAStream();
  _scatter_gather_elementwise_kernel<nt, vt, func_t><<<grid, block, 0, stream>>>(N, f);
  C10_CUDA_KERNEL_LAUNCH_CHECK();
}


template <bool is_scatter_like, typename scalar_t>
struct _cuda_scatter_gather_internal_kernel {
  template <typename func_t>
  void operator() (
    TensorIterator& iter,
    int64_t index_size,
    int64_t index_stride,
    int64_t numel,  // Do not use `const` qualifier here as it may cause issue in cuda 11.6.x. See #75434, #75545
    const func_t& f
  ) {
    if (!iter.can_use_32bit_indexing()) {
      for (auto& sub_iter : iter.with_32bit_indexing()) {
        _cuda_scatter_gather_internal_kernel<is_scatter_like, scalar_t>()(
          sub_iter, index_size, index_stride, numel, f
        );
      }
      return;
    }

    char* self_ptr = (char*)iter.data_ptr(0);
    char* src_ptr = (char*)iter.data_ptr(1);
    char* index_ptr = (char*)iter.data_ptr(2);

    auto offset_calc = make_offset_calculator<3>(iter);
    auto loop = [=]C10_DEVICE(int i) {
      auto offsets = offset_calc.get(i);

      int64_t idx_dim = *(int64_t*)(index_ptr + offsets[2]);
      CUDA_KERNEL_ASSERT(idx_dim >= 0 && idx_dim < index_size
        && "index out of bounds");

      f(
        (scalar_t*)(self_ptr + offsets[0]),
        is_scatter_like ? idx_dim * index_stride : 0,
        numel,
        (scalar_t*)(src_ptr + offsets[1]) + (is_scatter_like ? 0 : idx_dim * index_stride)
      );
    };

    _launch_scatter_gather_kernel<num_threads(), thread_work_size()>(iter.numel(), loop);
  }
}; // struct _cuda_scatter_gather_internal_kernel

template <typename scalar_t>
struct _cuda_scatter_large_index_internal_kernel {
  template <typename func_t>
  void operator() (
    TensorIterator& iter,
    int64_t index_size,
    int64_t index_stride,
    const IntArrayRef index_shape,
    const IntArrayRef index_strides,
    const IntArrayRef src_shape,
    const IntArrayRef src_strides,
    int64_t numel,  // Do not use `const` qualifier here as it may cause issue in cuda 11.6.x. See #75434, #75545
    const func_t& f
  ) {
    if (!iter.can_use_32bit_indexing()) {
      for (auto& sub_iter : iter.with_32bit_indexing()) {
        _cuda_scatter_large_index_internal_kernel<scalar_t>()(
          sub_iter, index_size, index_stride, index_shape, index_strides, src_shape, src_strides, numel, f
        );
      }
      return;
    }

    char* self_ptr = (char*)iter.data_ptr(0);
    char* src_ptr = (char*)iter.data_ptr(1);
    char* index_ptr = (char*)iter.data_ptr(2);

    int ndim = index_shape.size();
    int64_t *index_shape_device, *index_strides_device, *src_shape_device, *src_strides_device;
    cudaMalloc((void**)&index_shape_device, ndim * sizeof(int64_t));
    cudaMalloc((void**)&index_strides_device, ndim * sizeof(int64_t));
    cudaMalloc((void**)&src_shape_device, ndim * sizeof(int64_t));
    cudaMalloc((void**)&src_strides_device, ndim * sizeof(int64_t));
    cudaMemcpy(index_shape_device, index_shape.data(), ndim * sizeof(int64_t), cudaMemcpyHostToDevice);
    cudaMemcpy(index_strides_device, index_strides.data(), ndim * sizeof(int64_t), cudaMemcpyHostToDevice);
    cudaMemcpy(src_shape_device, src_shape.data(), ndim * sizeof(int64_t), cudaMemcpyHostToDevice);
    cudaMemcpy(src_strides_device, src_strides.data(), ndim * sizeof(int64_t), cudaMemcpyHostToDevice);

    auto offset_calc = make_offset_calculator<3>(iter);
    auto loop = [=]C10_DEVICE(int i) {
      auto offsets = offset_calc.get(i);

      int64_t idx_dim = *(int64_t*)(index_ptr + offsets[2]);
      CUDA_KERNEL_ASSERT(idx_dim >= 0 && idx_dim < index_size
        && "index out of bounds");

      auto original_index_offset = offsets[2] / 8;
      decltype(original_index_offset) src_offset = 0;
      int64_t index_idx;
      for (int d = ndim - 1; d >= 0; d--) {
        index_idx = (original_index_offset / index_strides_device[d]) % index_shape_device[d];
        original_index_offset -= index_idx * index_strides_device[d];

        index_idx %= src_shape_device[d];
        src_offset += src_strides_device[d] * index_idx;
      }
      src_offset *= 4;

      f(
        (scalar_t*)(self_ptr + offsets[0]),
        idx_dim * index_stride,
        numel,
        (scalar_t*)(src_ptr + src_offset)
      );
    };

    _launch_scatter_gather_kernel<num_threads(), thread_work_size()>(iter.numel(), loop);
  }
}; // struct _cuda_scatter_large_index_internal_kernel

template <bool is_scatter_like = true, bool cast_to_opaque = true>
struct cuda_scatter_gather_base_kernel {
  void operator()(
    const Tensor& self, int64_t dim,
    const Tensor& index, const Tensor& src,
    const std::string& method_name,
    const ReduceAdd& f
  ) {
    at::assert_no_internal_overlap(self);

    auto index_sizes = index.sizes();
    auto index_sizes_vec = ensure_nonempty_vec(index_sizes.vec());
    auto index_strides = index.strides();
    auto ndim = index_sizes.size();
    auto self_strides_vec = ensure_nonempty_vec(self.strides().vec());
    auto src_sizes = src.sizes();
    auto src_strides = src.strides();
    auto src_strides_vec = ensure_nonempty_vec(src_strides.vec());

    bool index_larger_than_src_in_scatter = false;
    if (is_scatter_like) {
      for (int i = 0; i < ndim; i++) {
        if (index_sizes[i] > src_sizes[i]) {
          index_larger_than_src_in_scatter = true;
          break;
        }
      }
    }

    // restride self and src such that
    // self.shape = src.shape = index.shape
    //
    // restride stride[dim] such that
    // if (is_scatter_like) self.stride[dim] = 0
    // else src.stride[dim] = 0
    auto self_restrided = is_scatter_like ?
        restride_dim(self, dim, index_sizes_vec)
      : self.as_strided(index_sizes_vec, self_strides_vec);
    auto src_restrided = is_scatter_like ?
        src.as_strided(index_sizes_vec, index_larger_than_src_in_scatter ? std::vector<int64_t> (ndim, 0) : src_strides_vec)
      : restride_dim(src, dim, index_sizes_vec);

    auto iter = TensorIteratorConfig()
      .set_check_mem_overlap(false)
      .check_all_same_dtype(false)
      .resize_outputs(false)
      .add_output(self_restrided)
      .add_input(src_restrided)
      .add_input(index)
      .build();

    auto self_dim_stride = ensure_nonempty_stride(self, dim);
    auto self_dim_size = ensure_nonempty_size(self, dim);

    auto src_dim_stride = ensure_nonempty_stride(src, dim);
    auto src_dim_size = ensure_nonempty_size(src, dim);

    auto index_size = is_scatter_like ? self_dim_size : src_dim_size;
    auto index_stride = is_scatter_like ? self_dim_stride : src_dim_stride;

    if (!index_larger_than_src_in_scatter) {
      AT_DISPATCH_ALL_TYPES_AND_COMPLEX_AND3(
        at::ScalarType::Half, at::ScalarType::Bool, at::ScalarType::BFloat16,
        iter.dtype(),
        "cuda_scatter_gather_base_kernel_func", [&] {
          using dtype = typename std::conditional<cast_to_opaque,
            OpaqueType<sizeof(scalar_t)>, scalar_t>::type;

          _cuda_scatter_gather_internal_kernel<is_scatter_like, dtype>()(
            iter, index_size, index_stride, self.numel(), f
          );
        }
      );
    }
    else {
      AT_DISPATCH_ALL_TYPES_AND_COMPLEX_AND3(
        at::ScalarType::Half, at::ScalarType::Bool, at::ScalarType::BFloat16,
        iter.dtype(),
        "cuda_scatter_gather_base_kernel_func", [&] {
          using dtype = typename std::conditional<cast_to_opaque,
            OpaqueType<sizeof(scalar_t)>, scalar_t>::type;

          _cuda_scatter_large_index_internal_kernel<dtype>()(
            iter, index_size, index_stride, index_sizes, index_strides, src_sizes, src_strides, self.numel(), f
          );
        }
      );
    }
  }

  void operator()(
    const Tensor& self, int64_t dim,
    const Tensor& index, const Tensor& src,
    const std::string& method_name,
    const TensorAssign& f
  ) {
    at::assert_no_internal_overlap(self);

    auto index_sizes = index.sizes();
    auto index_sizes_vec = ensure_nonempty_vec(index_sizes.vec());
    auto index_strides = index.strides();
    auto ndim = index_sizes.size();
    auto self_strides_vec = ensure_nonempty_vec(self.strides().vec());
    auto src_sizes = src.sizes();
    auto src_strides = src.strides();
    auto src_strides_vec = ensure_nonempty_vec(src_strides.vec());

    bool index_larger_than_src_in_scatter = false;
    if (is_scatter_like) {
      for (int i = 0; i < ndim; i++) {
        if (index_sizes[i] > src_sizes[i]) {
          index_larger_than_src_in_scatter = true;
          break;
        }
      }
    }

    // restride self and src such that
    // self.shape = src.shape = index.shape
    //
    // restride stride[dim] such that
    // if (is_scatter_like) self.stride[dim] = 0
    // else src.stride[dim] = 0
    auto self_restrided = is_scatter_like ?
        restride_dim(self, dim, index_sizes_vec)
      : self.as_strided(index_sizes_vec, self_strides_vec);
    auto src_restrided = is_scatter_like ?
        src.as_strided(index_sizes_vec, index_larger_than_src_in_scatter ? std::vector<int64_t> (ndim, 0) : src_strides_vec)
      : restride_dim(src, dim, index_sizes_vec);

    auto iter = TensorIteratorConfig()
      .set_check_mem_overlap(false)
      .check_all_same_dtype(false)
      .resize_outputs(false)
      .add_output(self_restrided)
      .add_input(src_restrided)
      .add_input(index)
      .build();

    auto self_dim_stride = ensure_nonempty_stride(self, dim);
    auto self_dim_size = ensure_nonempty_size(self, dim);

    auto src_dim_stride = ensure_nonempty_stride(src, dim);
    auto src_dim_size = ensure_nonempty_size(src, dim);

    auto index_size = is_scatter_like ? self_dim_size : src_dim_size;
    auto index_stride = is_scatter_like ? self_dim_stride : src_dim_stride;

    if (!index_larger_than_src_in_scatter) {
      AT_DISPATCH_ALL_TYPES_AND_COMPLEX_AND3(
        at::ScalarType::Half, at::ScalarType::Bool, at::ScalarType::BFloat16,
        iter.dtype(),
        "cuda_scatter_gather_base_kernel_func", [&] {
          using dtype = typename std::conditional<cast_to_opaque,
            OpaqueType<sizeof(scalar_t)>, scalar_t>::type;

          _cuda_scatter_gather_internal_kernel<is_scatter_like, dtype>()(
            iter, index_size, index_stride, self.numel(), f
          );
        }
      );
    }
    else {
      AT_DISPATCH_ALL_TYPES_AND_COMPLEX_AND3(
        at::ScalarType::Half, at::ScalarType::Bool, at::ScalarType::BFloat16,
        iter.dtype(),
        "cuda_scatter_gather_base_kernel_func", [&] {
          using dtype = typename std::conditional<cast_to_opaque,
            OpaqueType<sizeof(scalar_t)>, scalar_t>::type;

          _cuda_scatter_large_index_internal_kernel<dtype>()(
            iter, index_size, index_stride, index_sizes, index_strides, src_sizes, src_strides, self.numel(), f
          );
        }
      );
    }
  }

  template <typename func_t>
  void operator()(
    const Tensor& self, int64_t dim,
    const Tensor& index, const Tensor& src,
    const std::string& method_name,
    const func_t& f
  ) {
    at::assert_no_internal_overlap(self);

    auto index_sizes = index.sizes();
    auto index_sizes_vec = ensure_nonempty_vec(index_sizes.vec());
    auto index_strides = index.strides();
    auto ndim = index_sizes.size();
    auto self_strides_vec = ensure_nonempty_vec(self.strides().vec());
    auto src_sizes = src.sizes();
    auto src_strides = src.strides();
    auto src_strides_vec = ensure_nonempty_vec(src_strides.vec());

    bool index_larger_than_src_in_scatter = false;
    if (is_scatter_like) {
      for (int i = 0; i < ndim; i++) {
        if (index_sizes[i] > src_sizes[i]) {
          index_larger_than_src_in_scatter = true;
          break;
        }
      }
    }

    // restride self and src such that
    // self.shape = src.shape = index.shape
    //
    // restride stride[dim] such that
    // if (is_scatter_like) self.stride[dim] = 0
    // else src.stride[dim] = 0
    auto self_restrided = is_scatter_like ?
        restride_dim(self, dim, index_sizes_vec)
      : self.as_strided(index_sizes_vec, self_strides_vec);
    auto src_restrided = is_scatter_like ?
        src.as_strided(index_sizes_vec, index_larger_than_src_in_scatter ? std::vector<int64_t> (ndim, 0) : src_strides_vec)
      : restride_dim(src, dim, index_sizes_vec);

    auto iter = TensorIteratorConfig()
      .set_check_mem_overlap(false)
      .check_all_same_dtype(false)
      .resize_outputs(false)
      .add_output(self_restrided)
      .add_input(src_restrided)
      .add_input(index)
      .build();

    auto self_dim_stride = ensure_nonempty_stride(self, dim);
    auto self_dim_size = ensure_nonempty_size(self, dim);

    auto src_dim_stride = ensure_nonempty_stride(src, dim);
    auto src_dim_size = ensure_nonempty_size(src, dim);

    auto index_size = is_scatter_like ? self_dim_size : src_dim_size;
    auto index_stride = is_scatter_like ? self_dim_stride : src_dim_stride;

    if (!index_larger_than_src_in_scatter) {
      AT_DISPATCH_FLOATING_TYPES_AND2(
        at::ScalarType::Half, at::ScalarType::BFloat16,
        iter.dtype(),
        "cuda_scatter_gather_base_kernel_func", [&] {
          using dtype = typename std::conditional<cast_to_opaque,
            OpaqueType<sizeof(scalar_t)>, scalar_t>::type;

          _cuda_scatter_gather_internal_kernel<is_scatter_like, dtype>()(
            iter, index_size, index_stride, self.numel(), f
          );
        }
      );
    }
    else {
      AT_DISPATCH_FLOATING_TYPES_AND2(
        at::ScalarType::Half, at::ScalarType::BFloat16,
        iter.dtype(),
        "cuda_scatter_gather_base_kernel_func", [&] {
          using dtype = typename std::conditional<cast_to_opaque,
            OpaqueType<sizeof(scalar_t)>, scalar_t>::type;

          _cuda_scatter_large_index_internal_kernel<dtype>()(
            iter, index_size, index_stride, index_sizes, index_strides, src_sizes, src_strides, self.numel(), f
          );
        }
      );
    }
  }
}; // struct cuda_scatter_gather_base_kernel

template <typename scalar_t>
struct _cuda_scatter_fill_internal_kernel {
  template <typename func_t>
  void operator()(
    TensorIterator& iter,
    scalar_t src_val,
    int64_t index_size,
    int64_t index_stride,
    int64_t numel,  // Do not use `const` qualifier here as it may cause issue in cuda 11.6.x. See #75434, #75545
    const func_t& f
  ) {
    if (!iter.can_use_32bit_indexing()) {
      for (auto& sub_iter : iter.with_32bit_indexing()) {
        _cuda_scatter_fill_internal_kernel<scalar_t>()(
          sub_iter, src_val, index_size, index_stride, numel, f
        );
      }
      return;
    }

    char* self_ptr = (char*)iter.data_ptr(0);
    char* index_ptr = (char*)iter.data_ptr(1);

    auto offset_calc = make_offset_calculator<2>(iter);
    auto loop = [=]C10_DEVICE(int i) {
      auto offsets = offset_calc.get(i);

      int64_t idx_dim = *(int64_t*)(index_ptr + offsets[1]);
      CUDA_KERNEL_ASSERT(idx_dim >= 0 && idx_dim < index_size
        && "index out of bounds"
      );

      f(
        (scalar_t*)(self_ptr + offsets[0]),
        idx_dim * index_stride,
        numel,
        (scalar_t*)&src_val
      );
    };

    _launch_scatter_gather_kernel<num_threads(), thread_work_size()>(iter.numel(), loop);
  }
}; // struct _cuda_scatter_fill_internal_kernel

template <bool cast_to_opaque = true>
struct cuda_scatter_fill_base_kernel {
  template <typename func_t>
  void operator()(
    const Tensor& self, int64_t dim,
    const Tensor& index, Scalar src,
    const std::string& method_name,
    const func_t& f
  ) {
    at::assert_no_internal_overlap(self);

    auto index_sizes_vec = ensure_nonempty_vec(index.sizes().vec());

    // restride self such that
    // self.shape = index.shape and
    // self.stride[dim] = 0
    auto self_restrided = restride_dim(self, dim, index_sizes_vec);

    auto iter = TensorIteratorConfig()
      .set_check_mem_overlap(false)
      .check_all_same_dtype(false)
      .resize_outputs(false)
      .add_output(self_restrided)
      .add_input(index)
      .build();

    auto index_size = ensure_nonempty_size(self, dim);
    auto index_stride = ensure_nonempty_stride(self, dim);

    AT_DISPATCH_ALL_TYPES_AND_COMPLEX_AND3(
      at::ScalarType::Half, at::ScalarType::Bool, at::ScalarType::BFloat16,
      iter.dtype(),
      "cuda_scatter_fill_base_kernel_func", [&] {
        using dtype = typename std::conditional<cast_to_opaque,
          OpaqueType<sizeof(scalar_t)>, scalar_t>::type;

        auto src_scalar_val = src.to<scalar_t>();
        auto src_val = *(dtype*)&src_scalar_val;

        _cuda_scatter_fill_internal_kernel<dtype>()(
          iter, src_val, index_size, index_stride, self.numel(), f
        );
      }
    );
  }

  void operator()(
    const Tensor& self, int64_t dim,
    const Tensor& index, Scalar src,
    const std::string& method_name,
    const ReduceMultiply& f
  ) {
    at::assert_no_internal_overlap(self);

    auto index_sizes_vec = ensure_nonempty_vec(index.sizes().vec());

    // restride self such that
    // self.shape = index.shape and
    // self.stride[dim] = 0
    auto self_restrided = restride_dim(self, dim, index_sizes_vec);

    auto iter = TensorIteratorConfig()
      .set_check_mem_overlap(false)
      .check_all_same_dtype(false)
      .resize_outputs(false)
      .add_output(self_restrided)
      .add_input(index)
      .build();

    auto index_size = ensure_nonempty_size(self, dim);
    auto index_stride = ensure_nonempty_stride(self, dim);

    AT_DISPATCH_FLOATING_TYPES_AND2(
      at::ScalarType::Half, at::ScalarType::BFloat16,
      iter.dtype(),
      "cuda_scatter_fill_base_kernel_reduce_multiply", [&] {
        using dtype = typename std::conditional<cast_to_opaque,
          OpaqueType<sizeof(scalar_t)>, scalar_t>::type;

        auto src_scalar_val = src.to<scalar_t>();
        auto src_val = *(dtype*)&src_scalar_val;

        _cuda_scatter_fill_internal_kernel<dtype>()(
          iter, src_val, index_size, index_stride, self.numel(), f
        );
      }
    );
  }
}; // struct cuda_scatter_fill_base_kernel

void gather_cuda_kernel(const Tensor& result, const Tensor& self, int64_t dim, const Tensor& index) {
  cuda_scatter_gather_base_kernel</*is_scatter_like=*/false>()(
    result, dim, index, self,
    "gather_out_cuda", tensor_assign);
}

void scatter_cuda_kernel(const Tensor& self, int64_t dim, const Tensor& index, const Tensor& src) {
  cuda_scatter_gather_base_kernel<>()(
    self, dim, index, src,
    "scatter_cuda_", tensor_assign);
}

void scatter_fill_cuda_kernel(const Tensor& self, int64_t dim, const Tensor& index, const Scalar& src) {
  cuda_scatter_fill_base_kernel<>()(
    self, dim, index, src,
    "scatter_fill_cuda_", tensor_assign);
}

void scatter_add_cuda_kernel(const Tensor& self, int64_t dim, const Tensor& index, const Tensor& src) {
  // See Note [Writing Nondeterministic Operations]
  // Nondeterministic because of atomicAdd usage
  globalContext().alertNotDeterministic("scatter_add_cuda_kernel");
  cuda_scatter_gather_base_kernel</*is_scatter_like=*/true, /*cast_to_opaque=*/false>()(
    self, dim, index, src,
    "scatter_add_cuda_", reduce_add);
}

void scatter_reduce_cuda_kernel(const Tensor& self, const int64_t dim, const Tensor& index,
                               const Tensor& src, const SCATTER_GATHER_OP& reduce) {
  switch (reduce) {
  case SCATTER_GATHER_OP::REDUCE_ADD :
    cuda_scatter_gather_base_kernel<true, false>()(self, dim, index, src,
                                       "scatter_reduce_cuda_add_", reduce_add);
    break;
  case SCATTER_GATHER_OP::REDUCE_MULTIPLY :
    cuda_scatter_gather_base_kernel<true, false>()(self, dim, index, src,
                                       "scatter_reduce_cuda_multiply_", reduce_multiply);
    break;
  default :
    break;
  }
}

void scatter_reduce_two_cuda_kernel(const Tensor& self, const int64_t dim, const Tensor& index,
                                    const Tensor& src, const SCATTER_GATHER_OP& reduce) {
  globalContext().alertNotDeterministic("scatter_reduce_cuda");
  switch (reduce) {
  case SCATTER_GATHER_OP::REDUCE_ADD :
    cuda_scatter_gather_base_kernel<true, false>()(self, dim, index, src,
            "scatter_reduce_cuda_sum_", reduce_add);
    break;
  case SCATTER_GATHER_OP::REDUCE_MULTIPLY :
    cuda_scatter_gather_base_kernel<true, false>()(self, dim, index, src,
            "scatter_reduce_cuda_prod_", reduce_multiply);
    break;
  case SCATTER_GATHER_OP::REDUCE_MAXIMUM :
    cuda_scatter_gather_base_kernel<true, false>()(self, dim, index, src,
            "scatter_reduce_cuda_amax_", reduce_maximum);
    break;
  case SCATTER_GATHER_OP::REDUCE_MINIMUM :
    cuda_scatter_gather_base_kernel<true, false>()(self, dim, index, src,
            "scatter_reduce_cuda_amin_", reduce_minimum);
    break;
  case SCATTER_GATHER_OP::REDUCE_MEAN :
    cuda_scatter_gather_base_kernel<true, false>()(self, dim, index, src,
            "scatter_reduce_cuda_mean_", reduce_mean);
    break;
  }
}

void scatter_scalar_reduce_cuda_kernel(const Tensor& self, const int64_t dim, const Tensor& index,
                               const Scalar& value, const SCATTER_GATHER_OP& reduce) {
  switch (reduce) {
  case SCATTER_GATHER_OP::REDUCE_ADD :
    cuda_scatter_fill_base_kernel<false>()(self, dim, index, value,
                                      "scatter_fill_cuda_add_", reduce_add);
    break;
  case SCATTER_GATHER_OP::REDUCE_MULTIPLY :
    cuda_scatter_fill_base_kernel<false>()(self, dim, index, value,
                                      "scatter_fill_cuda_multiply_", reduce_multiply);
    break;
  default :
    break;
  }
}


REGISTER_DISPATCH(gather_stub, &gather_cuda_kernel);
REGISTER_DISPATCH(scatter_stub, &scatter_cuda_kernel);
REGISTER_DISPATCH(scatter_fill_stub, &scatter_fill_cuda_kernel);
REGISTER_DISPATCH(scatter_add_stub, &scatter_add_cuda_kernel);
REGISTER_DISPATCH(scatter_reduce_stub, &scatter_reduce_cuda_kernel);
REGISTER_DISPATCH(scatter_scalar_reduce_stub, &scatter_scalar_reduce_cuda_kernel);
REGISTER_DISPATCH(scatter_reduce_two_stub, &scatter_reduce_two_cuda_kernel);

}} // namespace at::native
