#include <ATen/native/ReduceOps.h>

#include <ATen/ATen.h>
#include <ATen/Dispatch.h>
#include <ATen/ExpandUtils.h>
#include <ATen/NativeFunctions.h>
#include <ATen/LegacyTHFunctions.h>
#include <ATen/WrapDimUtils.h>
#include <ATen/WrapDimUtilsMulti.h>
#include <ATen/native/ReduceOpsUtils.h>
#include <ATen/native/TensorIterator.h>

#include <algorithm>
#include <functional>
#include <limits>
#include <numeric>
#include <vector>
#include <map>

namespace at {
namespace native {

DEFINE_DISPATCH(sum_stub);
DEFINE_DISPATCH(std_var_stub);
DEFINE_DISPATCH(prod_stub);
DEFINE_DISPATCH(norm_stub);
DEFINE_DISPATCH(mean_stub);
DEFINE_DISPATCH(and_stub);
DEFINE_DISPATCH(or_stub);

static inline Tensor integer_upcast(const Tensor& self, optional<ScalarType> dtype) {
  ScalarType scalarType = self.type().scalarType();
  ScalarType upcast_scalarType = dtype.value_or(at::isIntegralType(scalarType) ? ScalarType::Long : scalarType);
  return self.toType(upcast_scalarType);
}

using DimMask = TensorIterator::DimMask;

static DimMask make_dim_mask(IntList dims, int ndim) {
  auto mask = DimMask();
  if (dims.empty()) {
    mask.flip();
  } else {
    for (int dim : dims) {
      mask.set(maybe_wrap_dim(dim, ndim));
    }
  }
  return mask;
}

static void allocate_reduction_result(
    Tensor& result, const Tensor& self, DimMask mask, bool keepdim,
    ScalarType dtype)
{
  auto shape = DimVector(self.sizes());
  for (int dim = shape.size() - 1; dim >= 0; dim--) {
    if (mask[dim]) {
      if (keepdim) {
        shape[dim] = 1;
      } else {
        shape.erase(shape.begin() + dim);
      }
    }
  }
  if (result.defined()) {
    result.resize_(shape);
  } else {
    result = at::empty(shape, self.type().toScalarType(dtype));
  }
}

static Tensor review_reduce_result(const Tensor& result, int ndim, DimMask mask, bool keepdim) {
  if (keepdim) {
    return result;
  }
  auto shape = DimVector(result.sizes());
  auto stride = DimVector(result.strides());
  for (int dim = 0; dim < ndim; dim++) {
    if (mask[dim]) {
      shape.insert(shape.begin() + dim, 1);
      stride.insert(stride.begin() + dim, 0);
    }
  }
  return result.as_strided(shape, stride);
}

static std::unique_ptr<TensorIterator> make_reduction(
    const char* name, Tensor& result, const Tensor& self, IntList dim,
    bool keepdim, ScalarType dtype)
{
  // check that result type and dtype match if provided
  AT_CHECK(
      !result.defined() || result.type().scalarType() == dtype,
      name, ": provided dtype must match dtype of result. Got ",
      toString(result.type().scalarType()),
      " and ",
      toString(dtype),
      ".");
  int ndim = self.dim();
  auto mask = make_dim_mask(dim, ndim);
  allocate_reduction_result(result, self, mask, keepdim, dtype);
  auto viewed_result = review_reduce_result(result, ndim, mask, keepdim);

  // special case for type promotion in mixed precision, improves computational
  // efficiency.
  // not generalize this to common mismatched input/output types to avoid cross
  // product of templated kernel launches.
  if (self.type().scalarType() == dtype ||
      (self.is_cuda() && self.type().scalarType() == kHalf && dtype == kFloat)) {
    return TensorIterator::reduce_op(viewed_result, self);
  }
  return TensorIterator::reduce_op(viewed_result, self.to(dtype));
}

static inline int64_t n_dim_size(const Tensor& self, IntList dim) {
  int64_t numel = 1;
  for (auto d : dim) {
    numel *= self.size(d);
  }
  return numel;
}

static inline Tensor cumsum(const Tensor& self, int64_t dim, optional<ScalarType> dtype) {
  return at::legacy::th::_th_cumsum(integer_upcast(self, dtype), dim);
}

Tensor cumsum(const Tensor& self, int64_t dim, ScalarType dtype) {
  return at::native::cumsum(self, dim, optional<ScalarType>(dtype));
}

Tensor cumsum(const Tensor& self, int64_t dim) {
  return at::native::cumsum(self, dim, c10::nullopt);
}

static inline Tensor& cumsum_out(Tensor& result, const Tensor& self, int64_t dim, optional<ScalarType> dtype) {
  // result type is favored over dtype; check that they match if provided (NumPy doesn't check)
  AT_CHECK(
      !dtype.has_value() || (result.type().scalarType() == dtype.value()),
      "provided dtype must match dtype of result in cumsum. Got ",
      toString(result.type().scalarType()),
      " and ",
      toString(dtype.value()),
      ".");
  return at::legacy::th::_th_cumsum_out(result, self.toType(result.type().scalarType()), dim);
}

Tensor& cumsum_out(Tensor& result, const Tensor& self, int64_t dim, ScalarType dtype) {
  return at::native::cumsum_out(result, self, dim, optional<ScalarType>(dtype));
}

Tensor& cumsum_out(Tensor& result, const Tensor& self, int64_t dim) {
  return at::native::cumsum_out(result, self, dim, c10::nullopt);
}

static inline Tensor cumprod(const Tensor& self, int64_t dim, optional<ScalarType> dtype) {
  return at::legacy::th::_th_cumprod(integer_upcast(self, dtype), dim);
}

Tensor cumprod(const Tensor& self, int64_t dim, ScalarType dtype) {
  return at::native::cumprod(self, dim, optional<ScalarType>(dtype));
}

Tensor cumprod(const Tensor& self, int64_t dim) {
  return at::native::cumprod(self, dim, c10::nullopt);
}

static inline Tensor& cumprod_out(Tensor& result, const Tensor& self, int64_t dim, optional<ScalarType> dtype) {
  // result type is favored over dtype; check that they match if provided (NumPy doesn't check)
  AT_CHECK(
      !dtype.has_value() || (result.type().scalarType() == dtype.value()),
      "provided dtype must match dtype of result in cumprod. Got ",
      toString(result.type().scalarType()),
      " and ",
      toString(dtype.value()),
      ".");
  return at::legacy::th::_th_cumprod_out(result, self.toType(result.type().scalarType()), dim);
}

Tensor& cumprod_out(Tensor& result, const Tensor& self, int64_t dim, ScalarType dtype) {
  return at::native::cumprod_out(result, self, dim, optional<ScalarType>(dtype));
}

Tensor& cumprod_out(Tensor& result, const Tensor& self, int64_t dim) {
  return at::native::cumprod_out(result, self, dim, c10::nullopt);
}

// ALL REDUCE #################################################################

static ScalarType get_dtype(Tensor& result, const Tensor& self, optional<ScalarType> dtype,
                            bool promote_integers=false) {
  if (dtype.has_value()) {
    return dtype.value();
  } else if (result.defined()) {
    return result.type().scalarType();
  }
  ScalarType src_type = self.type().scalarType();
  if (promote_integers && at::isIntegralType(src_type)) {
    return kLong;
  }
  return src_type;
}

static Tensor& sum_out(Tensor& result, const Tensor& self, IntList dim,
                       bool keepdim, optional<ScalarType> opt_dtype) {
  ScalarType dtype = get_dtype(result, self, opt_dtype, true);
  auto iter = make_reduction("sum", result, self, dim, keepdim, dtype);
  if (iter->numel() == 0) {
    result.zero_();
  } else {
    sum_stub(iter->device_type(), *iter);
  }
  return result;
}

static Tensor sum(const Tensor& self, IntList dim, bool keepdim, optional<ScalarType> dtype) {
  Tensor result;
  native::sum_out(result, self, dim, keepdim, dtype);
  return result;
}

Tensor sum(const Tensor &self, ScalarType dtype) {
  return at::native::sum(self, {}, false, optional<ScalarType>(dtype));
}

Tensor sum(const Tensor &self) {
  return at::native::sum(self, {}, false, c10::nullopt);
}

static Tensor& prod_out(Tensor& result, const Tensor& self, IntList dim,
                        bool keepdim, optional<ScalarType> opt_dtype) {
  ScalarType dtype = get_dtype(result, self, opt_dtype, true);
  auto iter = make_reduction("prod", result, self, dim, keepdim, dtype);
  if (iter->numel() == 0) {
    result.fill_(1);
  } else {
    prod_stub(iter->device_type(), *iter);
  }
  return result;
}

static Tensor prod(const Tensor& self, IntList dim, bool keepdim, optional<ScalarType> dtype) {
  Tensor result;
  native::prod_out(result, self, dim, keepdim, dtype);
  return result;
}

Tensor prod(const Tensor &self, ScalarType dtype) {
  return at::native::prod(self, {}, false, optional<ScalarType>(dtype));
}

Tensor prod(const Tensor &self) {
  return at::native::prod(self, {}, false, c10::nullopt);
}

static inline Tensor &mean_out(Tensor &result, const Tensor &self, IntList dim,
                 bool keepdim, optional<ScalarType> opt_dtype) {
  ScalarType scalarType = opt_dtype.has_value() ? opt_dtype.value() : self.type().scalarType();
  AT_CHECK(
      at::isFloatingType(scalarType),
      "Can only calculate the mean of floating types. Got ",
      toString(scalarType),
      " instead.");
  ScalarType dtype = get_dtype(result, self, opt_dtype, true);
  // TODO: the TensorIterator reduction implementation of mean
  // (mean_kernel_impl()) is unvectorized and leads to very poor performance
  // for production workloads. Once that's fixed, the following code can be used
  // in lieu of the sum + divide implementation below.
  if (self.device().is_cpu()) {
    int64_t dim_prod = 1;
    if (dim.size() == 0 || self.ndimension() == 0) {
      dim_prod = self.numel();
    } else {
      for (auto d : dim) {
        dim_prod *= self.size(d);
      }
    }
    at::sum_out(result, self, dim, keepdim, dtype).div_(dim_prod);
    return result;
  }

  auto iter = make_reduction("mean", result, self, dim, keepdim, dtype);
  if (iter->numel() == 0) {
    result.fill_(std::numeric_limits<double>::quiet_NaN());
  } else {
    mean_stub(iter->device_type(), *iter);
  }
  return result;
}

// \ALL REDUCE ################################################################

// DIM REDUCE #################################################################

Tensor& mean_out(Tensor& result, const Tensor& self, IntList dim, bool keepdim, ScalarType dtype) {
  return at::native::mean_out(
      result, self, dim, keepdim, c10::optional<ScalarType>(dtype));
}
Tensor& mean_out(Tensor& result, const Tensor& self, IntList dim, bool keepdim) {
  return at::native::mean_out(result, self, dim, keepdim, c10::nullopt);
}

Tensor& mean_out(Tensor& result, const Tensor& self, IntList dim, ScalarType dtype) {
  return at::native::mean_out(result, self, dim, false, dtype);
}

static inline Tensor mean(const Tensor &self, IntList dim, bool keepdim, optional<ScalarType> dtype) {
  Tensor result;
  return at::native::mean_out(result, self, dim, keepdim, dtype);
}

static inline Tensor mean(const Tensor &self, optional<ScalarType> dtype) {
  return at::native::mean(self, {}, false, dtype);
}

Tensor mean(const Tensor &self, ScalarType dtype) {
  return at::native::mean(self, optional<ScalarType>(dtype));
}

Tensor mean(const Tensor &self) {
  return at::native::mean(self, c10::nullopt);
}

Tensor& sum_out(Tensor& result, const Tensor& self, IntList dim, bool keepdim, ScalarType dtype) {
  return at::native::sum_out(
      result, self, dim, keepdim, c10::optional<ScalarType>(dtype));
}

Tensor& sum_out(Tensor& result, const Tensor& self, IntList dim, bool keepdim) {
  return at::native::sum_out(result, self, dim, keepdim, c10::nullopt);
}

Tensor& sum_out(Tensor& result, const Tensor& self, IntList dim, ScalarType dtype) {
  return at::native::sum_out(result, self, dim, false, dtype);
}

int64_t _safe_size(IntList sizes, IntList dim) {
  int64_t size = 1;
  if (sizes.size() == 0) {
    return 1;
  }
  for (auto d : dim) {
    d = at::maybe_wrap_dim(d, sizes.size());
    size *= sizes[d];
  }
  return size;
}

Tensor unsqueeze_multiple(const Tensor & t, IntList dim, size_t n_dims) {
    auto dims_to_unsqueeze = at::dim_list_to_bitset(dim, n_dims);
    Tensor res = t;
    for (size_t i = 0; i < n_dims; i++){
      if (dims_to_unsqueeze[i]) {
        res = res.unsqueeze(i);
      }
    }
    return res;
}

Tensor sum_backward(const Tensor & grad, IntList sizes, IntList dims, bool keepdim) {
  if (!keepdim && sizes.size() > 0) {
    if (dims.size()==1) {
      return grad.unsqueeze(dims[0]).expand(sizes);
    } else {
      Tensor res = unsqueeze_multiple(grad, dims, sizes.size());
      return res.expand(sizes);
    }
  } else {
    return grad.expand(sizes);
  }
}

Tensor& prod_out(Tensor& result, const Tensor& self, int64_t dim, bool keepdim, ScalarType dtype) {
  return at::native::prod_out(
      result, self, dim, keepdim, c10::optional<ScalarType>(dtype));
}

Tensor& prod_out(Tensor& result, const Tensor& self, int64_t dim, bool keepdim) {
  return at::native::prod_out(result, self, dim, keepdim, c10::nullopt);
}

Tensor& prod_out(Tensor& result, const Tensor& self, int64_t dim, ScalarType dtype) {
  return at::native::prod_out(result, self, dim, false, dtype);
}

Tensor mean(const Tensor& self, IntList dim, bool keepdim, ScalarType dtype) {
  return at::native::mean(self, dim, keepdim, c10::optional<ScalarType>(dtype));
}

Tensor mean(const Tensor& self, IntList dim, bool keepdim) {
  return at::native::mean(self, dim, keepdim, c10::nullopt);
}

Tensor mean(const Tensor& self, IntList dim, ScalarType dtype) {
  return at::native::mean(self, dim, false, dtype);
}

Tensor sum(const Tensor& self, IntList dim, bool keepdim, ScalarType dtype) {
  return at::native::sum(self, dim, keepdim, c10::optional<ScalarType>(dtype));
}

Tensor sum(const Tensor& self, IntList dim, bool keepdim) {
  return at::native::sum(self, dim, keepdim, c10::nullopt);
}

Tensor sum(const Tensor& self, IntList dim, ScalarType dtype) {
  return at::native::sum(self, dim, false, dtype);
}

Tensor prod(const Tensor& self, int64_t dim, bool keepdim, ScalarType dtype) {
  return at::native::prod(self, dim, keepdim, c10::optional<ScalarType>(dtype));
}

Tensor prod(const Tensor& self, int64_t dim, bool keepdim) {
  return at::native::prod(self, dim, keepdim, c10::nullopt);
}

Tensor prod(const Tensor& self, int64_t dim, ScalarType dtype) {
  return at::native::prod(self, dim, false, dtype);
}

Tensor& logsumexp_out(Tensor& result, const Tensor &self, int64_t dim_, bool keepdim) {
  int64_t dim = maybe_wrap_dim(dim_, self.dim());
  // can't take max of empty tensor
  if (self.numel() != 0) {
    auto maxes = at::max_values(self, dim, true);
    auto maxes_squeezed = (keepdim ? maxes : maxes.squeeze(dim));
    maxes_squeezed.masked_fill_(maxes_squeezed.abs() == INFINITY, 0);
    at::sum_out(result, at::exp(self - maxes), dim, keepdim);
    result.log_().add_(maxes_squeezed);
  } else {
    at::sum_out(result, at::exp(self), dim, keepdim);
    result.log_();
  }
  return result;
}

Tensor logsumexp(const Tensor &self, int64_t dim_, bool keepdim) {
  int64_t dim = maybe_wrap_dim(dim_, self.dim());
  Tensor result = at::empty({0}, self.options());
  return at::native::logsumexp_out(result, self, dim, keepdim);
}

static Tensor& norm_out(Tensor &result, const Tensor &self, optional<Scalar> opt_p,
                               IntList dim, bool keepdim, optional<ScalarType> opt_dtype) {
  auto p = opt_p.value_or(2.0);
  AT_CHECK(self.type().backend() == Backend::CPU || self.type().backend() == Backend::CUDA,
           "norm only supports CPU AND CUDA backend, got: ", toString(self.type().backend()));

  ScalarType scalarType = opt_dtype.has_value() ? opt_dtype.value() : self.type().scalarType();
  AT_CHECK(
      at::isFloatingType(scalarType),
      "Can only calculate the mean of floating types. Got ",
      toString(scalarType),
      " instead.");

  ScalarType dtype = get_dtype(result, self, opt_dtype, true);
  auto iter = make_reduction("norm", result, self, dim, keepdim, dtype);
  if (iter->numel() == 0) {
    result.zero_();
  } else {
    norm_stub(iter->device_type(), *iter, p);
  }
  return result;
}

static inline Tensor _norm(const Tensor &self, Scalar p) {
  if (self.is_sparse()) {
    return at::native_norm(self, p);
  } else {
    AT_CHECK(self.type().backend() == Backend::CPU || self.type().backend() == Backend::CUDA,
             "norm only supports CPU AND CUDA backend, got: ", toString(self.type().backend()));
    AT_CHECK(at::isFloatingType(self.type().scalarType()), "norm only supports floating-point dtypes");

    Tensor result;
    return at::native::norm_out(result, self, p, {}, false, c10::nullopt);
  }
}

Tensor &norm_out(Tensor& result, const Tensor& self, optional<Scalar> p, IntList dim, bool keepdim, ScalarType dtype) {
  return at::native::norm_out(result, self, p, dim, keepdim, optional<ScalarType>(dtype));
}

Tensor &norm_out(Tensor& result, const Tensor& self, optional<Scalar> p, IntList dim, bool keepdim) {
  return at::native::norm_out(result, self, p, dim, keepdim, c10::nullopt);
}

static Tensor norm(const Tensor& self, optional<Scalar> p, IntList dim, bool keepdim,
            optional<ScalarType> opt_dtype) {
  Tensor result;
  return at::native::norm_out(result, self, p, dim, keepdim, opt_dtype);
}

Tensor norm(const Tensor& self, optional<Scalar> p, IntList dim, bool keepdim, ScalarType dtype) {
  return at::native::norm(self, p, dim, keepdim, optional<ScalarType>(dtype));
}

Tensor norm(const Tensor& self, optional<Scalar> p, ScalarType dtype) {
  return at::native::norm(self, p, {}, false, optional<ScalarType>(dtype));
}

Tensor norm(const Tensor& self, optional<Scalar> p, IntList dim, bool keepdim) {
  return at::native::norm(self, p, dim, keepdim, c10::nullopt);
}

// leave it so we support sparse tensors
Tensor norm(const Tensor& self, Scalar p) {
  return at::native::_norm(self, p);
}

inline Tensor & _all(Tensor & result, std::unique_ptr<TensorIterator> & iter) {
  if (iter->numel() == 0) {
    result.fill_(1);
  } else {
    and_stub(iter->device_type(), *iter);
  }

  return result;
}

Tensor all(const Tensor& self) {
  AT_CHECK(self.type().backend() == Backend::CPU ||
    self.type().backend() == Backend::CUDA, "all only supports CPU AND CUDA "
    "backend, got: ", toString(self.type().backend()));
  AT_CHECK(self.type().scalarType() == at::ScalarType::Byte,
    "all only supports torch.uint8 dtype");

  Tensor result = at::empty({0}, self.options());
  auto iter = make_reduction(
    "all", result, self, {}, false, at::ScalarType::Byte);
  return _all(result, iter);
}

Tensor all(const Tensor& self, int64_t dim, bool keepdim) {
  Tensor result = at::empty({0}, self.options());
  return at::native::all_out(result, self, dim, keepdim);
}

Tensor &all_out(Tensor &result, const Tensor &self, int64_t dim, bool keepdim) {
  AT_CHECK(self.type().backend() == Backend::CPU ||
    self.type().backend() == Backend::CUDA, "all only supports CPU AND CUDA "
    "backend, got: ", toString(self.type().backend()));
  AT_CHECK(self.type().scalarType() == at::ScalarType::Byte,
    "all only supports torch.uint8 dtype");
  dim = maybe_wrap_dim(dim, self.dim());
  if (_dimreduce_return_trivial(result, self, 1, dim, keepdim)) {
    return result;
  } else {
    auto iter = make_reduction(
      "all", result, self, dim, keepdim, at::ScalarType::Byte);
    return _all(result, iter);
  }
}

inline Tensor & _any(Tensor & result, std::unique_ptr<TensorIterator> & iter) {
  if (iter->numel() == 0) {
    result.fill_(0);
  } else {
    or_stub(iter->device_type(), *iter);
  }

  return result;
}

Tensor any(const Tensor& self) {
  AT_CHECK(self.type().backend() == Backend::CPU ||
    self.type().backend() == Backend::CUDA, "any only supports CPU AND CUDA "
    "backend, got: ", toString(self.type().backend()));
  AT_CHECK(self.type().scalarType() == at::ScalarType::Byte,
    "any only supports torch.uint8 dtype");

  Tensor result = at::empty({0}, self.options());
  auto iter = make_reduction(
    "any", result, self, {}, false, at::ScalarType::Byte);
  return _any(result, iter);
}

Tensor any(const Tensor& self, int64_t dim, bool keepdim) {
  Tensor result = at::empty({0}, self.options());
  return at::native::any_out(result, self, dim, keepdim);
}

Tensor &any_out(Tensor &result, const Tensor &self, int64_t dim, bool keepdim) {
  AT_CHECK(self.type().backend() == Backend::CPU ||
    self.type().backend() == Backend::CUDA, "any only supports CPU AND CUDA "
    "backend, got: ", toString(self.type().backend()));
  AT_CHECK(self.type().scalarType() == at::ScalarType::Byte,
    "any only supports torch.uint8 dtype");
  dim = maybe_wrap_dim(dim, self.dim());
  if (_dimreduce_return_trivial(result, self, 0, dim, keepdim)) {
    return result;
  } else {
    auto iter = make_reduction(
      "any", result, self, dim, keepdim, at::ScalarType::Byte);
    return _any(result, iter);
  }
}

static Tensor &std_var_out(Tensor &result, const Tensor &self, IntList dim, bool unbiased, bool keepdim, bool take_sqrt) {
  AT_CHECK(self.type().backend() == Backend::CPU || self.type().backend() == Backend::CUDA,
           "std and var only support CPU AND CUDA backend, got: ", toString(self.type().backend()));
  AT_CHECK(at::isFloatingType(self.type().scalarType()), "std and var only support floating-point dtypes");
  ScalarType dtype = get_dtype(result, self, {}, true);
  auto iter = make_reduction("std or var", result, self, dim, keepdim, dtype);
  if (iter->numel() == 0) {
    result.fill_(NAN);
  } else {
    std_var_stub(iter->device_type(), *iter, unbiased, take_sqrt);
  }
  return result;
}

Tensor var(const Tensor& self, bool unbiased) {
  AT_CHECK(self.type().backend() == Backend::CPU || self.type().backend() == Backend::CUDA,
           "var only supports CPU AND CUDA backend, got: ", toString(self.type().backend()));
  AT_CHECK(at::isFloatingType(self.type().scalarType()), "var only supports floating-point dtypes");
  auto trivial_return = _allreduce_return_trivial(self, std::numeric_limits<double>::quiet_NaN());
  return trivial_return.has_value() ? trivial_return.value() : at::legacy::th::_th_var(self, unbiased);
}

Tensor var(const Tensor& self, IntList dim, bool unbiased, bool keepdim) {
  Tensor result = at::empty({0}, self.options());
  return at::native::var_out(result, self, dim, unbiased, keepdim);
}

Tensor &var_out(Tensor &result, const Tensor &self, IntList dim, bool unbiased, bool keepdim) {
  return std_var_out(result, self, dim, unbiased, keepdim, false);
}

Tensor var_backward(const Tensor & grad, const Tensor & self, bool unbiased) {
  return (2.0 / (self.numel() - unbiased)) * grad * (self - self.mean());
}

Tensor var_backward(const Tensor & grad, const Tensor & self, IntList dim, bool unbiased, bool keepdim) {
  if (self.dim() == 0) {
    return at::var_backward(grad, self, unbiased);
  }
  Tensor unsqueezed_grad = grad;
  if (!keepdim && self.dim() > 1) {
    unsqueezed_grad = unsqueeze_multiple(grad, dim, self.sizes().size());
  }
  return (2.0 / (at::_safe_size(self.sizes(), dim) - unbiased)) * unsqueezed_grad * (self - self.mean(dim, true));
}

Tensor std(const Tensor& self, bool unbiased) {
  AT_CHECK(self.type().backend() == Backend::CPU || self.type().backend() == Backend::CUDA,
           "std only supports CPU AND CUDA backend, got: ", toString(self.type().backend()));
  AT_CHECK(at::isFloatingType(self.type().scalarType()), "std only supports floating-point dtypes");
  auto trivial_return = _allreduce_return_trivial(self, std::numeric_limits<double>::quiet_NaN());
  return trivial_return.has_value() ? trivial_return.value() : at::legacy::th::_th_std(self, unbiased);
}

Tensor std(const Tensor& self, IntList dim, bool unbiased, bool keepdim) {
  Tensor result = at::empty({0}, self.options());
  return at::native::std_out(result, self, dim, unbiased, keepdim);
}

Tensor &std_out(Tensor &result, const Tensor &self, IntList dim, bool unbiased, bool keepdim) {
  return std_var_out(result, self, dim, unbiased, keepdim, true);
}

}} // namespace at::native
