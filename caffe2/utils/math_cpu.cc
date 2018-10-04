// Implements the math functions for CPU.
// The implementation in this file allows us to route the underlying numerical
// computation library to different backends. Notably:
// (1) For all BLAS-related functions, one can explicitly request a BLAS backend
//     such as MKL, openblas or Atlas. To see the set of supported backends
//     currently provided, check //third_party/blas/.
// (2) If one chooses to link against MKL, we utilize MKL's vector math library
//     (VML) for a few functions such as Exp and Log.
// (3) Fallback implementations are provided in Eigen for cross-platform
//     support. Since Eigen is a header-only library and supports a number of
//     platforms, it allows one to quickly port Caffe2 to different platforms
//     where BLAS may not be present.

#include "caffe2/utils/math.h"

#include <algorithm>
#include <array>
#include <atomic>
#include <chrono>
#include <cmath>
#include <cstdlib>
#include <cstring>
#include <functional>
#include <limits>
#include <numeric>
#include <random>
#include <tuple>
#include <unordered_set>
#include <vector>

#include "caffe2/core/context.h"
#include "caffe2/utils/cpu_neon.h"
#include "caffe2/utils/eigen_utils.h"
#include "caffe2/utils/fixed_divisor.h"

#include "Eigen/Core"
#include "Eigen/Dense"

#ifdef CAFFE2_USE_MKL
#include <mkl.h>
#endif // CAFFE2_USE_MKL

#ifdef CAFFE2_USE_HPTT
#include <hptt.h>
#endif // CAFFE2_USE_HPTT

#if defined(_MSC_VER)
#include <process.h>
#endif

namespace caffe2 {

namespace math {

////////////////////////////////////////////////////////////////////////////////
// BLAS alternatives.
// Depending on whether we have specified an external BLAS library or not, we
// will delegate the Caffe math functions that are BLAS-related to either the
// CBLAS call or the Eigen implementation.
////////////////////////////////////////////////////////////////////////////////
#ifdef CAFFE2_USE_EIGEN_FOR_BLAS

// Caffe2 gemm provides a simpler interface to the gemm functions, with the
// limitation that the data has to be contiguous in memory.
//
// The gemm call implements the following operation:
//
//                  C = alpha * op(A) * op(B) + beta * C
//
// where op(A) has size M x K, op(B) has size K x N, and C has size M x N. Each
// of A, B, and C are matrices and alpha and beta are scalars. Note that the
// most common use case of gemm will involve setting alpha to 1 and beta to 0.
//
// op(A) and op(B) represent the transformations that are done to A and B before
// the matrix multiply; depending on the flags set, op(A) is equal to A or A^T
// (transpose) if the argument TransA or TransB is set to CblasNoTrans or
// CblasTrans, respectively, for each of A and B.
template <>
C10_EXPORT void Gemm<float, CPUContext>(
    const CBLAS_TRANSPOSE trans_A,
    const CBLAS_TRANSPOSE trans_B,
    const int M,
    const int N,
    const int K,
    const float alpha,
    const float* A,
    const float* B,
    const float beta,
    float* C,
    CPUContext* context,
    TensorProto::DataType math_type) {
  auto C_mat = EigenMatrixMap<float>(C, N, M);
  if (beta == 0) {
    C_mat.setZero();
  } else {
    C_mat *= beta;
  }
  switch (trans_A) {
    case CblasNoTrans: {
      switch (trans_B) {
        case CblasNoTrans:
          C_mat.noalias() += alpha *
              (ConstEigenMatrixMap<float>(B, N, K) *
               ConstEigenMatrixMap<float>(A, K, M));
          return;
        case CblasTrans:
          C_mat.noalias() += alpha *
              (ConstEigenMatrixMap<float>(B, K, N).transpose() *
               ConstEigenMatrixMap<float>(A, K, M));
          return;
        default:
          LOG(FATAL) << "Unexpected CBLAS_TRANSPOSE for trans_B";
      }
    }
    case CblasTrans: {
      switch (trans_B) {
        case CblasNoTrans:
          C_mat.noalias() += alpha *
              (ConstEigenMatrixMap<float>(B, N, K) *
               ConstEigenMatrixMap<float>(A, M, K).transpose());
          return;
        case CblasTrans:
          C_mat.noalias() += alpha *
              (ConstEigenMatrixMap<float>(B, K, N).transpose() *
               ConstEigenMatrixMap<float>(A, M, K).transpose());
          return;
        default:
          LOG(FATAL) << "Unexpected CBLAS_TRANSPOSE for trans_B";
      }
    }
    default:
      LOG(FATAL) << "Unexpected CBLAS_TRANSPOSE for trans_A";
  }
}

template <>
C10_EXPORT void GemmEx<float, CPUContext>(
    const CBLAS_TRANSPOSE trans_A,
    const CBLAS_TRANSPOSE trans_B,
    const int M,
    const int N,
    const int K,
    const float alpha,
    const float* A,
    const int lda,
    const float* B,
    const int ldb,
    const float beta,
    float* C,
    const int ldc,
    CPUContext*) {
  EigenOuterStridedMatrixMap<float> C_mat(C, N, M, EigenOuterStride(ldc));
  if (beta == 0) {
    C_mat.setZero();
  } else {
    C_mat *= beta;
  }
  switch (trans_A) {
    case CblasNoTrans: {
      switch (trans_B) {
        case CblasNoTrans:
          C_mat.noalias() += alpha *
              (ConstEigenOuterStridedMatrixMap<float>(
                   B, N, K, EigenOuterStride(ldb)) *
               ConstEigenOuterStridedMatrixMap<float>(
                   A, K, M, EigenOuterStride(lda)));
          return;
        case CblasTrans:
          C_mat.noalias() += alpha *
              (ConstEigenOuterStridedMatrixMap<float>(
                   B, K, N, EigenOuterStride(ldb))
                   .transpose() *
               ConstEigenOuterStridedMatrixMap<float>(
                   A, K, M, EigenOuterStride(lda)));
          return;
        default:
          LOG(FATAL) << "Unexpected CBLAS_TRANSPOSE for trans_B";
      }
    }
    case CblasTrans: {
      switch (trans_B) {
        case CblasNoTrans:
          C_mat.noalias() += alpha *
              (ConstEigenOuterStridedMatrixMap<float>(
                   B, N, K, EigenOuterStride(ldb)) *
               ConstEigenOuterStridedMatrixMap<float>(
                   A, M, K, EigenOuterStride(lda))
                   .transpose());
          return;
        case CblasTrans:
          C_mat.noalias() += alpha *
              (ConstEigenOuterStridedMatrixMap<float>(
                   B, K, N, EigenOuterStride(ldb))
                   .transpose() *
               ConstEigenOuterStridedMatrixMap<float>(
                   A, M, K, EigenOuterStride(lda))
                   .transpose());
          return;
        default:
          LOG(FATAL) << "Unexpected CBLAS_TRANSPOSE for trans_B";
      }
    }
    default:
      LOG(FATAL) << "Unexpected CBLAS_TRANSPOSE for trans_A";
  }
}

template <>
C10_EXPORT void Gemv<float, CPUContext>(
    const CBLAS_TRANSPOSE trans_A,
    const int M,
    const int N,
    const float alpha,
    const float* A,
    const float* x,
    const float beta,
    float* y,
    CPUContext* context,
    TensorProto::DataType math_type) {
  EigenVectorMap<float> y_vec(y, trans_A == CblasNoTrans ? M : N);
  if (beta == 0) {
    // In Caffe2 we often do a lazy initialization, which may contain NaNs in
    // the float values. As a result, if beta is 0, we explicitly do a setzero.
    y_vec.setZero();
  } else {
    y_vec *= beta;
  }
  switch (trans_A) {
    case CblasNoTrans: {
      y_vec.noalias() += alpha *
          (ConstEigenMatrixMap<float>(A, N, M).transpose() *
           ConstEigenVectorMap<float>(x, N));
      return;
    }
    case CblasTrans: {
      y_vec.noalias() += alpha *
          (ConstEigenMatrixMap<float>(A, N, M) *
           ConstEigenVectorMap<float>(x, M));
      return;
    }
    default:
      LOG(FATAL) << "Gemv float found an unexpected CBLAS_TRANSPOSE input.";
  }
}

#define CAFFE2_SPECIALIZED_DOT(T)                                        \
  template <>                                                            \
  C10_EXPORT void Dot<T, CPUContext>(                                    \
      const int N, const T* a, const T* b, T* y, CPUContext* context) {  \
    *y = ConstEigenVectorMap<T>(a, N).dot(ConstEigenVectorMap<T>(b, N)); \
  }
CAFFE2_SPECIALIZED_DOT(float)
#undef CAFFE2_SPECIALIZED_DOT

#define CAFFE2_SPECIALIZED_AXPY(T)                                          \
  template <>                                                               \
  C10_EXPORT void Axpy<T, CPUContext>(                                      \
      const int N, const T alpha, const T* x, T* Y, CPUContext* context) {  \
    EigenVectorMap<T>(Y, N) += ConstEigenVectorMap<T>(x, N) * alpha;        \
  }                                                                         \
  template <>                                                               \
  C10_EXPORT void Axpy<T, CPUContext>(                                      \
      const int N, const T* alpha, const T* x, T* Y, CPUContext* context) { \
    EigenVectorMap<T>(Y, N) += ConstEigenVectorMap<T>(x, N) * (*alpha);     \
  }
CAFFE2_SPECIALIZED_AXPY(float)
#undef CAFFE2_SPECIALIZED_AXPY

#define CAFFE2_SPECIALIZED_AXPBY(T)                                     \
  template <>                                                           \
  C10_EXPORT void Axpby<T, T, CPUContext>(                              \
      const int N,                                                      \
      const T alpha,                                                    \
      const T* x,                                                       \
      const T beta,                                                     \
      T* y,                                                             \
      CPUContext* context) {                                            \
    EigenVectorArrayMap<T> y_arr(y, N);                                 \
    y_arr = y_arr * beta + ConstEigenVectorArrayMap<T>(x, N) * alpha;   \
  }                                                                     \
  template <>                                                           \
  C10_EXPORT void Axpby<T, T, CPUContext>(                              \
      const int N,                                                      \
      const T* alpha,                                                   \
      const T* x,                                                       \
      const T* beta,                                                    \
      T* y,                                                             \
      CPUContext* context) {                                            \
    EigenVectorArrayMap<T> y_arr(y, N);                                 \
    y_arr = y_arr * *beta + ConstEigenVectorArrayMap<T>(x, N) * *alpha; \
  }
CAFFE2_SPECIALIZED_AXPBY(float)
#undef CAFFE2_SPECIALIZED_AXPBY

#else // CAFFE2_USE_EIGEN_FOR_BLAS

template <>
C10_EXPORT void Gemm<float, CPUContext>(
    const CBLAS_TRANSPOSE trans_A,
    const CBLAS_TRANSPOSE trans_B,
    const int M,
    const int N,
    const int K,
    const float alpha,
    const float* A,
    const float* B,
    const float beta,
    float* C,
    CPUContext* /*context*/,
    TensorProto::DataType /*math_type*/) {
  const int lda = (trans_A == CblasNoTrans) ? K : M;
  const int ldb = (trans_B == CblasNoTrans) ? N : K;
  cblas_sgemm(
      CblasRowMajor,
      trans_A,
      trans_B,
      M,
      N,
      K,
      alpha,
      A,
      lda,
      B,
      ldb,
      beta,
      C,
      N);
}

template <>
C10_EXPORT void GemmEx<float, CPUContext>(
    const CBLAS_TRANSPOSE trans_A,
    const CBLAS_TRANSPOSE trans_B,
    const int M,
    const int N,
    const int K,
    const float alpha,
    const float* A,
    const int lda,
    const float* B,
    const int ldb,
    const float beta,
    float* C,
    const int ldc,
    CPUContext* /*context*/) {
  cblas_sgemm(
      CblasRowMajor,
      trans_A,
      trans_B,
      M,
      N,
      K,
      alpha,
      A,
      lda,
      B,
      ldb,
      beta,
      C,
      ldc);
}

template <>
C10_EXPORT void Gemv<float, CPUContext>(
    const CBLAS_TRANSPOSE trans_A,
    const int M,
    const int N,
    const float alpha,
    const float* A,
    const float* x,
    const float beta,
    float* y,
    CPUContext* /*context*/,
    TensorProto::DataType /*math_type*/) {
  cblas_sgemv(CblasRowMajor, trans_A, M, N, alpha, A, N, x, 1, beta, y, 1);
}

#define CAFFE2_SPECIALIZED_SCALE(TAlpha, TData, prefix)          \
  template <>                                                    \
  C10_EXPORT void Scale<TAlpha, TData, CPUContext>(              \
      const int n,                                               \
      const TAlpha alpha,                                        \
      const TData* x,                                            \
      TData* y,                                                  \
      CPUContext*) {                                             \
    if (y != x) {                                                \
      cblas_##prefix##copy(n, x, 1, y, 1);                       \
    }                                                            \
    if (alpha != TAlpha(1)) {                                    \
      cblas_##prefix##scal(n, static_cast<TData>(alpha), y, 1);  \
    }                                                            \
  }                                                              \
  template <>                                                    \
  C10_EXPORT void Scale<TAlpha, TData, CPUContext>(              \
      const int n,                                               \
      const TAlpha* alpha,                                       \
      const TData* x,                                            \
      TData* y,                                                  \
      CPUContext*) {                                             \
    if (y != x) {                                                \
      cblas_##prefix##copy(n, x, 1, y, 1);                       \
    }                                                            \
    if (*alpha != TAlpha(1)) {                                   \
      cblas_##prefix##scal(n, static_cast<TData>(*alpha), y, 1); \
    }                                                            \
  }
CAFFE2_SPECIALIZED_SCALE(float, float, s)
CAFFE2_SPECIALIZED_SCALE(double, double, d)
CAFFE2_SPECIALIZED_SCALE(float, double, d)
#undef CAFFE2_SPECIALIZED_SCALE

#define CAFFE2_SPECIALIZED_DOT(T, prefix)                       \
  template <>                                                   \
  C10_EXPORT void Dot<T, CPUContext>(                           \
      const int N, const T* a, const T* b, T* y, CPUContext*) { \
    *y = cblas_##prefix##dot(N, a, 1, b, 1);                    \
  }
CAFFE2_SPECIALIZED_DOT(float, s)
#undef CAFFE2_SPECIALIZED_DOT

#define CAFFE2_SPECIALIZED_AXPY(T, prefix)                          \
  template <>                                                       \
  C10_EXPORT void Axpy<T, CPUContext>(                              \
      const int N, const T alpha, const T* x, T* y, CPUContext*) {  \
    cblas_##prefix##axpy(N, alpha, x, 1, y, 1);                     \
  }                                                                 \
  template <>                                                       \
  C10_EXPORT void Axpy<T, CPUContext>(                              \
      const int N, const T* alpha, const T* x, T* y, CPUContext*) { \
    cblas_##prefix##axpy(N, *alpha, x, 1, y, 1);                    \
  }
CAFFE2_SPECIALIZED_AXPY(float, s)
#undef CAFFE2_SPECIALIZED_AXPY

// cblas_[sd]axpby is not a standard blas function, and if MKL is not present,
// we will need to implement it.
#ifdef CAFFE2_USE_MKL
#define CAFFE2_SPECIALIZED_AXPBY(T, prefix)              \
  template <>                                            \
  C10_EXPORT void Axpby<T, T, CPUContext>(               \
      const int N,                                       \
      const T alpha,                                     \
      const T* x,                                        \
      const T beta,                                      \
      T* y,                                              \
      CPUContext*) {                                     \
    cblas_##prefix##axpby(N, alpha, x, 1, beta, y, 1);   \
  }                                                      \
  template <>                                            \
  C10_EXPORT void Axpby<T, T, CPUContext>(               \
      const int N,                                       \
      const T* alpha,                                    \
      const T* x,                                        \
      const T* beta,                                     \
      T* y,                                              \
      CPUContext*) {                                     \
    cblas_##prefix##axpby(N, *alpha, x, 1, *beta, y, 1); \
  }
#else // CAFFE2_USE_MKL
#define CAFFE2_SPECIALIZED_AXPBY(T, prefix)      \
  template <>                                    \
  C10_EXPORT void Axpby<T, T, CPUContext>(       \
      const int N,                               \
      const T alpha,                             \
      const T* x,                                \
      const T beta,                              \
      T* y,                                      \
      CPUContext*) {                             \
    cblas_##prefix##scal(N, beta, y, 1);         \
    cblas_##prefix##axpy(N, alpha, x, 1, y, 1);  \
  }                                              \
  template <>                                    \
  C10_EXPORT void Axpby<T, T, CPUContext>(       \
      const int N,                               \
      const T* alpha,                            \
      const T* x,                                \
      const T* beta,                             \
      T* y,                                      \
      CPUContext*) {                             \
    cblas_##prefix##scal(N, *beta, y, 1);        \
    cblas_##prefix##axpy(N, *alpha, x, 1, y, 1); \
  }
#endif // CAFFE2_USE_MKL
CAFFE2_SPECIALIZED_AXPBY(float, s)
#undef CAFFE2_SPECIALIZED_AXPBY

#endif // CAFFE2_USE_EIGEN_FOR_BLAS

#define CAFFE2_SPECIALIZED_SCALE(TAlpha, TData)                        \
  template <>                                                          \
  C10_EXPORT void Scale<TAlpha, TData, CPUContext>(                    \
      const int n,                                                     \
      const TAlpha alpha,                                              \
      const TData* x,                                                  \
      TData* y,                                                        \
      CPUContext* /* context */) {                                     \
    EigenVectorMap<TData>(y, n) =                                      \
        ConstEigenVectorMap<TData>(x, n) * static_cast<TData>(alpha);  \
  }                                                                    \
  template <>                                                          \
  C10_EXPORT void Scale<TAlpha, TData, CPUContext>(                    \
      const int n,                                                     \
      const TAlpha* alpha,                                             \
      const TData* x,                                                  \
      TData* y,                                                        \
      CPUContext* /* context */) {                                     \
    EigenVectorMap<TData>(y, n) =                                      \
        ConstEigenVectorMap<TData>(x, n) * static_cast<TData>(*alpha); \
  }
#ifdef CAFFE2_USE_EIGEN_FOR_BLAS
CAFFE2_SPECIALIZED_SCALE(float, float)
CAFFE2_SPECIALIZED_SCALE(double, double)
CAFFE2_SPECIALIZED_SCALE(float, double)
#endif // CAFFE2_USE_EIGEN_FOR_BLAS
CAFFE2_SPECIALIZED_SCALE(std::int32_t, std::int32_t)
CAFFE2_SPECIALIZED_SCALE(std::int64_t, std::int64_t)
#undef CAFFE2_SPECIALIZED_SCALE

template <>
C10_EXPORT void GemmBatched<float, CPUContext>(
    const CBLAS_TRANSPOSE trans_A,
    const CBLAS_TRANSPOSE trans_B,
    const int batch_size,
    const int M,
    const int N,
    const int K,
    const float alpha,
    const float** A,
    const float** B,
    const float beta,
    float** C,
    CPUContext* context,
    TensorProto::DataType /* math_type */) {
#ifdef CAFFE2_USE_MKL
  (void)context;
  const int lda = (trans_A == CblasNoTrans) ? K : M;
  const int ldb = (trans_B == CblasNoTrans) ? N : K;
  const int ldc = N;
  cblas_sgemm_batch(
      CblasRowMajor,
      &trans_A,
      &trans_B,
      &M,
      &N,
      &K,
      &alpha,
      A,
      &lda,
      B,
      &ldb,
      &beta,
      C,
      &ldc,
      1,
      &batch_size);
#else // CAFFE2_USE_MKL
  // loop over matrices in the batch
  for (int i = 0; i < batch_size; ++i) {
    math::Gemm<float, CPUContext>(
        trans_A, trans_B, M, N, K, alpha, A[i], B[i], beta, C[i], context);
  }
#endif // CAFFE2_USE_MKL
}

template <>
C10_EXPORT void GemmStridedBatched<float, CPUContext>(
    const CBLAS_TRANSPOSE trans_A,
    const CBLAS_TRANSPOSE trans_B,
    const int batch_size,
    const int M,
    const int N,
    const int K,
    const float alpha,
    const float* A,
    const int A_stride,
    const float* B,
    const int B_stride,
    const float beta,
    float* C,
    const int C_stride,
    CPUContext* context,
    TensorProto::DataType /* math_type */) {
#ifdef CAFFE2_USE_MKL
  (void)context;
  const int lda = (trans_A == CblasNoTrans) ? K : M;
  const int ldb = (trans_B == CblasNoTrans) ? N : K;
  const int ldc = N;
  std::vector<const float*> A_array(batch_size);
  std::vector<const float*> B_array(batch_size);
  std::vector<float*> C_array(batch_size);
  for (int i = 0; i < batch_size; ++i) {
    A_array[i] = A + i * A_stride;
    B_array[i] = B + i * B_stride;
    C_array[i] = C + i * C_stride;
  }
  cblas_sgemm_batch(
      CblasRowMajor,
      &trans_A,
      &trans_B,
      &M,
      &N,
      &K,
      &alpha,
      A_array.data(),
      &lda,
      B_array.data(),
      &ldb,
      &beta,
      C_array.data(),
      &ldc,
      1,
      &batch_size);
#else // CAFFE2_USE_MKL
  // loop over matrices in the batch
  for (int i = 0; i < batch_size; ++i) {
    math::Gemm<float, CPUContext>(
        trans_A, trans_B, M, N, K, alpha, A, B, beta, C, context);
    A += A_stride;
    B += B_stride;
    C += C_stride;
  }
#endif
}

////////////////////////////////////////////////////////////////////////////////
// MKL VML alternatives.
// Depending on whether we are using MKL, we will delegate the Caffe math
// functions that are VML-related to either the VML call or the Eigen
// implementation. If you are setting the flags (such as AVX) right for your CPU
// architecture, usually Eigen will deliver a throughput as fast as the VML
// functions.
////////////////////////////////////////////////////////////////////////////////
#ifdef CAFFE2_USE_MKL

#define DELEGATE_SIMPLE_UNARY_FUNCTION(T, Funcname, OriginalFunc, ...) \
  template <>                                                          \
  C10_EXPORT void Funcname<T, CPUContext>(                             \
      const int N, const T* x, T* y, CPUContext*) {                    \
    OriginalFunc(N, x, y, ##__VA_ARGS__);                              \
  }
DELEGATE_SIMPLE_UNARY_FUNCTION(
    float,
    Exp,
    vmsExp,
    VML_HA | VML_FTZDAZ_OFF | VML_ERRMODE_IGNORE)
DELEGATE_SIMPLE_UNARY_FUNCTION(
    double,
    Exp,
    vmdExp,
    VML_HA | VML_FTZDAZ_OFF | VML_ERRMODE_IGNORE)
DELEGATE_SIMPLE_UNARY_FUNCTION(float, Log, vsLn)
DELEGATE_SIMPLE_UNARY_FUNCTION(double, Log, vdLn)
DELEGATE_SIMPLE_UNARY_FUNCTION(float, Cos, vsCos)
DELEGATE_SIMPLE_UNARY_FUNCTION(double, Cos, vdCos)
DELEGATE_SIMPLE_UNARY_FUNCTION(float, Acos, vsAcos)
DELEGATE_SIMPLE_UNARY_FUNCTION(double, Acos, vdAcos)
DELEGATE_SIMPLE_UNARY_FUNCTION(float, Sin, vsSin)
DELEGATE_SIMPLE_UNARY_FUNCTION(double, Sin, vdSin)
DELEGATE_SIMPLE_UNARY_FUNCTION(float, Asin, vsAsin)
DELEGATE_SIMPLE_UNARY_FUNCTION(double, Asin, vdAsin)
DELEGATE_SIMPLE_UNARY_FUNCTION(float, Tan, vsTan)
DELEGATE_SIMPLE_UNARY_FUNCTION(double, Tan, vdTan)
DELEGATE_SIMPLE_UNARY_FUNCTION(float, Atan, vsAtan)
DELEGATE_SIMPLE_UNARY_FUNCTION(double, Atan, vdAtan)
DELEGATE_SIMPLE_UNARY_FUNCTION(float, Sinh, vsSinh)
DELEGATE_SIMPLE_UNARY_FUNCTION(double, Sinh, vdSinh)
DELEGATE_SIMPLE_UNARY_FUNCTION(float, Cosh, vsCosh)
DELEGATE_SIMPLE_UNARY_FUNCTION(double, Cosh, vdCosh)
DELEGATE_SIMPLE_UNARY_FUNCTION(float, Tanh, vsTanh)
DELEGATE_SIMPLE_UNARY_FUNCTION(double, Tanh, vdTanh)
DELEGATE_SIMPLE_UNARY_FUNCTION(float, Abs, vsAbs)
DELEGATE_SIMPLE_UNARY_FUNCTION(double, Abs, vdAbs)
DELEGATE_SIMPLE_UNARY_FUNCTION(float, Sqr, vsSqr)
DELEGATE_SIMPLE_UNARY_FUNCTION(double, Sqr, vdSqr)
DELEGATE_SIMPLE_UNARY_FUNCTION(float, Sqrt, vsSqrt)
DELEGATE_SIMPLE_UNARY_FUNCTION(double, Sqrt, vdSqrt)
DELEGATE_SIMPLE_UNARY_FUNCTION(float, Rsqrt, vsInvSqrt)
DELEGATE_SIMPLE_UNARY_FUNCTION(double, Rsqrt, vdInvSqrt)
DELEGATE_SIMPLE_UNARY_FUNCTION(float, Cbrt, vsCbrt)
DELEGATE_SIMPLE_UNARY_FUNCTION(double, Cbrt, vdCbrt)
DELEGATE_SIMPLE_UNARY_FUNCTION(float, Inv, vsInv)
DELEGATE_SIMPLE_UNARY_FUNCTION(double, Inv, vdInv)
#undef DELEGATE_SIMPLE_UNARY_FUNCTION

#define DELEGATE_SINCOS_FUNCTION(T, OriginalFunc)           \
  template <>                                               \
  C10_EXPORT void SinCos<T, CPUContext>(                    \
      const int N, const T* a, T* ys, T* yc, CPUContext*) { \
    OriginalFunc(N, a, ys, yc);                             \
  }
DELEGATE_SINCOS_FUNCTION(float, vsSinCos)
DELEGATE_SINCOS_FUNCTION(double, vdSinCos)
#undef DELEGATE_SINCOS_FUNCTION

#define DELEGATE_POWX_FUNCTION(T, OriginalFunc)          \
  template <>                                            \
  C10_EXPORT void Powx<T, CPUContext>(                   \
      const int N, const T* a, T b, T* y, CPUContext*) { \
    OriginalFunc(N, a, b, y);                            \
  }
DELEGATE_POWX_FUNCTION(float, vsPowx)
DELEGATE_POWX_FUNCTION(double, vdPowx)
#undef DELEGATE_POWX_FUNCTION

#define DELEGATE_SIMPLE_BINARY_FUNCTION(T, Func, FuncImpl)      \
  template <>                                                   \
  C10_EXPORT void Func<T, CPUContext>(                          \
      const int N, const T* A, const T* B, T* C, CPUContext*) { \
    FuncImpl(N, A, B, C);                                       \
  }
DELEGATE_SIMPLE_BINARY_FUNCTION(float, Add, vsAdd)
DELEGATE_SIMPLE_BINARY_FUNCTION(double, Add, vdAdd)
DELEGATE_SIMPLE_BINARY_FUNCTION(float, Sub, vsSub)
DELEGATE_SIMPLE_BINARY_FUNCTION(double, Sub, vdSub)
DELEGATE_SIMPLE_BINARY_FUNCTION(float, Mul, vsMul)
DELEGATE_SIMPLE_BINARY_FUNCTION(double, Mul, vdMul)
DELEGATE_SIMPLE_BINARY_FUNCTION(float, Div, vsDiv)
DELEGATE_SIMPLE_BINARY_FUNCTION(double, Div, vdDiv)
#undef DELEGATE_SIMPLE_BINARY_FUNCTION

#else // CAFFE2_USE_MKL

#define DELEGATE_SIMPLE_UNARY_FUNCTION(T, Funcname, expr)               \
  template <>                                                           \
  C10_EXPORT void Funcname<T, CPUContext>(                              \
      const int N, const T* x, T* y, CPUContext*) {                     \
    EigenVectorMap<T>(y, N) = ConstEigenVectorArrayMap<T>(x, N).expr(); \
  }
DELEGATE_SIMPLE_UNARY_FUNCTION(float, Exp, exp)
DELEGATE_SIMPLE_UNARY_FUNCTION(double, Exp, exp)
DELEGATE_SIMPLE_UNARY_FUNCTION(float, Log, log)
DELEGATE_SIMPLE_UNARY_FUNCTION(double, Log, log)
DELEGATE_SIMPLE_UNARY_FUNCTION(float, Cos, cos)
DELEGATE_SIMPLE_UNARY_FUNCTION(double, Cos, cos)
DELEGATE_SIMPLE_UNARY_FUNCTION(float, Acos, acos)
DELEGATE_SIMPLE_UNARY_FUNCTION(double, Acos, acos)
DELEGATE_SIMPLE_UNARY_FUNCTION(float, Sin, sin)
DELEGATE_SIMPLE_UNARY_FUNCTION(double, Sin, sin)
DELEGATE_SIMPLE_UNARY_FUNCTION(float, Asin, asin)
DELEGATE_SIMPLE_UNARY_FUNCTION(double, Asin, asin)
DELEGATE_SIMPLE_UNARY_FUNCTION(float, Tan, tan)
DELEGATE_SIMPLE_UNARY_FUNCTION(double, Tan, tan)
DELEGATE_SIMPLE_UNARY_FUNCTION(float, Atan, atan)
DELEGATE_SIMPLE_UNARY_FUNCTION(double, Atan, atan)
DELEGATE_SIMPLE_UNARY_FUNCTION(float, Sqr, square)
DELEGATE_SIMPLE_UNARY_FUNCTION(double, Sqr, square)
DELEGATE_SIMPLE_UNARY_FUNCTION(float, Sqrt, sqrt)
DELEGATE_SIMPLE_UNARY_FUNCTION(double, Sqrt, sqrt)
DELEGATE_SIMPLE_UNARY_FUNCTION(float, Rsqrt, rsqrt)
DELEGATE_SIMPLE_UNARY_FUNCTION(double, Rsqrt, rsqrt)

#undef DELEGATE_SIMPLE_UNARY_FUNCTION

#define DELEGATE_SINCOS_FUNCTION(T)                                     \
  template <>                                                           \
  C10_EXPORT void SinCos<T, CPUContext>(                                \
      const int N, const T* x, T* ys, T* yc, CPUContext*) {             \
    EigenVectorMap<T>(ys, N) = ConstEigenVectorArrayMap<T>(x, N).sin(); \
    EigenVectorMap<T>(yc, N) = ConstEigenVectorArrayMap<T>(x, N).cos(); \
  }
DELEGATE_SINCOS_FUNCTION(float)
DELEGATE_SINCOS_FUNCTION(double)
#undef DELEGATE_SINCOS_FUNCTION

#define DELEGATE_TANH_FUNCTION(T)                                             \
  template <>                                                                 \
  C10_EXPORT void Tanh<T, CPUContext>(                                        \
      const int N, const T* X, T* Y, CPUContext*) {                           \
    EigenVectorMap<T>(Y, N) = T(1) -                                          \
        ((ConstEigenVectorArrayMap<T>(X, N) * T(2)).exp() + T(1)).inverse() * \
            T(2);                                                             \
  }
DELEGATE_TANH_FUNCTION(float)
DELEGATE_TANH_FUNCTION(double)
#undef DELEGATE_TANH_FUNCTION

#define DELEGATE_CBRT_FUNCTION(T)                                   \
  template <>                                                       \
  C10_EXPORT void Cbrt<T, CPUContext>(                              \
      const int N, const T* X, T* Y, CPUContext*) {                 \
    std::transform(X, X + N, Y, [](const T x) { return cbrt(x); }); \
  }
DELEGATE_CBRT_FUNCTION(float)
DELEGATE_CBRT_FUNCTION(double)
#undef DELEGATE_CBRT_FUNCTION

#define DELEGATE_POWX_FUNCTION(T)                                       \
  template <>                                                           \
  C10_EXPORT void Powx<T, CPUContext>(                                  \
      const int N, const T* a, const T b, T* y, CPUContext*) {          \
    EigenVectorMap<T>(y, N) = ConstEigenVectorArrayMap<T>(a, N).pow(b); \
  }
DELEGATE_POWX_FUNCTION(float)
#undef DELEGATE_POWX_FUNCTION

#define DELEGATE_SINH_FUNCTION(T)                                 \
  template <>                                                     \
  C10_EXPORT void Sinh<T, CPUContext>(                            \
      const int N, const T* X, T* Y, CPUContext*) {               \
    ConstEigenVectorArrayMap<T> X_arr(X, N);                      \
    EigenVectorMap<T>(Y, N) = (X_arr.exp() - (-X_arr).exp()) / 2; \
  }
DELEGATE_SINH_FUNCTION(float)
DELEGATE_SINH_FUNCTION(double)
#undef DELEGATE_SINH_FUNCTION

#define DELEGATE_COSH_FUNCTION(T)                                 \
  template <>                                                     \
  C10_EXPORT void Cosh<T, CPUContext>(                            \
      const int N, const T* X, T* Y, CPUContext*) {               \
    ConstEigenVectorArrayMap<T> X_arr(X, N);                      \
    EigenVectorMap<T>(Y, N) = (X_arr.exp() + (-X_arr).exp()) / 2; \
  }
DELEGATE_COSH_FUNCTION(float)
DELEGATE_COSH_FUNCTION(double)
#undef DELEGATE_COSH_FUNCTION

#define DELEGATE_INV_FUNCTION(T)                                           \
  template <>                                                              \
  C10_EXPORT void Inv<T, CPUContext>(                                      \
      const int N, const T* x, T* y, CPUContext*) {                        \
    EigenVectorMap<T>(y, N) = ConstEigenVectorArrayMap<T>(x, N).inverse(); \
  }
DELEGATE_INV_FUNCTION(float)
DELEGATE_INV_FUNCTION(double)
#undef DELEGATE_INV_FUNCTION

#endif // CAFFE2_USE_MKL

#define DELEGATE_NEG_FUNCTION(T)                             \
  template <>                                                \
  C10_EXPORT void Neg<T, CPUContext>(                        \
      const int N, const T* x, T* y, CPUContext*) {          \
    EigenVectorMap<T>(y, N) = -ConstEigenVectorMap<T>(x, N); \
  }
DELEGATE_NEG_FUNCTION(float)
DELEGATE_NEG_FUNCTION(double)
DELEGATE_NEG_FUNCTION(std::int32_t)
DELEGATE_NEG_FUNCTION(std::int64_t)
#undef DELEGATE_NEG_FUNCTION

#define DELEGATE_SIGN_FUNCTION(T)                                       \
  template <>                                                           \
  C10_EXPORT void Sign<T, CPUContext>(                                  \
      const int N, const T* x, T* y, CPUContext*) {                     \
    EigenVectorMap<T>(y, N) = ConstEigenVectorArrayMap<T>(x, N).sign(); \
  }
DELEGATE_SIGN_FUNCTION(float)
DELEGATE_SIGN_FUNCTION(double)
DELEGATE_SIGN_FUNCTION(std::int32_t)
DELEGATE_SIGN_FUNCTION(std::int64_t)
#undef DELEGATE_SIGN_FUNCTION

#define DELEGATE_ABS_FUNCTION(T)                                       \
  template <>                                                          \
  C10_EXPORT void Abs<T, CPUContext>(                                  \
      const int N, const T* x, T* y, CPUContext*) {                    \
    EigenVectorMap<T>(y, N) = ConstEigenVectorArrayMap<T>(x, N).abs(); \
  }
#ifndef CAFFE2_USE_MKL
DELEGATE_ABS_FUNCTION(float)
DELEGATE_ABS_FUNCTION(double)
#endif // CAFFE2_USE_MKL
DELEGATE_ABS_FUNCTION(std::int32_t)
DELEGATE_ABS_FUNCTION(std::int64_t)
#undef DELEGATE_ABS_FUNCTION

#define DELEGATE_CUBE_FUNCTION(T)                                       \
  template <>                                                           \
  C10_EXPORT void Cube<T, CPUContext>(                                  \
      const int N, const T* X, T* Y, CPUContext*) {                     \
    EigenVectorMap<T>(Y, N) = ConstEigenVectorArrayMap<T>(X, N).cube(); \
  }
DELEGATE_CUBE_FUNCTION(float)
DELEGATE_CUBE_FUNCTION(double)
DELEGATE_CUBE_FUNCTION(std::int32_t)
DELEGATE_CUBE_FUNCTION(std::int64_t)
#undef DELEGATE_CUBE_FUNCTION

#define EIGEN_SIMPLE_BINARY_FUNCTION(T, Func, expr)             \
  template <>                                                   \
  C10_EXPORT void Func<T, CPUContext>(                          \
      const int N, const T* A, const T* B, T* C, CPUContext*) { \
    EigenVectorMap<T>(C, N) = ConstEigenVectorArrayMap<T>(A, N) \
        expr ConstEigenVectorArrayMap<T>(B, N);                 \
  }

#ifdef CAFFE2_USE_MKL

#define DEFINE_SIMPLE_BINARY_FUNCTION(Func, expr)        \
  EIGEN_SIMPLE_BINARY_FUNCTION(std::int32_t, Func, expr) \
  EIGEN_SIMPLE_BINARY_FUNCTION(std::int64_t, Func, expr)

#else

#define DEFINE_SIMPLE_BINARY_FUNCTION(Func, expr)        \
  EIGEN_SIMPLE_BINARY_FUNCTION(float, Func, expr)        \
  EIGEN_SIMPLE_BINARY_FUNCTION(double, Func, expr)       \
  EIGEN_SIMPLE_BINARY_FUNCTION(std::int32_t, Func, expr) \
  EIGEN_SIMPLE_BINARY_FUNCTION(std::int64_t, Func, expr)

#endif

DEFINE_SIMPLE_BINARY_FUNCTION(Add, +)
DEFINE_SIMPLE_BINARY_FUNCTION(Sub, -)
DEFINE_SIMPLE_BINARY_FUNCTION(Mul, *)
DEFINE_SIMPLE_BINARY_FUNCTION(Div, /)

#undef DEFINE_SIMPLE_BINARY_FUNCTION
#undef EIGEN_SIMPLE_BINARY_FUNCTION

////////////////////////////////////////////////////////////////////////////////
// Common math functions being used in Caffe that do not have a BLAS or MKL
// equivalent. For all these functions, we will simply implement them either via
// Eigen or via custom code.
////////////////////////////////////////////////////////////////////////////////

#define CAFFE2_SPECIALIZED_SET(T)                         \
  template <>                                             \
  C10_EXPORT void Set<T, CPUContext>(                     \
      const size_t N, const T alpha, T* Y, CPUContext*) { \
    if (N == 0) {                                         \
      return;                                             \
    }                                                     \
    if (alpha == (T)0) {                                  \
      if (Y != nullptr) {                                 \
        std::memset(Y, 0, N * sizeof(T));                 \
      }                                                   \
    } else {                                              \
      EigenVectorMap<T>(Y, N).setConstant(alpha);         \
    }                                                     \
  }

CAFFE2_SPECIALIZED_SET(float);
CAFFE2_SPECIALIZED_SET(double);
CAFFE2_SPECIALIZED_SET(int8_t);
CAFFE2_SPECIALIZED_SET(int16_t);
CAFFE2_SPECIALIZED_SET(int);
CAFFE2_SPECIALIZED_SET(int64_t);
CAFFE2_SPECIALIZED_SET(bool);
CAFFE2_SPECIALIZED_SET(char);
CAFFE2_SPECIALIZED_SET(uint8_t);
CAFFE2_SPECIALIZED_SET(uint16_t);
#undef CAFFE2_SPECIALIZED_SET

#define CAFFE2_SPECIALIZED_REDUCEMIN(T)                \
  template <>                                          \
  C10_EXPORT void ReduceMin<T, CPUContext>(            \
      const int N,                                     \
      const T* x,                                      \
      T* y,                                            \
      Tensor* /*scratch_ptr*/,                         \
      CPUContext* /*context*/) {                       \
    *y = ConstEigenVectorArrayMap<T>(x, N).minCoeff(); \
  }
CAFFE2_SPECIALIZED_REDUCEMIN(float)
#undef CAFFE2_SPECIALIZED_REDUCEMIN

#define CAFFE2_SPECIALIZED_REDUCEMAX(T)                \
  template <>                                          \
  C10_EXPORT void ReduceMax<T, CPUContext>(            \
      const int N,                                     \
      const T* x,                                      \
      T* y,                                            \
      Tensor* /*scratch_ptr*/,                         \
      CPUContext* /*context*/) {                       \
    *y = ConstEigenVectorArrayMap<T>(x, N).maxCoeff(); \
  }
CAFFE2_SPECIALIZED_REDUCEMAX(float)
CAFFE2_SPECIALIZED_REDUCEMAX(int32_t)
CAFFE2_SPECIALIZED_REDUCEMAX(int64_t)

#undef CAFFE2_SPECIALIZED_REDUCEMAX

namespace {

template <typename T>
struct MinFunctor {
  inline T operator()(const T a, const T b) const {
    return std::min(a, b);
  }
};

template <typename T>
struct MaxFunctor {
  inline T operator()(const T a, const T b) const {
    return std::max(a, b);
  }
};

template <typename T>
struct L1NormFunctor {
  inline T operator()(const T a, const T b) const {
    return a + std::abs(b);
  }
};

template <typename T>
struct SquaredL2NormFunctor {
  inline T operator()(const T a, const T b) const {
    return a + b * b;
  }
};

#define DELEGATE_ROWWISE_REDUCE_FUNCTION(Func, EigenOp)                    \
  template <typename T>                                                    \
  C10_EXPORT void Rowwise##Func(                                           \
      const int rows, const int cols, const T alpha, const T* X, T* Y) {   \
    EigenVectorMap<T>(Y, rows) =                                           \
        ConstEigenMatrixMap<T>(X, cols, rows).colwise().EigenOp() * alpha; \
  }
DELEGATE_ROWWISE_REDUCE_FUNCTION(ReduceMin, minCoeff)
DELEGATE_ROWWISE_REDUCE_FUNCTION(ReduceMax, maxCoeff)
DELEGATE_ROWWISE_REDUCE_FUNCTION(ReduceSum, sum)
DELEGATE_ROWWISE_REDUCE_FUNCTION(ReduceMean, mean)
DELEGATE_ROWWISE_REDUCE_FUNCTION(ReduceL1, template lpNorm<1>);
DELEGATE_ROWWISE_REDUCE_FUNCTION(ReduceL2, norm)
#undef DELEGATE_ROWWISE_REDUCE_FUNCTION

#define DELEGATE_COLWISE_REDUCE_FUNCTION(Func, EigenOp)                    \
  template <typename T>                                                    \
  C10_EXPORT void Colwise##Func(                                           \
      const int rows, const int cols, const T alpha, const T* X, T* Y) {   \
    EigenVectorMap<T>(Y, cols) =                                           \
        ConstEigenMatrixMap<T>(X, cols, rows).rowwise().EigenOp() * alpha; \
  }
DELEGATE_COLWISE_REDUCE_FUNCTION(ReduceMin, minCoeff)
DELEGATE_COLWISE_REDUCE_FUNCTION(ReduceMax, maxCoeff)
DELEGATE_COLWISE_REDUCE_FUNCTION(ReduceSum, sum)
DELEGATE_COLWISE_REDUCE_FUNCTION(ReduceMean, mean)
DELEGATE_COLWISE_REDUCE_FUNCTION(ReduceL1, template lpNorm<1>);
DELEGATE_COLWISE_REDUCE_FUNCTION(ReduceL2, norm)
#undef DELEGATE_COLWISE_REDUCE_FUNCTION

template <typename T>
C10_EXPORT void BothEndsReduceMin(
    const int pre,
    const int mid,
    const int nxt,
    const T alpha,
    const T* X,
    T* Y) {
  EigenVectorArrayMap<T> Y_arr(Y, mid);
  Y_arr = ConstEigenArrayMap<T>(X, nxt, mid).colwise().minCoeff();
  const T* X_ptr = X + mid * nxt;
  // It seems there is some bug in eigen array::min so it cannot be implemented
  // as ReduceSum below.
  for (int i = 1; i < pre; ++i) {
    for (int j = 0; j < mid; ++j) {
      Y[j] = std::min(Y[j], ConstEigenVectorArrayMap<T>(X_ptr, nxt).minCoeff());
      X_ptr += nxt;
    }
  }
  if (alpha != T(1)) {
    Y_arr *= alpha;
  }
}

template <typename T>
C10_EXPORT void BothEndsReduceMax(
    const int pre,
    const int mid,
    const int nxt,
    const T alpha,
    const T* X,
    T* Y) {
  EigenVectorArrayMap<T> Y_arr(Y, mid);
  Y_arr = ConstEigenArrayMap<T>(X, nxt, mid).colwise().maxCoeff();
  const T* X_ptr = X + mid * nxt;
  for (int i = 1; i < pre; ++i) {
    for (int j = 0; j < mid; ++j) {
      Y[j] = std::max(Y[j], ConstEigenVectorArrayMap<T>(X_ptr, nxt).maxCoeff());
      X_ptr += nxt;
    }
  }
  if (alpha != T(1)) {
    Y_arr *= alpha;
  }
}

template <typename T>
C10_EXPORT void BothEndsReduceSum(
    const int pre,
    const int mid,
    const int nxt,
    const T alpha,
    const T* X,
    T* Y) {
  EigenVectorArrayMap<T> Y_arr(Y, mid);
  Y_arr = ConstEigenArrayMap<T>(X, nxt, mid).colwise().sum();
  const int stride = mid * nxt;
  const T* X_ptr = X + stride;
  for (int i = 1; i < pre; ++i) {
    Y_arr += ConstEigenArrayMap<T>(X_ptr, nxt, mid).colwise().sum();
    X_ptr += stride;
  }
  if (alpha != T(1)) {
    Y_arr *= alpha;
  }
}

template <typename T>
C10_EXPORT void BothEndsReduceMean(
    const int pre,
    const int mid,
    const int nxt,
    const T alpha,
    const T* X,
    T* Y) {
  EigenVectorArrayMap<T> Y_arr(Y, mid);
  Y_arr = ConstEigenArrayMap<T>(X, nxt, mid).colwise().mean();
  const int stride = mid * nxt;
  const T* X_ptr = X + stride;
  for (int i = 1; i < pre; ++i) {
    Y_arr += ConstEigenArrayMap<T>(X_ptr, nxt, mid).colwise().mean();
    X_ptr += stride;
  }
  if (alpha / static_cast<T>(pre) != 1) {
    Y_arr *= alpha / static_cast<T>(pre);
  }
}

template <typename T>
C10_EXPORT void BothEndsReduceL1(
    const int pre,
    const int mid,
    const int nxt,
    const T alpha,
    const T* X,
    T* Y) {
  EigenVectorArrayMap<T> Y_arr(Y, mid);
  Y_arr = ConstEigenMatrixMap<T>(X, nxt, mid)
              .colwise()
              .template lpNorm<1>()
              .array();
  const int stride = mid * nxt;
  const T* X_ptr = X + stride;
  for (int i = 1; i < pre; ++i) {
    Y_arr += ConstEigenMatrixMap<T>(X_ptr, nxt, mid)
                 .colwise()
                 .template lpNorm<1>()
                 .array();
    X_ptr += stride;
  }
  if (alpha != T(1)) {
    Y_arr *= alpha;
  }
}

template <typename T>
C10_EXPORT void BothEndsReduceL2(
    const int pre,
    const int mid,
    const int nxt,
    const T alpha,
    const T* X,
    T* Y) {
  EigenVectorArrayMap<T> Y_arr(Y, mid);
  Y_arr = ConstEigenMatrixMap<T>(X, nxt, mid).colwise().squaredNorm().array();
  const int stride = mid * nxt;
  const T* X_ptr = X + stride;
  for (int i = 1; i < pre; ++i) {
    Y_arr +=
        ConstEigenMatrixMap<T>(X_ptr, nxt, mid).colwise().squaredNorm().array();
    X_ptr += stride;
  }
  Y_arr = Y_arr.sqrt() * alpha;
}

template <typename T, class Reducer>
C10_EXPORT void ReduceTensor(
    const int ndim,
    const int* X_dims,
    const int* Y_dims,
    const Reducer& reducer,
    const T init,
    const T alpha,
    const T* X,
    T* Y,
    CPUContext* context) {
  const int X_size =
      std::accumulate(X_dims, X_dims + ndim, 1, std::multiplies<int>());
  const int Y_size =
      std::accumulate(Y_dims, Y_dims + ndim, 1, std::multiplies<int>());
  Set<T, CPUContext>(Y_size, init, Y, context);
  std::vector<int> index(ndim, 0);
  for (int X_index = 0; X_index < X_size; ++X_index) {
    const int Y_index = utils::GetIndexFromDims(ndim, Y_dims, index.data());
    Y[Y_index] = reducer(Y[Y_index], X[X_index]);
    utils::IncreaseIndexInDims(ndim, X_dims, index.data());
  }
  Scale<T, T, CPUContext>(Y_size, alpha, Y, Y, context);
}

} // namespace

#define DELEGATE_REDUCE_FUNCTION(T, Func, reducer, init, is_norm)              \
  template <>                                                                  \
  C10_EXPORT void Func<T, CPUContext>(                                         \
      const int num_dims,                                                      \
      const int* dims,                                                         \
      const int num_axes,                                                      \
      const int* axes,                                                         \
      const T alpha,                                                           \
      const T* X,                                                              \
      T* Y,                                                                    \
      CPUContext* context) {                                                   \
    CAFFE_ENFORCE_LE(num_axes, num_dims);                                      \
    std::vector<int> Y_dims_vector(dims, dims + num_dims);                     \
    for (int i = 0; i < num_axes; ++i) {                                       \
      Y_dims_vector[axes[i]] = 1;                                              \
    }                                                                          \
    const int* X_dims = dims;                                                  \
    const int* Y_dims = Y_dims_vector.data();                                  \
    const int X_size =                                                         \
        std::accumulate(X_dims, X_dims + num_dims, 1, std::multiplies<int>()); \
    const int Y_size =                                                         \
        std::accumulate(Y_dims, Y_dims + num_dims, 1, std::multiplies<int>()); \
    if (X_size == 0) {                                                         \
      Set<T, CPUContext>(Y_size, alpha * init, Y, context);                    \
      return;                                                                  \
    }                                                                          \
    if (alpha == T(0)) {                                                       \
      Set<T, CPUContext>(Y_size, 0, Y, context);                               \
      return;                                                                  \
    }                                                                          \
    if (std::equal(X_dims, X_dims + num_dims, Y_dims)) {                       \
      if (is_norm) {                                                           \
        Abs<T, CPUContext>(X_size, X, Y, context);                             \
        Scale<T, T, CPUContext>(Y_size, alpha, Y, Y, context);                 \
      } else {                                                                 \
        Scale<T, T, CPUContext>(Y_size, alpha, X, Y, context);                 \
      }                                                                        \
      return;                                                                  \
    }                                                                          \
    int rows;                                                                  \
    int cols;                                                                  \
    if (utils::IsRowwiseReduce(num_dims, X_dims, Y_dims, &rows, &cols)) {      \
      Rowwise##Func<T>(rows, cols, alpha, X, Y);                               \
      return;                                                                  \
    }                                                                          \
    if (utils::IsColwiseReduce(num_dims, X_dims, Y_dims, &rows, &cols)) {      \
      Colwise##Func<T>(rows, cols, alpha, X, Y);                               \
      return;                                                                  \
    }                                                                          \
    int pre;                                                                   \
    int mid;                                                                   \
    int nxt;                                                                   \
    if (utils::IsBothEndsReduce(num_dims, X_dims, Y_dims, &pre, &mid, &nxt)) { \
      BothEnds##Func<T>(pre, mid, nxt, alpha, X, Y);                           \
      return;                                                                  \
    }                                                                          \
    ReduceTensor(                                                              \
        num_dims, X_dims, Y_dims, reducer, init, alpha, X, Y, context);        \
  }

DELEGATE_REDUCE_FUNCTION(
    float,
    ReduceMin,
    MinFunctor<float>(),
    std::numeric_limits<float>::max(),
    false)
DELEGATE_REDUCE_FUNCTION(
    double,
    ReduceMin,
    MinFunctor<double>(),
    std::numeric_limits<double>::max(),
    false)
DELEGATE_REDUCE_FUNCTION(
    std::int32_t,
    ReduceMin,
    MinFunctor<std::int32_t>(),
    std::numeric_limits<std::int32_t>::max(),
    false)
DELEGATE_REDUCE_FUNCTION(
    std::int64_t,
    ReduceMin,
    MinFunctor<std::int64_t>(),
    std::numeric_limits<std::int64_t>::max(),
    false)

DELEGATE_REDUCE_FUNCTION(
    float,
    ReduceMax,
    MaxFunctor<float>(),
    std::numeric_limits<float>::lowest(),
    false)
DELEGATE_REDUCE_FUNCTION(
    double,
    ReduceMax,
    MaxFunctor<double>(),
    std::numeric_limits<double>::lowest(),
    false)
DELEGATE_REDUCE_FUNCTION(
    std::int32_t,
    ReduceMax,
    MaxFunctor<std::int32_t>(),
    std::numeric_limits<std::int32_t>::lowest(),
    false)
DELEGATE_REDUCE_FUNCTION(
    std::int64_t,
    ReduceMax,
    MaxFunctor<std::int64_t>(),
    std::numeric_limits<std::int64_t>::lowest(),
    false)

DELEGATE_REDUCE_FUNCTION(float, ReduceSum, std::plus<float>(), 0.0f, false)
DELEGATE_REDUCE_FUNCTION(double, ReduceSum, std::plus<double>(), 0.0, false)
DELEGATE_REDUCE_FUNCTION(
    std::int32_t,
    ReduceSum,
    std::plus<std::int32_t>(),
    0,
    false)
DELEGATE_REDUCE_FUNCTION(
    std::int64_t,
    ReduceSum,
    std::plus<std::int64_t>(),
    std::int64_t(0),
    false)

DELEGATE_REDUCE_FUNCTION(float, ReduceL1, L1NormFunctor<float>(), 0.0f, true)
DELEGATE_REDUCE_FUNCTION(double, ReduceL1, L1NormFunctor<double>(), 0.0, true)
DELEGATE_REDUCE_FUNCTION(
    std::int32_t,
    ReduceL1,
    L1NormFunctor<std::int32_t>(),
    0,
    true)
DELEGATE_REDUCE_FUNCTION(
    std::int64_t,
    ReduceL1,
    L1NormFunctor<std::int64_t>(),
    std::int64_t(0),
    true)

#undef DELEGATE_REDUCE_FUNCTION

#define CAFFE2_SPECIALIZED_REDUCE_MEAN(T)                                      \
  template <>                                                                  \
  C10_EXPORT void ReduceMean<T, CPUContext>(                                   \
      const int num_dims,                                                      \
      const int* dims,                                                         \
      const int num_axes,                                                      \
      const int* axes,                                                         \
      const T alpha,                                                           \
      const T* X,                                                              \
      T* Y,                                                                    \
      CPUContext* context) {                                                   \
    CAFFE_ENFORCE_LE(num_axes, num_dims);                                      \
    std::vector<int> Y_dims_vector(dims, dims + num_dims);                     \
    for (int i = 0; i < num_axes; ++i) {                                       \
      Y_dims_vector[axes[i]] = 1;                                              \
    }                                                                          \
    const int* X_dims = dims;                                                  \
    const int* Y_dims = Y_dims_vector.data();                                  \
    const int X_size =                                                         \
        std::accumulate(X_dims, X_dims + num_dims, 1, std::multiplies<int>()); \
    const int Y_size =                                                         \
        std::accumulate(Y_dims, Y_dims + num_dims, 1, std::multiplies<int>()); \
    if (X_size == 0) {                                                         \
      Set<T, CPUContext>(Y_size, 0, Y, context);                               \
      return;                                                                  \
    }                                                                          \
    if (alpha == T(0)) {                                                       \
      Set<T, CPUContext>(Y_size, 0, Y, context);                               \
      return;                                                                  \
    }                                                                          \
    if (std::equal(X_dims, X_dims + num_dims, Y_dims)) {                       \
      Scale<T, T, CPUContext>(X_size, alpha, X, Y, context);                   \
      return;                                                                  \
    }                                                                          \
    int rows;                                                                  \
    int cols;                                                                  \
    if (utils::IsRowwiseReduce(num_dims, X_dims, Y_dims, &rows, &cols)) {      \
      RowwiseReduceMean<T>(rows, cols, alpha, X, Y);                           \
      return;                                                                  \
    }                                                                          \
    if (utils::IsColwiseReduce(num_dims, X_dims, Y_dims, &rows, &cols)) {      \
      ColwiseReduceMean<T>(rows, cols, alpha, X, Y);                           \
      return;                                                                  \
    }                                                                          \
    int pre;                                                                   \
    int mid;                                                                   \
    int nxt;                                                                   \
    if (utils::IsBothEndsReduce(num_dims, X_dims, Y_dims, &pre, &mid, &nxt)) { \
      BothEndsReduceMean<T>(pre, mid, nxt, alpha, X, Y);                       \
      return;                                                                  \
    }                                                                          \
    const int scale = X_size / Y_size;                                         \
    ReduceTensor(                                                              \
        num_dims,                                                              \
        X_dims,                                                                \
        Y_dims,                                                                \
        std::plus<T>(),                                                        \
        T(0),                                                                  \
        alpha / static_cast<T>(scale),                                         \
        X,                                                                     \
        Y,                                                                     \
        context);                                                              \
  }
CAFFE2_SPECIALIZED_REDUCE_MEAN(float)
CAFFE2_SPECIALIZED_REDUCE_MEAN(double)
#undef CAFFE2_SPECIALIZED_REDUCE_MEAN

#define CAFFE2_SPECIALIZED_REDUCE_L2(T)                                        \
  template <>                                                                  \
  C10_EXPORT void ReduceL2<T, CPUContext>(                                     \
      const int num_dims,                                                      \
      const int* dims,                                                         \
      const int num_axes,                                                      \
      const int* axes,                                                         \
      const T alpha,                                                           \
      const T* X,                                                              \
      T* Y,                                                                    \
      CPUContext* context) {                                                   \
    CAFFE_ENFORCE_LE(num_axes, num_dims);                                      \
    std::vector<int> Y_dims_vector(dims, dims + num_dims);                     \
    for (int i = 0; i < num_axes; ++i) {                                       \
      Y_dims_vector[axes[i]] = 1;                                              \
    }                                                                          \
    const int* X_dims = dims;                                                  \
    const int* Y_dims = Y_dims_vector.data();                                  \
    const int X_size =                                                         \
        std::accumulate(X_dims, X_dims + num_dims, 1, std::multiplies<int>()); \
    const int Y_size =                                                         \
        std::accumulate(Y_dims, Y_dims + num_dims, 1, std::multiplies<int>()); \
    if (X_size == 0) {                                                         \
      Set<T, CPUContext>(Y_size, 0, Y, context);                               \
      return;                                                                  \
    }                                                                          \
    if (alpha == T(0)) {                                                       \
      Set<T, CPUContext>(Y_size, 0, Y, context);                               \
      return;                                                                  \
    }                                                                          \
    if (std::equal(X_dims, X_dims + num_dims, Y_dims)) {                       \
      Abs<T, CPUContext>(X_size, X, Y, context);                               \
      Scale<T, T, CPUContext>(Y_size, alpha, Y, Y, context);                   \
      return;                                                                  \
    }                                                                          \
    int rows;                                                                  \
    int cols;                                                                  \
    if (utils::IsRowwiseReduce(num_dims, X_dims, Y_dims, &rows, &cols)) {      \
      RowwiseReduceL2<T>(rows, cols, alpha, X, Y);                             \
      return;                                                                  \
    }                                                                          \
    if (utils::IsColwiseReduce(num_dims, X_dims, Y_dims, &rows, &cols)) {      \
      ColwiseReduceL2<T>(rows, cols, alpha, X, Y);                             \
      return;                                                                  \
    }                                                                          \
    int pre;                                                                   \
    int mid;                                                                   \
    int nxt;                                                                   \
    if (utils::IsBothEndsReduce(num_dims, X_dims, Y_dims, &pre, &mid, &nxt)) { \
      BothEndsReduceL2<T>(pre, mid, nxt, alpha, X, Y);                         \
      return;                                                                  \
    }                                                                          \
    ReduceTensor(                                                              \
        num_dims,                                                              \
        X_dims,                                                                \
        Y_dims,                                                                \
        SquaredL2NormFunctor<T>(),                                             \
        T(0),                                                                  \
        T(1),                                                                  \
        X,                                                                     \
        Y,                                                                     \
        context);                                                              \
    Sqrt<T, CPUContext>(Y_size, Y, Y, context);                                \
    Scale<T, T, CPUContext>(Y_size, alpha, Y, Y, context);                     \
  }
CAFFE2_SPECIALIZED_REDUCE_L2(float)
CAFFE2_SPECIALIZED_REDUCE_L2(double)
#undef CAFFE2_SPECIALIZED_REDUCE_L2

namespace {

template <typename T>
C10_EXPORT void BroadcastImpl(
    const int X_ndim,
    const int* X_dims,
    const int Y_ndim,
    const int* Y_dims,
    const T alpha,
    const T* X,
    T* Y,
    CPUContext* context) {
  CAFFE_ENFORCE_LE(X_ndim, Y_ndim);
  std::vector<int> X_dims_vector(Y_ndim);
  const int d = Y_ndim - X_ndim;
  std::fill(X_dims_vector.begin(), X_dims_vector.begin() + d, 1);
  for (int i = d; i < Y_ndim; ++i) {
    CAFFE_ENFORCE(X_dims[i - d] == 1 || X_dims[i - d] == Y_dims[i]);
    X_dims_vector[i] = X_dims[i - d];
  }
  X_dims = X_dims_vector.data();
  const int Y_size =
      std::accumulate(Y_dims, Y_dims + Y_ndim, 1, std::multiplies<int>());
  std::vector<int> index(Y_ndim, 0);
  for (int Y_index = 0; Y_index < Y_size; ++Y_index) {
    const int X_index = utils::GetIndexFromDims(Y_ndim, X_dims, index.data());
    Y[Y_index] = X[X_index];
    utils::IncreaseIndexInDims(Y_ndim, Y_dims, index.data());
  }
  Scale<T, T, CPUContext>(Y_size, alpha, Y, Y, context);
}

} // namespace

#define CAFFE2_SPECIALIZED_BROADCAST(T)                                     \
  template <>                                                               \
  C10_EXPORT void Broadcast<T, CPUContext>(                                 \
      const int X_ndim,                                                     \
      const int* X_dims,                                                    \
      const int Y_ndim,                                                     \
      const int* Y_dims,                                                    \
      const T alpha,                                                        \
      const T* X,                                                           \
      T* Y,                                                                 \
      CPUContext* context) {                                                \
    BroadcastImpl<T>(X_ndim, X_dims, Y_ndim, Y_dims, alpha, X, Y, context); \
  }
CAFFE2_SPECIALIZED_BROADCAST(std::int32_t)
CAFFE2_SPECIALIZED_BROADCAST(std::int64_t)
CAFFE2_SPECIALIZED_BROADCAST(float)
CAFFE2_SPECIALIZED_BROADCAST(double)
#undef CAFFE2_SPECIALIZED_BROADCAST

namespace {

template <typename T>
C10_EXPORT void RowwiseMoments(
    const int rows,
    const int cols,
    const T* X,
    T* mean,
    T* variance) {
  ConstEigenArrayMap<T> X_arr(X, cols, rows);
  EigenVectorArrayMap<T> mean_arr(mean, rows);
  EigenVectorArrayMap<T> var_arr(variance, rows);
  mean_arr = X_arr.colwise().mean();
  var_arr = X_arr.square().colwise().mean() - mean_arr.square().transpose();
}

template <typename T>
C10_EXPORT void ColwiseMoments(
    const int rows,
    const int cols,
    const T* X,
    T* mean,
    T* variance) {
  std::memset(mean, 0, sizeof(T) * cols);
  std::memset(variance, 0, sizeof(T) * cols);
  ConstEigenArrayMap<T> X_arr(X, cols, rows);
  EigenVectorArrayMap<T> mean_arr(mean, cols);
  EigenVectorArrayMap<T> var_arr(variance, cols);
  // Eigen rowwise reduction is about 10 times slower than this for-loop.
  for (int i = 0; i < rows; ++i) {
    mean_arr += X_arr.col(i);
    var_arr += X_arr.col(i).square();
  }
  const T scale = T(1) / static_cast<T>(rows);
  mean_arr *= scale;
  var_arr = var_arr * scale - mean_arr.square();
}

template <typename T>
C10_EXPORT void BothEndsMoments(
    const int pre,
    const int mid,
    const int nxt,
    const T* X,
    T* mean,
    T* variance) {
  std::memset(mean, 0, sizeof(T) * mid);
  std::memset(variance, 0, sizeof(T) * mid);
  EigenVectorArrayMap<T> mean_arr(mean, mid);
  EigenVectorArrayMap<T> var_arr(variance, mid);
  ConstEigenArrayMap<T> X_arr(X, nxt, pre * mid);
  for (int i = 0; i < pre; ++i) {
    for (int j = 0; j < mid; ++j) {
      const int c = i * mid + j;
      mean_arr(j) += X_arr.col(c).sum();
      var_arr(j) += X_arr.col(c).square().sum();
    }
  }
  const T scale = T(1) / static_cast<T>(pre * nxt);
  mean_arr *= scale;
  var_arr = var_arr * scale - mean_arr.square();
}

template <typename T>
C10_EXPORT void MomentsImpl(
    const int num_dims,
    const int* dims,
    const int num_axes,
    const int* axes,
    const T* X,
    T* mean,
    T* variance,
    CPUContext* context) {
  std::vector<int> Y_dims_vector(dims, dims + num_dims);
  for (int i = 0; i < num_axes; ++i) {
    Y_dims_vector[axes[i]] = 1;
  }
  const int* X_dims = dims;
  const int* Y_dims = Y_dims_vector.data();
  const int X_size =
      std::accumulate(X_dims, X_dims + num_dims, 1, std::multiplies<int>());
  const int Y_size =
      std::accumulate(Y_dims, Y_dims + num_dims, 1, std::multiplies<int>());
  if (X_size == 0) {
    std::memset(mean, 0, sizeof(T) * Y_size);
    std::memset(variance, 0, sizeof(T) * Y_size);
    return;
  }
  if (std::equal(X_dims, X_dims + num_dims, Y_dims)) {
    std::memcpy(mean, X, sizeof(T) * Y_size);
    std::memset(variance, 0, sizeof(T) * Y_size);
    return;
  }
  int rows;
  int cols;
  if (utils::IsRowwiseReduce(num_dims, X_dims, Y_dims, &rows, &cols)) {
    RowwiseMoments<T>(rows, cols, X, mean, variance);
    return;
  }
  if (utils::IsColwiseReduce(num_dims, X_dims, Y_dims, &rows, &cols)) {
    ColwiseMoments<T>(rows, cols, X, mean, variance);
    return;
  }
  int pre;
  int mid;
  int nxt;
  if (utils::IsBothEndsReduce(num_dims, X_dims, Y_dims, &pre, &mid, &nxt)) {
    BothEndsMoments<T>(pre, mid, nxt, X, mean, variance);
    return;
  }
  Set<T, CPUContext>(Y_size, T(0), mean, context);
  Set<T, CPUContext>(Y_size, T(0), variance, context);
  std::vector<int> index(num_dims, 0);
  for (int X_index = 0; X_index < X_size; ++X_index) {
    const int Y_index = utils::GetIndexFromDims(num_dims, Y_dims, index.data());
    mean[Y_index] += X[X_index];
    variance[Y_index] += X[X_index] * X[X_index];
    utils::IncreaseIndexInDims(num_dims, dims, index.data());
  }
  const T scale = static_cast<T>(Y_size) / static_cast<T>(X_size);
  EigenVectorArrayMap<T> mean_arr(mean, Y_size);
  EigenVectorArrayMap<T> var_arr(variance, Y_size);
  mean_arr *= scale;
  var_arr =
      var_arr * scale - ConstEigenVectorArrayMap<T>(mean, Y_size).square();
}

} // namespace

#define CAFFE2_SPECIALIZED_MOMENTS(T)                                \
  template <>                                                        \
  C10_EXPORT void Moments<T, CPUContext>(                            \
      const int num_dims,                                            \
      const int* dims,                                               \
      const int num_axes,                                            \
      const int* axes,                                               \
      const T* X,                                                    \
      T* mean,                                                       \
      T* variance,                                                   \
      CPUContext* context) {                                         \
    MomentsImpl<T>(                                                  \
        num_dims, dims, num_axes, axes, X, mean, variance, context); \
  }
CAFFE2_SPECIALIZED_MOMENTS(float)
#undef CAFFE2_SPECIALIZED_MOMENTS

#define CAFFE2_SPECIALIZED_INV_STD(T)                            \
  template <>                                                    \
  void InvStd<T, CPUContext>(                                    \
      const int N,                                               \
      const T epsilon,                                           \
      const T* var,                                              \
      T* inv_std,                                                \
      CPUContext* context) {                                     \
    EigenVectorArrayMap<T>(inv_std, N) =                         \
        (ConstEigenVectorArrayMap<T>(var, N) + epsilon).rsqrt(); \
  }
CAFFE2_SPECIALIZED_INV_STD(float)
#undef CAFFE2_SPECIALIZED_INV_STD

#define CAFFE2_SPECIALIZED_ROWWISEMAX(T)                         \
  template <>                                                    \
  C10_EXPORT void RowwiseMax<T, CPUContext>(                     \
      const int N, const int D, const T* x, T* y, CPUContext*) { \
    EigenVectorMap<T>(y, N) =                                    \
        ConstEigenMatrixMap<T>(x, D, N).colwise().maxCoeff();    \
  }
CAFFE2_SPECIALIZED_ROWWISEMAX(float)
#undef CAFFE2_SPECIALIZED_ROWWISEMAX

#define CAFFE2_SPECIALIZED_COLWISEMAX(T)                         \
  template <>                                                    \
  C10_EXPORT void ColwiseMax<T, CPUContext>(                     \
      const int N, const int D, const T* x, T* y, CPUContext*) { \
    EigenVectorMap<T>(y, D) =                                    \
        ConstEigenMatrixMap<T>(x, D, N).rowwise().maxCoeff();    \
  }
CAFFE2_SPECIALIZED_COLWISEMAX(float)
#undef CAFFE2_SPECIALIZED_COLWISEMAX

#define CAFFE2_SPECIALIZED_ELEMWISEMAX(T)                                   \
  template <>                                                               \
  C10_EXPORT void ElemwiseMax<T, CPUContext>(                               \
      const int N, const T* x, const T* y, T* z, CPUContext* /*context*/) { \
    std::transform(x, x + N, y, z, [](const T& x_i, const T& y_i) {         \
      return std::max(x_i, y_i);                                            \
    });                                                                     \
  }
CAFFE2_SPECIALIZED_ELEMWISEMAX(float)
#undef CAFFE2_SPECIALIZED_ELEMWISEMAX

#define CAFFE2_SPECIALIZED_MAXIMUM(T)                                          \
  template <>                                                                  \
  C10_EXPORT void Maximum<T, CPUContext>(                                      \
      const int N, const float alpha, const T* x, T* y, CPUContext* context) { \
    std::transform(                                                            \
        x, x + N, y, [&alpha](const T& x_i) { return std::max(x_i, alpha); }); \
  }
CAFFE2_SPECIALIZED_MAXIMUM(float)
#undef CAFFE2_SPECIALIZED_MAXIMUM

// The actual implementation uses eigen which is column major, so notice the
// row/column swap in the actual implementation.

#define DELEGATE_EIGEN_2D_BROADCAST_1ST_BINARY_FUNCTION(T, Func, expr) \
  template <>                                                          \
  C10_EXPORT void Rowwise##Func<T, CPUContext, true>(                  \
      const int rows,                                                  \
      const int cols,                                                  \
      const T* A,                                                      \
      const T* B,                                                      \
      T* C,                                                            \
      CPUContext*) {                                                   \
    if (C == B) {                                                      \
      EigenArrayMap<T>(C, cols, rows).colwise() expr## =               \
          ConstEigenVectorArrayMap<T>(A, cols);                        \
    } else {                                                           \
      EigenArrayMap<T>(C, cols, rows) =                                \
          ConstEigenArrayMap<T>(B, cols, rows)                         \
              .colwise() expr ConstEigenVectorArrayMap<T>(A, cols);    \
    }                                                                  \
  }                                                                    \
  template <>                                                          \
  C10_EXPORT void Colwise##Func<T, CPUContext, true>(                  \
      const int rows,                                                  \
      const int cols,                                                  \
      const T* A,                                                      \
      const T* B,                                                      \
      T* C,                                                            \
      CPUContext*) {                                                   \
    if (C == B) {                                                      \
      EigenArrayMap<T>(C, cols, rows).rowwise() expr## =               \
          ConstEigenVectorArrayMap<T>(A, rows).transpose();            \
    } else {                                                           \
      EigenArrayMap<T>(C, cols, rows) =                                \
          ConstEigenArrayMap<T>(B, cols, rows)                         \
              .rowwise() expr ConstEigenVectorArrayMap<T>(A, rows)     \
              .transpose();                                            \
    }                                                                  \
  }

#define DELEGATE_EIGEN_2D_BROADCAST_2ND_BINARY_FUNCTION(T, Func, expr) \
  template <>                                                          \
  C10_EXPORT void Rowwise##Func<T, CPUContext, false>(                 \
      const int rows,                                                  \
      const int cols,                                                  \
      const T* A,                                                      \
      const T* B,                                                      \
      T* C,                                                            \
      CPUContext*) {                                                   \
    if (C == A) {                                                      \
      EigenArrayMap<T>(C, cols, rows).colwise() expr## =               \
          ConstEigenVectorArrayMap<T>(B, cols);                        \
    } else {                                                           \
      EigenArrayMap<T>(C, cols, rows) =                                \
          ConstEigenArrayMap<T>(A, cols, rows)                         \
              .colwise() expr ConstEigenVectorArrayMap<T>(B, cols);    \
    }                                                                  \
  }                                                                    \
  template <>                                                          \
  C10_EXPORT void Colwise##Func<T, CPUContext, false>(                 \
      const int rows,                                                  \
      const int cols,                                                  \
      const T* A,                                                      \
      const T* B,                                                      \
      T* C,                                                            \
      CPUContext*) {                                                   \
    if (C == A) {                                                      \
      EigenArrayMap<T>(C, cols, rows).rowwise() expr## =               \
          ConstEigenVectorArrayMap<T>(B, rows).transpose();            \
    } else {                                                           \
      EigenArrayMap<T>(C, cols, rows) =                                \
          ConstEigenArrayMap<T>(A, cols, rows)                         \
              .rowwise() expr ConstEigenVectorArrayMap<T>(B, rows)     \
              .transpose();                                            \
    }                                                                  \
  }

#define DELEGATE_EIGEN_2D_BROADCAST_BINARY_FUNCTION(T, Func, expr) \
  DELEGATE_EIGEN_2D_BROADCAST_1ST_BINARY_FUNCTION(T, Func, expr)   \
  DELEGATE_EIGEN_2D_BROADCAST_2ND_BINARY_FUNCTION(T, Func, expr)

#define DEFINE_EIGEN_2D_BROADCAST_BINARY_FUNCTION(Func, expr)           \
  DELEGATE_EIGEN_2D_BROADCAST_BINARY_FUNCTION(float, Func, expr)        \
  DELEGATE_EIGEN_2D_BROADCAST_BINARY_FUNCTION(double, Func, expr)       \
  DELEGATE_EIGEN_2D_BROADCAST_BINARY_FUNCTION(std::int32_t, Func, expr) \
  DELEGATE_EIGEN_2D_BROADCAST_BINARY_FUNCTION(std::int64_t, Func, expr)

DEFINE_EIGEN_2D_BROADCAST_BINARY_FUNCTION(Add, +)
DEFINE_EIGEN_2D_BROADCAST_BINARY_FUNCTION(Mul, *)

#undef DEFINE_EIGEN_2D_BROADCAST_BINARY_FUNCTION
#undef DELEGATE_EIGEN_2D_BROADCAST_BINARY_FUNCTION

#define DEFINE_EIGEN_2D_BROADCAST_SUB_FUNCTION(T)           \
  template <>                                               \
  C10_EXPORT void RowwiseSub<T, CPUContext, true>(          \
      const int rows,                                       \
      const int cols,                                       \
      const T* A,                                           \
      const T* B,                                           \
      T* C,                                                 \
      CPUContext*) {                                        \
    EigenArrayMap<T>(C, cols, rows) =                       \
        (-ConstEigenArrayMap<T>(B, cols, rows)).colwise() + \
        ConstEigenVectorArrayMap<T>(A, cols);               \
  }                                                         \
  template <>                                               \
  C10_EXPORT void ColwiseSub<T, CPUContext, true>(          \
      const int rows,                                       \
      const int cols,                                       \
      const T* A,                                           \
      const T* B,                                           \
      T* C,                                                 \
      CPUContext*) {                                        \
    EigenArrayMap<T>(C, cols, rows) =                       \
        (-ConstEigenArrayMap<T>(B, cols, rows)).rowwise() + \
        ConstEigenVectorArrayMap<T>(A, rows).transpose();   \
  }                                                         \
  DELEGATE_EIGEN_2D_BROADCAST_2ND_BINARY_FUNCTION(T, Sub, -)

DEFINE_EIGEN_2D_BROADCAST_SUB_FUNCTION(float)
DEFINE_EIGEN_2D_BROADCAST_SUB_FUNCTION(double)
DEFINE_EIGEN_2D_BROADCAST_SUB_FUNCTION(std::int32_t)
DEFINE_EIGEN_2D_BROADCAST_SUB_FUNCTION(std::int64_t)

#undef DEFINE_EIGEN_2D_BROADCAST_SUB_FUNCTION

#define DEFINE_EIGEN_2D_BROADCAST_DIV_FUNCTION(T)                  \
  template <>                                                      \
  C10_EXPORT void RowwiseDiv<T, CPUContext, true>(                 \
      const int rows,                                              \
      const int cols,                                              \
      const T* A,                                                  \
      const T* B,                                                  \
      T* C,                                                        \
      CPUContext*) {                                               \
    EigenArrayMap<T>(C, cols, rows) =                              \
        ConstEigenArrayMap<T>(B, cols, rows).inverse().colwise() * \
        ConstEigenVectorArrayMap<T>(A, cols);                      \
  }                                                                \
  template <>                                                      \
  C10_EXPORT void ColwiseDiv<T, CPUContext, true>(                 \
      const int rows,                                              \
      const int cols,                                              \
      const T* A,                                                  \
      const T* B,                                                  \
      T* C,                                                        \
      CPUContext*) {                                               \
    EigenArrayMap<T>(C, cols, rows) =                              \
        ConstEigenArrayMap<T>(B, cols, rows).inverse().rowwise() * \
        ConstEigenVectorArrayMap<T>(A, rows).transpose();          \
  }                                                                \
  DELEGATE_EIGEN_2D_BROADCAST_2ND_BINARY_FUNCTION(T, Div, /)

DEFINE_EIGEN_2D_BROADCAST_DIV_FUNCTION(float)
DEFINE_EIGEN_2D_BROADCAST_DIV_FUNCTION(double)
DELEGATE_EIGEN_2D_BROADCAST_2ND_BINARY_FUNCTION(std::int32_t, Div, /)
DELEGATE_EIGEN_2D_BROADCAST_2ND_BINARY_FUNCTION(std::int64_t, Div, /)

#undef DEFINE_EIGEN_2D_BROADCAST_DIV_FUNCTION

#undef DELEGATE_EIGEN_2D_BROADCAST_1ST_BINARY_FUNCTION
#undef DELEGATE_EIGEN_2D_BROADCAST_2ND_BINARY_FUNCTION

template <>
C10_EXPORT void Not<bool, CPUContext>(
    const int N,
    const bool* x,
    bool* y,
    CPUContext* /*context*/) {
  for (int i = 0; i < N; ++i) {
    y[i] = !x[i];
  }
}

#undef C10_DEFINE_BINARY_OP
#undef CAFFE2_INSTANTIATE_BINARY_OP

#define CAFFE2_SPECIALIZED_CPU_ADD_STRIPED_BATCH(T)             \
  template <>                                                   \
  C10_EXPORT void AddStripedBatch(                              \
      const int N,                                              \
      const T* first,                                           \
      T* y,                                                     \
      const int stripe,                                         \
      const int batch,                                          \
      CPUContext* context) {                                    \
    for (int j = 0; j < batch; j++) {                           \
      Add<T, CPUContext>(N, first + j * stripe, y, y, context); \
    }                                                           \
  }

CAFFE2_SPECIALIZED_CPU_ADD_STRIPED_BATCH(float);
#undef CAFFE2_SPECIALIZED_CPU_ADD_STRIPED_BATCH

namespace {

template <typename TIn, typename TOut, class BinaryOperator, bool kBroadcast1st>
C10_EXPORT void RowwiseBinaryOp(
    const int rows,
    const int cols,
    const BinaryOperator& op,
    const TIn* A,
    const TIn* B,
    TOut* C) {
  for (int i = 0; i < rows; ++i) {
    for (int j = 0; j < cols; ++j) {
      const int C_index = i * cols + j;
      const int A_index = kBroadcast1st ? j : C_index;
      const int B_index = kBroadcast1st ? C_index : j;
      C[C_index] = op(A[A_index], B[B_index]);
    }
  }
}

template <typename TIn, typename TOut, class BinaryOperator, bool kBroadcast1st>
C10_EXPORT void ColwiseBinaryOp(
    const int rows,
    const int cols,
    const BinaryOperator& op,
    const TIn* A,
    const TIn* B,
    TOut* C) {
  for (int i = 0; i < rows; ++i) {
    for (int j = 0; j < cols; ++j) {
      const int C_index = i * cols + j;
      const int A_index = kBroadcast1st ? i : C_index;
      const int B_index = kBroadcast1st ? C_index : i;
      C[C_index] = op(A[A_index], B[B_index]);
    }
  }
}

template <typename TIn, typename TOut, class BinaryOperator>
C10_EXPORT void BroadcastBinaryOpImpl(
    const int ndim,
    const int* A_dims,
    const int* B_dims,
    const int* C_dims,
    const BinaryOperator& op,
    const TIn* A,
    const TIn* B,
    TOut* C) {
  std::vector<int> index(ndim, 0);
  const int C_size =
      std::accumulate(C_dims, C_dims + ndim, 1, std::multiplies<int>());
  for (int C_index = 0; C_index < C_size; ++C_index) {
    const int A_index = utils::GetIndexFromDims(ndim, A_dims, index.data());
    const int B_index = utils::GetIndexFromDims(ndim, B_dims, index.data());
    C[C_index] = op(A[A_index], B[B_index]);
    utils::IncreaseIndexInDims(ndim, C_dims, index.data());
  }
}

} // namespace

#define DELEGATE_1D_BINARY_FUNCTION(TIn, TOut, Func, Op)               \
  template <>                                                          \
  C10_EXPORT void Func<TIn, CPUContext>(                               \
      const int N, const TIn* A, const TIn* B, TOut* C, CPUContext*) { \
    std::transform(A, A + N, B, C, Op<TIn>());                         \
  }

#define DEFINE_1D_COMPARE_FUNCTION(Func, Op)                \
  DELEGATE_1D_BINARY_FUNCTION(float, bool, Func, Op)        \
  DELEGATE_1D_BINARY_FUNCTION(double, bool, Func, Op)       \
  DELEGATE_1D_BINARY_FUNCTION(std::int32_t, bool, Func, Op) \
  DELEGATE_1D_BINARY_FUNCTION(std::int64_t, bool, Func, Op) \
  DELEGATE_1D_BINARY_FUNCTION(bool, bool, Func, Op)

DEFINE_1D_COMPARE_FUNCTION(EQ, std::equal_to)
DEFINE_1D_COMPARE_FUNCTION(NE, std::not_equal_to)
DEFINE_1D_COMPARE_FUNCTION(LT, std::less)
DEFINE_1D_COMPARE_FUNCTION(LE, std::less_equal)
DEFINE_1D_COMPARE_FUNCTION(GT, std::greater)
DEFINE_1D_COMPARE_FUNCTION(GE, std::greater_equal)

#undef DEFINE_1D_COMPARE_FUNCTION

DELEGATE_1D_BINARY_FUNCTION(bool, bool, And, std::logical_and)
DELEGATE_1D_BINARY_FUNCTION(bool, bool, Or, std::logical_or)
DELEGATE_1D_BINARY_FUNCTION(bool, bool, Xor, std::bit_xor)

#define DEFINE_1D_BITWISE_BINARY_FUNCTION(Func, op)                 \
  DELEGATE_1D_BINARY_FUNCTION(bool, bool, Func, op)                 \
  DELEGATE_1D_BINARY_FUNCTION(std::int32_t, std::int32_t, Func, op) \
  DELEGATE_1D_BINARY_FUNCTION(std::int64_t, std::int64_t, Func, op)

DEFINE_1D_BITWISE_BINARY_FUNCTION(BitwiseAnd, std::bit_and)
DEFINE_1D_BITWISE_BINARY_FUNCTION(BitwiseOr, std::bit_or)
DEFINE_1D_BITWISE_BINARY_FUNCTION(BitwiseXor, std::bit_xor)

#undef DEFINE_1D_BITWISE_BINARY_FUNCTION

#undef DELEGATE_1D_BINARY_FUNCTION

#define DELEGATE_2D_BROADCAST_BINARY_FUNCTION(TIn, TOut, Func, Op)             \
  template <>                                                                  \
  C10_EXPORT void Rowwise##Func<TIn, CPUContext, true>(                        \
      const int rows,                                                          \
      const int cols,                                                          \
      const TIn* A,                                                            \
      const TIn* B,                                                            \
      TOut* C,                                                                 \
      CPUContext*) {                                                           \
    RowwiseBinaryOp<TIn, TOut, Op<TIn>, true>(rows, cols, Op<TIn>(), A, B, C); \
  }                                                                            \
  template <>                                                                  \
  C10_EXPORT void Rowwise##Func<TIn, CPUContext, false>(                       \
      const int rows,                                                          \
      const int cols,                                                          \
      const TIn* A,                                                            \
      const TIn* B,                                                            \
      TOut* C,                                                                 \
      CPUContext*) {                                                           \
    RowwiseBinaryOp<TIn, TOut, Op<TIn>, false>(                                \
        rows, cols, Op<TIn>(), A, B, C);                                       \
  }                                                                            \
  template <>                                                                  \
  C10_EXPORT void Colwise##Func<TIn, CPUContext, true>(                        \
      const int rows,                                                          \
      const int cols,                                                          \
      const TIn* A,                                                            \
      const TIn* B,                                                            \
      TOut* C,                                                                 \
      CPUContext*) {                                                           \
    ColwiseBinaryOp<TIn, TOut, Op<TIn>, true>(rows, cols, Op<TIn>(), A, B, C); \
  }                                                                            \
  template <>                                                                  \
  C10_EXPORT void Colwise##Func<TIn, CPUContext, false>(                       \
      const int rows,                                                          \
      const int cols,                                                          \
      const TIn* A,                                                            \
      const TIn* B,                                                            \
      TOut* C,                                                                 \
      CPUContext*) {                                                           \
    ColwiseBinaryOp<TIn, TOut, Op<TIn>, false>(                                \
        rows, cols, Op<TIn>(), A, B, C);                                       \
  }

#define DEFINE_2D_COMPARE_FUNCTION(Func, Op)                          \
  DELEGATE_2D_BROADCAST_BINARY_FUNCTION(float, bool, Func, Op)        \
  DELEGATE_2D_BROADCAST_BINARY_FUNCTION(double, bool, Func, Op)       \
  DELEGATE_2D_BROADCAST_BINARY_FUNCTION(std::int32_t, bool, Func, Op) \
  DELEGATE_2D_BROADCAST_BINARY_FUNCTION(std::int64_t, bool, Func, Op) \
  DELEGATE_2D_BROADCAST_BINARY_FUNCTION(bool, bool, Func, Op)

DEFINE_2D_COMPARE_FUNCTION(EQ, std::equal_to)
DEFINE_2D_COMPARE_FUNCTION(NE, std::not_equal_to)
DEFINE_2D_COMPARE_FUNCTION(LT, std::less)
DEFINE_2D_COMPARE_FUNCTION(LE, std::less_equal)
DEFINE_2D_COMPARE_FUNCTION(GT, std::greater)
DEFINE_2D_COMPARE_FUNCTION(GE, std::greater_equal)

#undef DEFINE_2D_COMPARE_FUNCTION

DELEGATE_2D_BROADCAST_BINARY_FUNCTION(bool, bool, And, std::logical_and)
DELEGATE_2D_BROADCAST_BINARY_FUNCTION(bool, bool, Or, std::logical_or)
DELEGATE_2D_BROADCAST_BINARY_FUNCTION(bool, bool, Xor, std::bit_xor)

#define DEFINE_2D_BROADCAST_BITWISE_BINARY_FUNCTION(Func, Op)                 \
  DELEGATE_2D_BROADCAST_BINARY_FUNCTION(bool, bool, Func, Op)                 \
  DELEGATE_2D_BROADCAST_BINARY_FUNCTION(std::int32_t, std::int32_t, Func, Op) \
  DELEGATE_2D_BROADCAST_BINARY_FUNCTION(std::int64_t, std::int64_t, Func, Op)

DEFINE_2D_BROADCAST_BITWISE_BINARY_FUNCTION(BitwiseAnd, std::bit_and)
DEFINE_2D_BROADCAST_BITWISE_BINARY_FUNCTION(BitwiseOr, std::bit_or)
DEFINE_2D_BROADCAST_BITWISE_BINARY_FUNCTION(BitwiseXor, std::bit_xor)

#undef DEFINE_2D_BROADCAST_BITWISE_BINARY_FUNCTION

#undef DELEGATE_2D_BROADCAST_BINARY_FUNCTION

#define DEFINE_2D_BROADCAST_1ST_DIV_FUNCTION(T)    \
  template <>                                      \
  C10_EXPORT void RowwiseDiv<T, CPUContext, true>( \
      const int rows,                              \
      const int cols,                              \
      const T* A,                                  \
      const T* B,                                  \
      T* C,                                        \
      CPUContext*) {                               \
    RowwiseBinaryOp<T, T, std::divides<T>, true>(  \
        rows, cols, std::divides<T>(), A, B, C);   \
  }                                                \
  template <>                                      \
  C10_EXPORT void ColwiseDiv<T, CPUContext, true>( \
      const int rows,                              \
      const int cols,                              \
      const T* A,                                  \
      const T* B,                                  \
      T* C,                                        \
      CPUContext*) {                               \
    ColwiseBinaryOp<T, T, std::divides<T>, true>(  \
        rows, cols, std::divides<T>(), A, B, C);   \
  }
DEFINE_2D_BROADCAST_1ST_DIV_FUNCTION(std::int32_t)
DEFINE_2D_BROADCAST_1ST_DIV_FUNCTION(std::int64_t)
#undef DEFINE_2D_BROADCAST_1ST_DIV_FUNCTION

#define DELEGATE_BROADCAST_BINARY_FUNCTION(TIn, TOut, Func, Op)              \
  template <>                                                                \
  C10_EXPORT void Func<TIn, CPUContext>(                                     \
      const int A_ndim,                                                      \
      const int* A_dims,                                                     \
      const int B_ndim,                                                      \
      const int* B_dims,                                                     \
      const TIn* A,                                                          \
      const TIn* B,                                                          \
      TOut* C,                                                               \
      CPUContext* context) {                                                 \
    const int ndim = std::max(A_ndim, B_ndim);                               \
    std::vector<int> A_dims_array(ndim);                                     \
    std::vector<int> B_dims_array(ndim);                                     \
    std::vector<int> C_dims_array(ndim);                                     \
    utils::ComputeBroadcastBinaryOpDims(                                     \
        A_ndim,                                                              \
        A_dims,                                                              \
        B_ndim,                                                              \
        B_dims,                                                              \
        A_dims_array.data(),                                                 \
        B_dims_array.data(),                                                 \
        C_dims_array.data());                                                \
    if (A_dims_array == B_dims_array) {                                      \
      const int size = std::accumulate(                                      \
          C_dims_array.cbegin(),                                             \
          C_dims_array.cend(),                                               \
          1,                                                                 \
          std::multiplies<int>());                                           \
      Func<TIn, CPUContext>(size, A, B, C, context);                         \
      return;                                                                \
    }                                                                        \
    int rows;                                                                \
    int cols;                                                                \
    bool broadcast_1st;                                                      \
    if (utils::IsRowwiseBroadcastBinaryOp(                                   \
            ndim,                                                            \
            A_dims_array.data(),                                             \
            B_dims_array.data(),                                             \
            &rows,                                                           \
            &cols,                                                           \
            &broadcast_1st)) {                                               \
      if (broadcast_1st) {                                                   \
        Rowwise##Func<TIn, CPUContext, true>(rows, cols, A, B, C, context);  \
      } else {                                                               \
        Rowwise##Func<TIn, CPUContext, false>(rows, cols, A, B, C, context); \
      }                                                                      \
      return;                                                                \
    }                                                                        \
    if (utils::IsColwiseBroadcastBinaryOp(                                   \
            ndim,                                                            \
            A_dims_array.data(),                                             \
            B_dims_array.data(),                                             \
            &rows,                                                           \
            &cols,                                                           \
            &broadcast_1st)) {                                               \
      if (broadcast_1st) {                                                   \
        Colwise##Func<TIn, CPUContext, true>(rows, cols, A, B, C, context);  \
      } else {                                                               \
        Colwise##Func<TIn, CPUContext, false>(rows, cols, A, B, C, context); \
      }                                                                      \
      return;                                                                \
    }                                                                        \
    int pre;                                                                 \
    int mid;                                                                 \
    int nxt;                                                                 \
    if (utils::IsBothEndsBroadcastBinaryOp(                                  \
            ndim,                                                            \
            A_dims_array.data(),                                             \
            B_dims_array.data(),                                             \
            &pre,                                                            \
            &mid,                                                            \
            &nxt,                                                            \
            &broadcast_1st)) {                                               \
      const int stride = mid * nxt;                                          \
      for (int i = 0; i < pre; ++i) {                                        \
        if (broadcast_1st) {                                                 \
          Colwise##Func<TIn, CPUContext, true>(                              \
              mid, nxt, A, B + i * stride, C + i * stride, context);         \
        } else {                                                             \
          Colwise##Func<TIn, CPUContext, false>(                             \
              mid, nxt, A + i * stride, B, C + i * stride, context);         \
        }                                                                    \
      }                                                                      \
      return;                                                                \
    }                                                                        \
    BroadcastBinaryOpImpl(                                                   \
        ndim,                                                                \
        A_dims_array.data(),                                                 \
        B_dims_array.data(),                                                 \
        C_dims_array.data(),                                                 \
        Op<TIn>(),                                                           \
        A,                                                                   \
        B,                                                                   \
        C);                                                                  \
  }

#define DEFINE_BROADCAST_COMPARE_FUNCTION(Func, Op)                \
  DELEGATE_BROADCAST_BINARY_FUNCTION(float, bool, Func, Op)        \
  DELEGATE_BROADCAST_BINARY_FUNCTION(double, bool, Func, Op)       \
  DELEGATE_BROADCAST_BINARY_FUNCTION(std::int32_t, bool, Func, Op) \
  DELEGATE_BROADCAST_BINARY_FUNCTION(std::int64_t, bool, Func, Op) \
  DELEGATE_BROADCAST_BINARY_FUNCTION(bool, bool, Func, Op)

DEFINE_BROADCAST_COMPARE_FUNCTION(EQ, std::equal_to)
DEFINE_BROADCAST_COMPARE_FUNCTION(NE, std::not_equal_to)
DEFINE_BROADCAST_COMPARE_FUNCTION(LT, std::less)
DEFINE_BROADCAST_COMPARE_FUNCTION(LE, std::less_equal)
DEFINE_BROADCAST_COMPARE_FUNCTION(GT, std::greater)
DEFINE_BROADCAST_COMPARE_FUNCTION(GE, std::greater_equal)

#undef DEFINE_BROADCAST_COMPARE_FUNCTION

#define DEFINE_BROADCAST_BINARY_FUNCTION(Func, Op)                         \
  DELEGATE_BROADCAST_BINARY_FUNCTION(float, float, Func, Op)               \
  DELEGATE_BROADCAST_BINARY_FUNCTION(double, double, Func, Op)             \
  DELEGATE_BROADCAST_BINARY_FUNCTION(std::int32_t, std::int32_t, Func, Op) \
  DELEGATE_BROADCAST_BINARY_FUNCTION(std::int64_t, std::int64_t, Func, Op)

DEFINE_BROADCAST_BINARY_FUNCTION(Add, std::plus)
DEFINE_BROADCAST_BINARY_FUNCTION(Sub, std::minus)
DEFINE_BROADCAST_BINARY_FUNCTION(Mul, std::multiplies)
DEFINE_BROADCAST_BINARY_FUNCTION(Div, std::divides)

#undef DEFINE_BROADCAST_BINARY_FUNCTION

DELEGATE_BROADCAST_BINARY_FUNCTION(bool, bool, And, std::logical_and)
DELEGATE_BROADCAST_BINARY_FUNCTION(bool, bool, Or, std::logical_or)
DELEGATE_BROADCAST_BINARY_FUNCTION(bool, bool, Xor, std::bit_xor)

#define DEFINE_BROADCAST_BITWISE_BINARY_FUNCTION(Func, Op)                 \
  DELEGATE_BROADCAST_BINARY_FUNCTION(bool, bool, Func, Op)                 \
  DELEGATE_BROADCAST_BINARY_FUNCTION(std::int32_t, std::int32_t, Func, Op) \
  DELEGATE_BROADCAST_BINARY_FUNCTION(std::int64_t, std::int64_t, Func, Op)

DEFINE_BROADCAST_BITWISE_BINARY_FUNCTION(BitwiseAnd, std::bit_and)
DEFINE_BROADCAST_BITWISE_BINARY_FUNCTION(BitwiseOr, std::bit_or)
DEFINE_BROADCAST_BITWISE_BINARY_FUNCTION(BitwiseXor, std::bit_xor)

#undef DEFINE_BITWISE_BROADCAST_BINARY_FUNCTION

#undef DELEGATE_BROADCAST_BINARY_FUNCTION

#define CAFFE2_RAND_UNIFORM_REAL(T)                                      \
  template <>                                                            \
  C10_EXPORT void RandUniform<T, CPUContext>(                            \
      const size_t n, const T a, const T b, T* r, CPUContext* context) { \
    std::uniform_real_distribution<T> distribution(a, b);                \
    for (size_t i = 0; i < n; ++i) {                                     \
      r[i] = distribution(context->RandGenerator());                     \
    }                                                                    \
  }
CAFFE2_RAND_UNIFORM_REAL(float);
CAFFE2_RAND_UNIFORM_REAL(double);
#undef CAFFE2_RAND_UNIFORM_REAL

#define CAFFE2_RAND_UNIFORM_CHAR(T)                                        \
  template <>                                                              \
  C10_EXPORT void RandUniform<T, CPUContext>(                              \
      const size_t n, const T a, const T b, T* r, CPUContext* context) {   \
    std::uniform_int_distribution<short> distribution((short)a, (short)b); \
    for (size_t i = 0; i < n; ++i) {                                       \
      r[i] = static_cast<T>(distribution(context->RandGenerator()));       \
    }                                                                      \
  }
CAFFE2_RAND_UNIFORM_CHAR(int8_t);
CAFFE2_RAND_UNIFORM_CHAR(uint8_t);
#undef CAFFE2_RAND_UNIFORM_CHAR

#define CAFFE2_RAND_UNIFORM_INT(T)                                       \
  template <>                                                            \
  C10_EXPORT void RandUniform<T, CPUContext>(                            \
      const size_t n, const T a, const T b, T* r, CPUContext* context) { \
    std::uniform_int_distribution<T> distribution(a, b);                 \
    for (size_t i = 0; i < n; ++i) {                                     \
      r[i] = distribution(context->RandGenerator());                     \
    }                                                                    \
  }

CAFFE2_RAND_UNIFORM_INT(int16_t);
CAFFE2_RAND_UNIFORM_INT(int32_t);
CAFFE2_RAND_UNIFORM_INT(int64_t);
CAFFE2_RAND_UNIFORM_INT(uint16_t);
CAFFE2_RAND_UNIFORM_INT(uint32_t);
CAFFE2_RAND_UNIFORM_INT(uint64_t);
#undef CAFFE2_RAND_UNIFORM_INT

// This is not uniformly distributed between a and b.
// It takes advantage of normal distribution to generate numbers
// with mean = sum / n.
// Ideally the algorithm should be generating n numbers between 0 and 1,
// sum them up as scaled_sum, and use sum / scaled_sum to adjust the values
// to between a and b.
// The algorithm is non-trivial given the adjustment would be different towards
// each value.
#define CAFFE2_RAND_FIXED_SUM(T)                                        \
  template <>                                                           \
  C10_EXPORT void RandFixedSum<T, CPUContext>(                          \
      const size_t n,                                                   \
      const T a,                                                        \
      const T b,                                                        \
      const T sum,                                                      \
      T* r,                                                             \
      CPUContext* context) {                                            \
    CAFFE_ENFORCE_GE(a, 0);                                             \
    CAFFE_ENFORCE_GE(sum / (double)n, a);                               \
    CAFFE_ENFORCE_LE(sum / (double)n, b);                               \
    T current_sum = 0;                                                  \
    for (size_t i = 0; i < n - 1; ++i) {                                \
      auto remaining_numbers = n - 1 - i;                               \
      double mean = (sum - current_sum) / remaining_numbers;            \
      double stdev = std::min(mean - a, b - mean);                      \
      std::normal_distribution<double> distribution{mean, stdev / 4.0}; \
      T value = distribution(context->RandGenerator());                 \
      auto remaining_sum = sum - current_sum - value;                   \
      if (value < a || remaining_sum > b * remaining_numbers) {         \
        value = a;                                                      \
      } else if (value > b || remaining_sum < a * remaining_numbers) {  \
        value = b;                                                      \
      }                                                                 \
      r[i] = value;                                                     \
      CAFFE_ENFORCE(a <= value && value <= b);                          \
      current_sum += value;                                             \
    }                                                                   \
    r[n - 1] = sum - current_sum;                                       \
    CAFFE_ENFORCE(a <= r[n - 1] && r[n - 1] <= b);                      \
  }
CAFFE2_RAND_FIXED_SUM(float);
CAFFE2_RAND_FIXED_SUM(double);
CAFFE2_RAND_FIXED_SUM(int8_t);
CAFFE2_RAND_FIXED_SUM(int16_t);
CAFFE2_RAND_FIXED_SUM(int32_t);
CAFFE2_RAND_FIXED_SUM(int64_t);
CAFFE2_RAND_FIXED_SUM(uint8_t);
CAFFE2_RAND_FIXED_SUM(uint16_t);
CAFFE2_RAND_FIXED_SUM(uint32_t);
CAFFE2_RAND_FIXED_SUM(uint64_t);
#undef CAFFE2_RAND_FIXED_SUM

template <class Type, class Val_t, class Ind_t, class Context_t, bool cdf_app>
Ind_t generate_stack_distance(
    std::vector<Ind_t>& cum_val,
    std::vector<Val_t>& cum_dis,
    std::vector<Ind_t>& cum_map,
    Ind_t max_i,
    Ind_t i,
    Context_t* context) {
  /* Description:
     Inverse Transform Sampling method to generate values for random variable X
     that is described by the cumulative distribution F (cum_val,cum_dis).
     Notice, that we may choose to use the inverse map of F (cum_map) as an
     approximation to avoid searching. Also, scaling the probability so that
     the values are within max_i refs, because stack distance can not be >
     than the # of already generated refs (max_i).
  */
  Ind_t j, k, n;
  Val_t u, f, fi;

  // generate a random number u in [0,1] from a uniform distribution U
  math::RandUniform<Val_t, Context_t>(1, 0, 1, &u, context);

  // scale the random number u to be within range [0,f(i)], if needed
  if (i < max_i) {
    // approach 2: allows gaps in the distribution
    j = (std::upper_bound(cum_val.begin(), cum_val.end(), i) -
         cum_val.begin()) -
        1;
    fi = cum_dis[j];
    u *= fi;
  }
  // 2. compute the stack distance value of x, s.t. F(x)=u
  // notice that the cumulative distribution F increases monotonically up to 1
  if (cdf_app) {
    // look up cum_val corresponding to u <= cum_dis[j]
    k = cum_map.size();
    n = (Ind_t)round(u * k);
    j = cum_map[n];
    return cum_val[j];
  } else {
    // iterate until you find the cum_val corresponding to u <= cum_dis[j]
    for (j = 0; j < cum_dis.size(); j++) {
      f = cum_dis[j];
      if (u <= f) {
        return cum_val[j];
      }
    }
    return cum_val[j - 1];
  }
}

template <class Type, class Val_t, class Ind_t, class Context_t, bool cdf_app>
C10_EXPORT void generate_trace_lru(
    std::vector<Ind_t>& uni_ref,
    std::vector<Ind_t>& cum_val,
    std::vector<Val_t>& cum_dis,
    std::vector<Ind_t>& cum_map,
    Context_t* context,
    Ind_t cache_line_size,
    Ind_t n,
    Type min,
    Type max,
    Type* syn_ref) {
  /* Description:
     Generate synthetic trace from a list of unique accesses uni_ref, and
     cumulative distribution of distances (cum_val,cum_dis) between them.
     Also, there is an option to use cum_map approximation to avoid searching.
  */
  Ind_t i, j, k, sd, line_ref, mem_ref, mem_ref_within_line;
  Ind_t max_sd = cum_val.back();
  Ind_t l = uni_ref.size();

  for (i = 0, j = 0; j < n; j++) {
    // generate stack distance
    sd = generate_stack_distance<Type, Val_t, Ind_t, Context_t, cdf_app>(
        cum_val, cum_dis, cum_map, max_sd, i, context);
    // fixed access within cache line
    mem_ref_within_line = 0;
    // random access within cache line
    // Val_t r;
    // math::RandUniform<Val_t, Context_t>(1, 0, 1, &r, context);
    // mem_ref_within_line = floor(r*cache_line_size);

    // generate memory reference
    if (sd == 0) {
      k = 0; /// new reference ///
      i++;
    } else {
      k = l - sd; /// existing reference ///
    }
    line_ref = uni_ref[k]; // pop k-th element
    uni_ref.erase(uni_ref.begin() + k);
    uni_ref.push_back(line_ref); // append it back
    mem_ref = line_ref * cache_line_size + mem_ref_within_line;
    /*
    //debug prints
    if ((mem_ref < min) || (mem_ref > max)) {
      //printf("mem_ref[%d]=%d (%ld) \n",j,mem_ref,syn_ref[j]);
      std::cout << "syn_ref[" << j << "]=" << (Type)mem_ref << " ";
      std::cout << "(" << mem_ref << ") ";
      std::cout << "[" << min << "," << max << "]" << std::endl;
      int scanf_temp;
      scanf("%d",&scanf_temp);
    }
    */

    // patch mem_ref to be within range
    // WARNING: this should not be needed if instantiation type and distribution
    // choice is correct. It is remeding a symptom of earlier mistakes.
    if (mem_ref < min) {
      mem_ref = min;
      // std::cout << "clamping (min) mem_ref=" << mem_ref << std::endl;
    }
    if (mem_ref > max) {
      mem_ref = max; // mem_ref % max;
      // std::cout << "clamping (max) mem_ref=" << mem_ref << std::endl;
    }

    // save generated memory reference
    syn_ref[j] = (Type)mem_ref;
  }
}

// Generate n values from synthetic data distribution,
// define by unique accesses and stack distances
// WARNING: can create this for all tables or per table, but in latter
// case we need to know the table id, to sample from the right distribution
#define CAFFE2_RAND_SYNTHETIC_DATA(T)                                         \
  template <>                                                                 \
  C10_EXPORT void RandSyntheticData<T, CPUContext>(                           \
      const size_t n, const T a, const T b, T* r, CPUContext* context) {      \
    /* unique memory references */                                            \
    std::vector<int> mem_ref = {1, 2, 3, 4, 5, 6};                            \
    /* cumulative distribution of distances */                                \
    std::vector<int> cum_val = {0, 1, 3, 4, 5};                               \
    std::vector<double> cum_dis = {0.55, 0.64, 0.82, 0.91, 1.0};              \
    /* inverse map of cumulative distribution (for O(1) lookup) */            \
    /* std::vector<int> cum_map = {0, 0, 0, 0, 0, 1, 2, 2, 3, 4}; */          \
    int k = 10; /* 100; */                                                    \
    std::vector<int> cum_map(k, 0);                                           \
    for (int j = 0; j < cum_dis.size();) {                                    \
      int sz = (int)round(cum_dis[j] * k);                                    \
      for (int i = 0; i < sz; i++) {                                          \
        cum_map[j + i] = j;                                                   \
      }                                                                       \
      j += sz;                                                                \
    }                                                                         \
                                                                              \
    /* code to generate the synthetic data from the above values */           \
    const int cache_line = 1; /* 64; */                                       \
    generate_trace_lru<T, double, int, CPUContext, false>(                    \
        mem_ref, cum_val, cum_dis, cum_map, context, cache_line, n, a, b, r); \
  }

CAFFE2_RAND_SYNTHETIC_DATA(float);
CAFFE2_RAND_SYNTHETIC_DATA(double);
CAFFE2_RAND_SYNTHETIC_DATA(int8_t);
CAFFE2_RAND_SYNTHETIC_DATA(int16_t);
CAFFE2_RAND_SYNTHETIC_DATA(int32_t);
CAFFE2_RAND_SYNTHETIC_DATA(int64_t);
CAFFE2_RAND_SYNTHETIC_DATA(uint8_t);
CAFFE2_RAND_SYNTHETIC_DATA(uint16_t);
CAFFE2_RAND_SYNTHETIC_DATA(uint32_t);
CAFFE2_RAND_SYNTHETIC_DATA(uint64_t);
#undef CAFFE2_RAND_SYNTHETIC_DATA

#define CAFFE2_SPECIALIZED_RAND_UNIFORM_UNIQUE(T)                    \
  template <>                                                        \
  C10_EXPORT void RandUniformUnique<T, CPUContext>(                  \
      const size_t n,                                                \
      const T a,                                                     \
      const T b,                                                     \
      T* r,                                                          \
      const size_t m,                                                \
      const T* avoid,                                                \
      CPUContext* context) {                                         \
    CAFFE_ENFORCE_LE(                                                \
        n, b - a - m + 1, "Cannot satisfy the unique requirement");  \
    std::unordered_set<T> avoid_set(n);                              \
    if (m) {                                                         \
      avoid_set.insert(avoid, avoid + m);                            \
      CAFFE_ENFORCE_EQ(                                              \
          m, avoid_set.size(), "AC10_EXPORT void should be unique"); \
    }                                                                \
    std::uniform_int_distribution<T> distribution(a, b);             \
    T v = 0;                                                         \
    for (size_t i = 0; i < n; ++i) {                                 \
      do {                                                           \
        v = distribution(context->RandGenerator());                  \
      } while (avoid_set.count(v));                                  \
      r[i] = v;                                                      \
      avoid_set.insert(v);                                           \
    }                                                                \
  }

CAFFE2_SPECIALIZED_RAND_UNIFORM_UNIQUE(int32_t);
CAFFE2_SPECIALIZED_RAND_UNIFORM_UNIQUE(int64_t);
#undef CAFFE2_SPECIALIZED_RAND_UNIFORM_UNIQUE

template <>
C10_EXPORT void RandGaussian<float, CPUContext>(
    const size_t n,
    const float mean,
    const float std,
    float* r,
    CPUContext* context) {
  std::normal_distribution<float> distribution(mean, std);
  for (size_t i = 0; i < n; ++i) {
    r[i] = distribution(context->RandGenerator());
  }
}

#define CAFFE2_SPECIALIZED_SUM(T)            \
  template <>                                \
  C10_EXPORT void Sum<T, CPUContext>(        \
      const int N,                           \
      const T* x,                            \
      T* y,                                  \
      CPUContext* /* unused */,              \
      Tensor* /* unused */) {                \
    *y = ConstEigenVectorMap<T>(x, N).sum(); \
  }

CAFFE2_SPECIALIZED_SUM(float);
CAFFE2_SPECIALIZED_SUM(int32_t);
CAFFE2_SPECIALIZED_SUM(int64_t);

#undef CAFFE2_SPECIALIZED_SUM

template <>
C10_EXPORT void SumSqr<float, CPUContext>(
    const int N,
    const float* x,
    float* y,
    CPUContext* /*context*/ /* unused */,
    Tensor* /*scratch_ptr*/ /* unused */) {
  *y = ConstEigenVectorMap<float>(x, N).squaredNorm();
}

template <>
C10_EXPORT void Select<float, CPUContext>(
    const int N,
    const int D,
    const float* x,
    const int* idx,
    float* y,
    CPUContext* /*context*/) {
  for (int i = 0; i < N; ++i) {
    DCHECK_LT(idx[i], D);
    y[i] = x[i * D + idx[i]];
  }
}

template <>
C10_EXPORT void CopyMatrix<CPUContext>(
    const size_t itemsize,
    const int M,
    const int N,
    const void* A,
    const int lda,
    void* B,
    const int ldb,
    CPUContext* /*context*/,
    TypeMeta::TypedCopy copy) {
  if (A == nullptr || B == nullptr) {
    return;
  }
  if (lda == N && ldb == N) {
    // can coalese to a single memcpy of size M * N
    if (copy) {
      copy(static_cast<const char*>(A), static_cast<char*>(B), N * M);
    } else {
      memcpy(
          static_cast<char*>(B), static_cast<const char*>(A), itemsize * N * M);
    }
    return;
  }

  for (int i = 0; i < M; ++i) {
    if (copy) {
      copy(
          static_cast<const char*>(A) + lda * i * itemsize,
          static_cast<char*>(B) + ldb * i * itemsize,
          N);
    } else {
      memcpy(
          static_cast<char*>(B) + ldb * i * itemsize,
          static_cast<const char*>(A) + lda * i * itemsize,
          itemsize * N);
    }
  }
}

#ifdef CAFFE2_USE_MKL

#define DELEGATE_COPY_MATRIX_FUNCTION(T, Func)  \
  template <>                                   \
  C10_EXPORT void CopyMatrix<T, CPUContext>(    \
      const int M,                              \
      const int N,                              \
      const T* A,                               \
      const int lda,                            \
      T* B,                                     \
      const int ldb,                            \
      CPUContext* /* context */) {              \
    Func('R', 'N', M, N, T(1), A, lda, B, ldb); \
  }                                             \
  template <>                                   \
  C10_EXPORT void CopyMatrix<T, CPUContext>(    \
      const int M,                              \
      const int N,                              \
      const T* A,                               \
      const int A_outer_stride,                 \
      const int A_inner_stride,                 \
      T* B,                                     \
      const int B_outer_stride,                 \
      const int B_inner_stride,                 \
      CPUContext* /* context */) {              \
    Func##2(                                    \
        'R',                                    \
        'N',                                    \
        M,                                      \
        N,                                      \
        T(1),                                   \
        A,                                      \
        A_outer_stride,                         \
        A_inner_stride,                         \
        B,                                      \
        B_outer_stride,                         \
        B_inner_stride);                        \
  }
DELEGATE_COPY_MATRIX_FUNCTION(float, mkl_somatcopy)
DELEGATE_COPY_MATRIX_FUNCTION(double, mkl_domatcopy)
#undef DELEGATE_COPY_MATRIX_FUNCTION

#endif // CAFFE2_USE_MKL

#define CAFFE2_SPECIALIZED_COPY_MATRIX(T)                                \
  template <>                                                            \
  C10_EXPORT void CopyMatrix<T, CPUContext>(                             \
      const int M,                                                       \
      const int N,                                                       \
      const T* A,                                                        \
      const int lda,                                                     \
      T* B,                                                              \
      const int ldb,                                                     \
      CPUContext* /* context */) {                                       \
    if (M == 0 || N == 0) {                                              \
      return;                                                            \
    }                                                                    \
    if (lda == N) {                                                      \
      if (ldb == N) {                                                    \
        std::memcpy(B, A, sizeof(T) * M * N);                            \
      } else {                                                           \
        EigenOuterStridedMatrixMap<T>(B, N, M, EigenOuterStride(ldb)) =  \
            ConstEigenMatrixMap<T>(A, N, M);                             \
      }                                                                  \
    } else {                                                             \
      if (ldb == N) {                                                    \
        EigenMatrixMap<T>(B, N, M) = ConstEigenOuterStridedMatrixMap<T>( \
            A, N, M, EigenOuterStride(lda));                             \
      } else {                                                           \
        EigenOuterStridedMatrixMap<T>(B, N, M, EigenOuterStride(ldb)) =  \
            ConstEigenOuterStridedMatrixMap<T>(                          \
                A, N, M, EigenOuterStride(lda));                         \
      }                                                                  \
    }                                                                    \
  }                                                                      \
  template <>                                                            \
  C10_EXPORT void CopyMatrix<T, CPUContext>(                             \
      const int M,                                                       \
      const int N,                                                       \
      const T* A,                                                        \
      const int A_outer_stride,                                          \
      const int A_inner_stride,                                          \
      T* B,                                                              \
      const int B_outer_stride,                                          \
      const int B_inner_stride,                                          \
      CPUContext* context) {                                             \
    if (A_inner_stride == 1 && B_inner_stride == 1) {                    \
      CopyMatrix<T, CPUContext>(                                         \
          M, N, A, A_outer_stride, B, B_outer_stride, context);          \
      return;                                                            \
    }                                                                    \
    EigenStridedMatrixMap<T>(                                            \
        B, N, M, EigenStride(B_outer_stride, B_inner_stride)) =          \
        ConstEigenStridedMatrixMap<T>(                                   \
            A, N, M, EigenStride(A_outer_stride, A_inner_stride));       \
  }

#ifndef CAFFE2_USE_MKL
CAFFE2_SPECIALIZED_COPY_MATRIX(float)
CAFFE2_SPECIALIZED_COPY_MATRIX(double)
#endif // CAFFE2_USE_MKL

CAFFE2_SPECIALIZED_COPY_MATRIX(int)
CAFFE2_SPECIALIZED_COPY_MATRIX(int64_t)
#ifdef CAFFE2_UNIQUE_LONG_TYPEMETA
CAFFE2_SPECIALIZED_COPY_MATRIX(long)
#endif
CAFFE2_SPECIALIZED_COPY_MATRIX(std::uint8_t)
CAFFE2_SPECIALIZED_COPY_MATRIX(std::uint16_t)

#undef CAFFE2_SPECIALIZXED_COPY_MATRIX

namespace {

template <typename T>
C10_EXPORT void Im2ColZeroPaddingAndNoDilationNCHW(
    const int C,
    const int H,
    const int W,
    const int kernel_h,
    const int kernel_w,
    const int stride_h,
    const int stride_w,
    const T* img_data,
    T* col_data,
    CPUContext* context) {
  const int output_h = (H - kernel_h) / stride_h + 1;
  const int output_w = (W - kernel_w) / stride_w + 1;
  const int output_size = output_h * output_w;
  for (int c = 0; c < C; ++c) {
    for (int kh = 0; kh < kernel_h; ++kh) {
      for (int kw = 0; kw < kernel_w; ++kw) {
        const T* src = img_data + kh * W + kw;
        if (stride_w == 1) {
          CopyMatrix<T, CPUContext>(
              output_h,
              output_w,
              src,
              stride_h * W,
              col_data,
              output_w,
              context);
        } else {
          CopyMatrix<T, CPUContext>(
              output_h,
              output_w,
              src,
              stride_h * W,
              stride_w,
              col_data,
              output_w,
              1,
              context);
        }
        col_data += output_size;
      }
    }
    img_data += H * W;
  }
}

template <typename T>
C10_EXPORT void Col2ImZeroPaddingAndNoDilationNCHW(
    const int C,
    const int H,
    const int W,
    const int kernel_h,
    const int kernel_w,
    const int stride_h,
    const int stride_w,
    const T* col_data,
    T* img_data,
    CPUContext* context) {
  Set<T, CPUContext>(C * H * W, T(0), img_data, context);
  const int output_h = (H - kernel_h) / stride_h + 1;
  const int output_w = (W - kernel_w) / stride_w + 1;
  const int output_size = output_h * output_w;
  for (int c = 0; c < C; ++c) {
    for (int kh = 0; kh < kernel_h; ++kh) {
      for (int kw = 0; kw < kernel_w; ++kw) {
        T* dst = img_data + kh * W + kw;
        if (stride_w == 1) {
          EigenOuterStridedArrayMap<T>(
              dst, output_w, output_h, EigenOuterStride(stride_h * W)) +=
              ConstEigenArrayMap<T>(col_data, output_w, output_h);
        } else {
          EigenStridedArrayMap<T>(
              dst, output_w, output_h, EigenStride(stride_h * W, stride_w)) +=
              ConstEigenArrayMap<T>(col_data, output_w, output_h);
        }
        col_data += output_size;
      }
    }
    img_data += H * W;
  }
}

template <typename T>
C10_EXPORT void Im2ColZeroPaddingAndNoDilationNHWC(
    const int C,
    const int H,
    const int W,
    const int kernel_h,
    const int kernel_w,
    const int stride_h,
    const int stride_w,
    const T* img_data,
    T* col_data,
    CPUContext* context) {
  const int output_h = (H - kernel_h) / stride_h + 1;
  const int output_w = (W - kernel_w) / stride_w + 1;
  const int kernel_size = kernel_h * kernel_w;
  for (int yh = 0; yh < output_h; ++yh) {
    for (int yw = 0; yw < output_w; ++yw) {
      const T* src = img_data + (yh * stride_h * W + yw * stride_w) * C;
      CopyMatrix<T, CPUContext>(
          kernel_h, kernel_w * C, src, W * C, col_data, kernel_w * C, context);
      col_data += kernel_size * C;
    }
  }
}

template <typename T>
C10_EXPORT void Col2ImZeroPaddingAndNoDilationNHWC(
    const int C,
    const int H,
    const int W,
    const int kernel_h,
    const int kernel_w,
    const int stride_h,
    const int stride_w,
    const T* col_data,
    T* img_data,
    CPUContext* context) {
  Set<T, CPUContext>(H * W * C, T(0), img_data, context);
  const int output_h = (H - kernel_h) / stride_h + 1;
  const int output_w = (W - kernel_w) / stride_w + 1;
  const int kernel_size = kernel_h * kernel_w;
  for (int yh = 0; yh < output_h; ++yh) {
    for (int yw = 0; yw < output_w; ++yw) {
      T* dst = img_data + (yh * stride_h * W + yw * stride_w) * C;
      EigenOuterStridedArrayMap<T>(
          dst, kernel_w * C, kernel_h, EigenOuterStride(W * C)) +=
          ConstEigenArrayMap<T>(col_data, kernel_w * C, kernel_h);
      col_data += kernel_size * C;
    }
  }
}

template <typename T, bool kCol2Im>
C10_EXPORT void Im2ColNdNCHWImpl(
    const int N,
    const int img_size,
    const int col_size,
    const int* img_shape,
    const int* col_shape,
    const int* kernel_shape,
    const int* stride,
    const int* dilation,
    const int* pad,
    const float* X_data,
    float* Y_data) {
  if (kCol2Im) {
    std::memset(Y_data, 0, img_size * sizeof(float));
  }
  const int outer_size = col_shape[0];
  const int inner_size = col_size / outer_size;
  const int kernel_size = std::accumulate(
      kernel_shape, kernel_shape + N, 1, std::multiplies<int>());
  std::vector<FixedDivisor<int>> kernel_shape_div(N);
  for (int i = 0; i < N; ++i) {
    kernel_shape_div[i] = FixedDivisor<int>(kernel_shape[i]);
  }
  std::vector<int> d_offset(N, 0);
  std::vector<int> d_iter(N, 0);
  for (int i = 0; i < outer_size; ++i) {
    // Loop over spatial axes in reverse order to compute a per-axis offset.
    int offset = i;
    for (int d_i = N - 1; d_i >= 0; --d_i) {
      kernel_shape_div[d_i].DivMod(offset, &offset, &d_offset[d_i]);
    }
    for (int j = 0; j < inner_size; ++j) {
      // Loop over spatial axes in forward order to compute the indices in the
      // image and column, and whether the index lies in the padding.
      const int col_index = i * inner_size + j;
      int img_index = i / kernel_size;
      bool is_padding = false;
      for (int d_i = 0; d_i < N; ++d_i) {
        const int d_img = d_iter[d_i] * stride[d_i] - pad[d_i] +
            d_offset[d_i] * dilation[d_i];
        is_padding |= !utils::IsAGeZeroAndALtB(d_img, img_shape[d_i + 1]);
        img_index = img_index * img_shape[d_i + 1] + d_img;
      }
      if (!kCol2Im) {
        Y_data[col_index] = is_padding ? 0 : X_data[img_index];
      } else if (!is_padding) {
        Y_data[img_index] += X_data[col_index];
      }
      utils::IncreaseIndexInDims(N, col_shape + 1, d_iter.data());
    }
  }
}

} // namespace

template <>
C10_EXPORT void Im2ColNd<float, CPUContext, StorageOrder::NCHW>(
    const int N,
    const int img_size,
    const int col_size,
    const int* img_shape,
    const int* col_shape,
    const int* kernel_shape,
    const int* stride,
    const int* dilation,
    const int* pad,
    const float* img_data,
    float* col_data,
    CPUContext* /* context */) {
  Im2ColNdNCHWImpl<float, false>(
      N,
      img_size,
      col_size,
      img_shape,
      col_shape,
      kernel_shape,
      stride,
      dilation,
      pad,
      img_data,
      col_data);
}

template <>
C10_EXPORT void Col2ImNd<float, CPUContext, StorageOrder::NCHW>(
    const int N,
    const int img_size,
    const int col_size,
    const int* img_shape,
    const int* col_shape,
    const int* kernel_shape,
    const int* stride,
    const int* dilation,
    const int* pad,
    const float* col_data,
    float* img_data,
    CPUContext* /* context */) {
  Im2ColNdNCHWImpl<float, true>(
      N,
      img_size,
      col_size,
      img_shape,
      col_shape,
      kernel_shape,
      stride,
      dilation,
      pad,
      col_data,
      img_data);
}

template <>
C10_EXPORT void Im2Col<float, CPUContext, StorageOrder::NCHW>(
    const int C,
    const int H,
    const int W,
    const int kernel_h,
    const int kernel_w,
    const int dilation_h,
    const int dilation_w,
    const int pad_t,
    const int pad_l,
    const int pad_b,
    const int pad_r,
    const int stride_h,
    const int stride_w,
    const float* img_data,
    float* col_data,
    CPUContext* context,
    const int /* groups */) {
  // In NCHW, the number of groups doesn't affect Im2Col.

  // Fast path for zero padding and no dilation
  if (pad_t == 0 && pad_l == 0 && pad_b == 0 && pad_r == 0 && dilation_h == 1 &&
      dilation_w == 1) {
    Im2ColZeroPaddingAndNoDilationNCHW<float>(
        C,
        H,
        W,
        kernel_h,
        kernel_w,
        stride_h,
        stride_w,
        img_data,
        col_data,
        context);
    return;
  }

  // Baseline
  const int output_h =
      (H + pad_b + pad_t - (dilation_h * (kernel_h - 1) + 1)) / stride_h + 1;
  const int output_w =
      (W + pad_l + pad_r - (dilation_w * (kernel_w - 1) + 1)) / stride_w + 1;
  const int output_size = output_h * output_w;
  for (int c = 0; c < C; ++c) {
    for (int kh = 0; kh < kernel_h; ++kh) {
      for (int kw = 0; kw < kernel_w; ++kw) {
        for (int h = 0; h < output_h; ++h) {
          const int h_pad = h * stride_h - pad_t + kh * dilation_h;
          if (!utils::IsAGeZeroAndALtB(h_pad, H)) {
            std::memset(col_data + h * output_w, 0, output_w * sizeof(float));
            continue;
          }
          for (int w = 0; w < output_w; ++w) {
            const int w_pad = w * stride_w - pad_l + kw * dilation_w;
            col_data[h * output_w + w] = utils::IsAGeZeroAndALtB(w_pad, W)
                ? img_data[(c * H + h_pad) * W + w_pad]
                : 0;
          }
        }
        col_data += output_size;
      }
    }
  }
}

template <>
C10_EXPORT void Im2Col<float, CPUContext, StorageOrder::NHWC>(
    const int C,
    const int H,
    const int W,
    const int kernel_h,
    const int kernel_w,
    const int dilation_h,
    const int dilation_w,
    const int pad_t,
    const int pad_l,
    const int pad_b,
    const int pad_r,
    const int stride_h,
    const int stride_w,
    const float* img_data,
    float* col_data,
    CPUContext* context,
    const int groups) {
  // Fast path for zero padding and no dilation
  if (pad_t == 0 && pad_l == 0 && pad_b == 0 && pad_r == 0 && dilation_h == 1 &&
      dilation_w == 1 && groups == 1) {
    Im2ColZeroPaddingAndNoDilationNHWC<float>(
        C,
        H,
        W,
        kernel_h,
        kernel_w,
        stride_h,
        stride_w,
        img_data,
        col_data,
        context);
    return;
  }

  const int dkernel_h = dilation_h * (kernel_h - 1) + 1;
  const int dkernel_w = dilation_w * (kernel_w - 1) + 1;
  const int output_h = (H + pad_b + pad_t - dkernel_h) / stride_h + 1;
  const int output_w = (W + pad_l + pad_r - dkernel_w) / stride_w + 1;
  int h_pad = -pad_t;
  if (groups == 1) {
    for (int h = 0; h < output_h; ++h) {
      int w_pad = -pad_l;
      for (int w = 0; w < output_w; ++w) {
        for (int ih = h_pad; ih < h_pad + dkernel_h; ih += dilation_h) {
          if (!utils::IsAGeZeroAndALtB(ih, H)) {
            std::memset(col_data, 0, sizeof(float) * kernel_w * C);
            col_data += kernel_w * C;
            continue;
          }
          for (int iw = w_pad; iw < w_pad + dkernel_w; iw += dilation_w) {
            if (utils::IsAGeZeroAndALtB(iw, W)) {
              std::memcpy(
                  col_data, img_data + (ih * W + iw) * C, sizeof(float) * C);
            } else {
              std::memset(col_data, 0, sizeof(float) * C);
            }
            col_data += C;
          } // iw
        } // ih
        w_pad += stride_w;
      } // w
      h_pad += stride_h;
    } // h
  } else {
    const int C_per_G = C / groups;
    for (int h = 0; h < output_h; ++h) {
      int w_pad = -pad_l;
      for (int w = 0; w < output_w; ++w) {
        int r = 0;
        for (int ih = h_pad; ih < h_pad + dkernel_h; ih += dilation_h, ++r) {
          int s = 0;
          for (int iw = w_pad; iw < w_pad + dkernel_w; iw += dilation_w, ++s) {
            if (utils::IsAGeZeroAndALtB(ih, H) &&
                utils::IsAGeZeroAndALtB(iw, W)) {
              for (int g = 0; g < groups; ++g) {
                std::memcpy(
                    col_data + ((g * kernel_h + r) * kernel_w + s) * C_per_G,
                    img_data + (ih * W + iw) * C + g * C_per_G,
                    sizeof(float) * C_per_G);
              }
            } else {
              for (int g = 0; g < groups; ++g) {
                std::memset(
                    col_data + ((g * kernel_h + r) * kernel_w + s) * C_per_G,
                    0,
                    sizeof(float) * C_per_G);
              }
            }
          } // iw
        } // ih
        col_data += kernel_h * kernel_w * C;
        w_pad += stride_w;
      } // w
      h_pad += stride_h;
    } // h
  }
}

template <>
C10_EXPORT void Col2Im<float, CPUContext, StorageOrder::NCHW>(
    const int C,
    const int H,
    const int W,
    const int kernel_h,
    const int kernel_w,
    const int dilation_h,
    const int dilation_w,
    const int pad_t,
    const int pad_l,
    const int pad_b,
    const int pad_r,
    const int stride_h,
    const int stride_w,
    const float* col_data,
    float* img_data,
    CPUContext* context,
    const int /* groups */) {
  // In NCHW, the number of groups doesn't affect Col2Im.

  // Fast path for zero padding and no dilation
  if (pad_t == 0 && pad_l == 0 && pad_b == 0 && pad_r == 0 && dilation_h == 1 &&
      dilation_w == 1) {
    Col2ImZeroPaddingAndNoDilationNCHW<float>(
        C,
        H,
        W,
        kernel_h,
        kernel_w,
        stride_h,
        stride_w,
        col_data,
        img_data,
        context);
    return;
  }

  // Fallback
  Set<float, CPUContext>(C * H * W, 0.0f, img_data, context);
  const int output_h =
      (H + pad_t + pad_b - (dilation_h * (kernel_h - 1) + 1)) / stride_h + 1;
  const int output_w =
      (W + pad_l + pad_r - (dilation_w * (kernel_w - 1) + 1)) / stride_w + 1;
  const int output_size = output_h * output_w;
  for (int c = 0; c < C; ++c) {
    for (int kh = 0; kh < kernel_h; ++kh) {
      for (int kw = 0; kw < kernel_w; ++kw) {
        for (int h = 0; h < output_h; ++h) {
          const int h_pad = h * stride_h - pad_t + kh * dilation_h;
          if (!utils::IsAGeZeroAndALtB(h_pad, H)) {
            continue;
          }
          for (int w = 0; w < output_w; ++w) {
            const int w_pad = w * stride_w - pad_l + kw * dilation_w;
            if (utils::IsAGeZeroAndALtB(w_pad, W)) {
              img_data[(c * H + h_pad) * W + w_pad] +=
                  col_data[h * output_w + w];
            }
          }
        }
        col_data += output_size;
      }
    }
  }
}

template <>
C10_EXPORT void Col2Im<float, CPUContext, StorageOrder::NHWC>(
    const int C,
    const int H,
    const int W,
    const int kernel_h,
    const int kernel_w,
    const int dilation_h,
    const int dilation_w,
    const int pad_t,
    const int pad_l,
    const int pad_b,
    const int pad_r,
    const int stride_h,
    const int stride_w,
    const float* col_data,
    float* img_data,
    CPUContext* context,
    const int groups) {
  // Fast path for zero padding and no dilation
  if (pad_t == 0 && pad_l == 0 && pad_b == 0 && pad_r == 0 && dilation_h == 1 &&
      dilation_w == 1 && groups == 1) {
    Col2ImZeroPaddingAndNoDilationNHWC<float>(
        C,
        H,
        W,
        kernel_h,
        kernel_w,
        stride_h,
        stride_w,
        col_data,
        img_data,
        context);
    return;
  }

  Set<float, CPUContext>(H * W * C, 0, img_data, context);
  const int dkernel_h = dilation_h * (kernel_h - 1) + 1;
  const int dkernel_w = dilation_w * (kernel_w - 1) + 1;
  const int output_h = (H + pad_t + pad_b - dkernel_h) / stride_h + 1;
  const int output_w = (W + pad_l + pad_r - dkernel_w) / stride_w + 1;

  int h_pad = -pad_t;
  if (groups == 1) {
    for (int h = 0; h < output_h; ++h) {
      int w_pad = -pad_l;
      for (int w = 0; w < output_w; ++w) {
        for (int ih = h_pad; ih < h_pad + dkernel_h; ih += dilation_h) {
          if (!utils::IsAGeZeroAndALtB(ih, H)) {
            col_data += kernel_w * C;
            continue;
          }
          for (int iw = w_pad; iw < w_pad + dkernel_w; iw += dilation_w) {
            if (utils::IsAGeZeroAndALtB(iw, W)) {
              float* img_data_patch = img_data + (ih * W + iw) * C;
              Add<float, CPUContext>(
                  C, img_data_patch, col_data, img_data_patch, context);
            }
            col_data += C;
          } // iw
        } // ih
        w_pad += stride_w;
      } // w
      h_pad += stride_h;
    } // h
  } else {
    const int C_per_G = C / groups;
    for (int h = 0; h < output_h; ++h) {
      int w_pad = -pad_l;
      for (int w = 0; w < output_w; ++w) {
        int r = 0;
        for (int ih = h_pad; ih < h_pad + dkernel_h; ih += dilation_h, ++r) {
          int s = 0;
          for (int iw = w_pad; iw < w_pad + dkernel_w; iw += dilation_w, ++s) {
            if (utils::IsAGeZeroAndALtB(ih, H) &&
                utils::IsAGeZeroAndALtB(iw, W)) {
              float* img_data_patch = img_data + (ih * W + iw) * C;
              for (int g = 0; g < groups; ++g) {
                Add<float, CPUContext>(
                    C_per_G,
                    img_data_patch + g * C_per_G,
                    col_data + ((g * kernel_h + r) * kernel_w + s) * C_per_G,
                    img_data_patch + g * C_per_G,
                    context);
              }
            }
          } // iw
        } // ih
        col_data += kernel_h * kernel_w * C;
        w_pad += stride_w;
      } // w
      h_pad += stride_h;
    } // h
  }
}

template <>
C10_EXPORT void BiasCHW<float, CPUContext>(
    const float* bias,
    const float* /*bias_multiplier*/,
    const int bias_channels,
    const int image_size,
    float* image,
    CPUContext* /*context*/) {
  // Sum the per-channel bias into every image plane
  for (int c = 0; c < bias_channels; ++c) {
    float b = bias[c];

#if defined(__ARM_NEON__) || defined(__ARM_NEON)
    float32x4_t vBias = vdupq_n_f32(b);

    // We give alignment hints for additional speed, so handle the
    // non-vectorizable prologue separately
    constexpr int kVecSizeInFloat = sizeof(float32x4_t) / sizeof(float);

    // FIXME: if input < kVecSizeInFloat, can't vectorize at all

    int prologue = kVecSizeInFloat -
        // remainder in floats
        (((uintptr_t)image) % (sizeof(float32x4_t))) / sizeof(float);

    int i = 0;
    // Prologue loop
    for (; i < prologue; ++i) {
      image[i] += b;
    }

    // The loop is manually unrolled by 8
    constexpr int kUnroll = 8;
    constexpr int kFloatsPerLoop = kUnroll * kVecSizeInFloat;

    int remainder = image_size - prologue;
    int vectorizable = prologue + (remainder / kFloatsPerLoop) * kFloatsPerLoop;

    // Vectorizable body
    for (; i < vectorizable; i += kFloatsPerLoop) {
      // Manually unrolled
      float32x4_t v0 = vld1q_f32_aligned(image + i + 0);
      float32x4_t v1 = vld1q_f32_aligned(image + i + 4);
      float32x4_t v2 = vld1q_f32_aligned(image + i + 8);
      float32x4_t v3 = vld1q_f32_aligned(image + i + 12);
      float32x4_t v4 = vld1q_f32_aligned(image + i + 16);
      float32x4_t v5 = vld1q_f32_aligned(image + i + 20);
      float32x4_t v6 = vld1q_f32_aligned(image + i + 24);
      float32x4_t v7 = vld1q_f32_aligned(image + i + 28);

      v0 = vaddq_f32(v0, vBias);
      v1 = vaddq_f32(v1, vBias);
      v2 = vaddq_f32(v2, vBias);
      v3 = vaddq_f32(v3, vBias);
      v4 = vaddq_f32(v4, vBias);
      v5 = vaddq_f32(v5, vBias);
      v6 = vaddq_f32(v6, vBias);
      v7 = vaddq_f32(v7, vBias);

      vst1q_f32_aligned(image + i + 0, v0);
      vst1q_f32_aligned(image + i + 4, v1);
      vst1q_f32_aligned(image + i + 8, v2);
      vst1q_f32_aligned(image + i + 12, v3);
      vst1q_f32_aligned(image + i + 16, v4);
      vst1q_f32_aligned(image + i + 20, v5);
      vst1q_f32_aligned(image + i + 24, v6);
      vst1q_f32_aligned(image + i + 28, v7);
    }

    // Non-vectorizable epilogue
    for (; i < image_size; ++i) {
      image[i] += b;
    }
#else
    // Non-NEON CPU implementation
    for (int i = 0; i < image_size; ++i) {
      image[i] += b;
    }
#endif // defined(__ARM_NEON__) || defined(__ARM_NEON)

    image += image_size;
  }
}

#define CAFFE2_SPECIALIZED_COPYVECTOR(T)                            \
  template <>                                                       \
  C10_EXPORT void CopyVector<T, CPUContext>(                        \
      const int N, const T* src, T* dst, CPUContext* /*context*/) { \
    if (src != dst && N > 0) {                                      \
      memcpy(dst, src, sizeof(T) * N);                              \
    }                                                               \
  }
CAFFE2_SPECIALIZED_COPYVECTOR(float)
#undef CAFFE2_SPECIALIZED_COPYVECTOR

namespace {

#ifdef CAFFE2_USE_HPTT

bool TransposeWithHPTT(
    const int ndim,
    const int* dims,
    const int* axes,
    const float* X,
    float* Y) {
  std::vector<int> axes_cm(ndim);
  std::vector<int> dims_cm(ndim);
  // Convert row-major index to column-major.
  const auto cm_fn = [ndim](const int i) { return ndim - i - 1; };
  for (int i = 0; i < ndim; ++i) {
    axes_cm[i] = cm_fn(axes[cm_fn(i)]);
    dims_cm[i] = dims[cm_fn(i)];
  }

  // HPTT doesn't handle 0 sized inputs.
  for (auto dim : dims_cm) {
    if (dim <= 0) {
      return false;
    }
  }
  auto plan = hptt::create_plan(
      axes_cm.data(),
      ndim,
      1.0,
      X,
      dims_cm.data(),
      nullptr,
      0.0,
      Y,
      nullptr,
      hptt::ESTIMATE,
      1);
  if (plan == nullptr) {
    return false;
  }
  plan->execute();
  return true;
}

#endif // CAFFE2_USE_HPTT

template <typename T>
void Transpose2D(const int rows, const int cols, const T* X, T* Y);

#ifdef CAFFE2_USE_MKL

#define DELEGATE_TRANSPOSE_2D_FUNCTION(T, Func)                          \
  template <>                                                            \
  void Transpose2D<T>(const int rows, const int cols, const T* X, T* Y) { \
    Func('R', 'T', rows, cols, T(1), X, cols, Y, rows);                  \
  }
DELEGATE_TRANSPOSE_2D_FUNCTION(float, mkl_somatcopy);
DELEGATE_TRANSPOSE_2D_FUNCTION(double, mkl_domatcopy);
#undef DELEGATE_TRANSPOSE_2D_FUNCTION

#endif // CAFFE2_USE_MKL

#define CAFFE2_SPECIALIZED_TRANSPOSE_2D(T)                               \
  template <>                                                            \
  void Transpose2D<T>(const int rows, const int cols, const T* X, T* Y) { \
    EigenMatrixMap<T>(Y, rows, cols) =                                   \
        ConstEigenMatrixMap<T>(X, cols, rows).transpose();               \
  }

#ifndef CAFFE2_USE_MKL

template <>
void Transpose2D<float>(
    const int rows,
    const int cols,
    const float* X,
    float* Y) {
#ifdef CAFFE2_USE_HPTT
  const std::array<int, 2> dims = {rows, cols};
  const std::array<int, 2> axes = {1, 0};
  if (TransposeWithHPTT(2, dims.data(), axes.data(), X, Y)) {
    return;
  }
#endif // CAFFE2_USE_HPTT
  EigenMatrixMap<float>(Y, rows, cols) =
      ConstEigenMatrixMap<float>(X, cols, rows).transpose();
}

CAFFE2_SPECIALIZED_TRANSPOSE_2D(double)

#endif // CAFFE2_USE_MKL

CAFFE2_SPECIALIZED_TRANSPOSE_2D(int)
CAFFE2_SPECIALIZED_TRANSPOSE_2D(int64_t)
#ifdef CAFFE2_UNIQUE_LONG_TYPEMETA
CAFFE2_SPECIALIZED_TRANSPOSE_2D(long)
#endif
CAFFE2_SPECIALIZED_TRANSPOSE_2D(std::uint8_t)
CAFFE2_SPECIALIZED_TRANSPOSE_2D(std::uint16_t)

#undef CAFFE2_SPECIALIZED_TRANSPOSE_2D

std::vector<int>
ComputeXStrides(const int ndim, const int* dims, const int* axes) {
  std::vector<int> x_strides(ndim);
  std::vector<int> buff(ndim);
  int cur_stride = 1;
  for (int i = ndim - 1; i >= 0; --i) {
    buff[i] = cur_stride;
    cur_stride *= dims[i];
  }
  for (int i = 0; i < ndim; ++i) {
    x_strides[i] = buff[axes[i]];
  }
  return x_strides;
}

template <typename T>
void TransposeND(
    const int ndim,
    const int* dims,
    const int* axes,
    const T* X,
    T* Y) {
  std::vector<int> Y_dims(ndim);
  for (int i = 0; i < ndim; ++i) {
    Y_dims[i] = dims[axes[i]];
  }
  // Measure amount of contiguous data we can copy at once
  int block_size = 1;
  int num_shared_idx = 0;
  for (int i = ndim - 1; i >= 0 && axes[i] == i; --i) {
    block_size *= Y_dims[i];
    ++num_shared_idx;
  }
  const int itr_axes = ndim - num_shared_idx;
  const int num_blocks = std::accumulate(
      Y_dims.cbegin(), Y_dims.cbegin() + itr_axes, 1, std::multiplies<int>());
  const std::vector<int> X_strides = ComputeXStrides(itr_axes, dims, axes);
  std::vector<int> index(itr_axes, 0);
  for (int Y_index = 0; Y_index < num_blocks; ++Y_index) {
    const int X_index = std::inner_product(
        X_strides.cbegin(), X_strides.cend(), index.cbegin(), 0);
    if (block_size == 1) {
      Y[Y_index] = X[X_index];
    } else {
      std::memcpy(
          Y + block_size * Y_index,
          X + block_size * X_index,
          block_size * sizeof(T));
    }
    utils::IncreaseIndexInDims(itr_axes, Y_dims.data(), index.data());
  }
}

template <typename T>
void TransposeCPUImpl(
    const int ndim,
    const int* dims,
    const int* axes,
    const T* X,
    T* Y) {
  if (utils::IsIdentityPermutation(ndim, axes)) {
    const int size =
        std::accumulate(dims, dims + ndim, 1, std::multiplies<int>());
    std::memcpy(Y, X, size * sizeof(T));
    return;
  }
  if (ndim == 2) {
    Transpose2D<T>(dims[0], dims[1], X, Y);
  } else {
    TransposeND<T>(ndim, dims, axes, X, Y);
  }
}

template <>
void TransposeCPUImpl(
    const int ndim,
    const int* dims,
    const int* axes,
    const float* X,
    float* Y) {
  if (utils::IsIdentityPermutation(ndim, axes)) {
    const int size =
        std::accumulate(dims, dims + ndim, 1, std::multiplies<int>());
    std::memcpy(Y, X, size * sizeof(float));
    return;
  }
  if (ndim == 2) {
    Transpose2D<float>(dims[0], dims[1], X, Y);
  } else {
#ifdef CAFFE2_USE_HPTT
    if (TransposeWithHPTT(ndim, dims, axes, X, Y)) {
      return;
    }
#endif
    TransposeND<float>(ndim, dims, axes, X, Y);
  }
}

} // namespace

#define CAFFE2_SPECIALIZED_TRANSPOSE(T)       \
  template <>                                 \
  C10_EXPORT void Transpose<T, CPUContext>(   \
      const int ndim,                         \
      const int* dims,                        \
      const int* axes,                        \
      const T* X,                             \
      T* Y,                                   \
      CPUContext* /* context */) {            \
    TransposeCPUImpl(ndim, dims, axes, X, Y); \
  }
CAFFE2_SPECIALIZED_TRANSPOSE(float)
CAFFE2_SPECIALIZED_TRANSPOSE(double)
CAFFE2_SPECIALIZED_TRANSPOSE(int)
CAFFE2_SPECIALIZED_TRANSPOSE(int64_t)
#ifdef CAFFE2_UNIQUE_LONG_TYPEMETA
CAFFE2_SPECIALIZED_TRANSPOSE(long)
#endif
CAFFE2_SPECIALIZED_TRANSPOSE(std::uint8_t)
CAFFE2_SPECIALIZED_TRANSPOSE(std::uint16_t)
#undef CAFFE2_SPECIALIZED_TRANSPOSE

#define CAFFE2_SPECIALIZED_AFFINE_CHANNEL(T)                \
  template <>                                               \
  void AffineChannel<T, CPUContext, StorageOrder::NCHW>(    \
      const int N,                                          \
      const int C,                                          \
      const int HxW,                                        \
      const T* X,                                           \
      const T* scale,                                       \
      const T* bias,                                        \
      T* Y,                                                 \
      CPUContext* /* context */) {                          \
    ConstEigenVectorArrayMap<T> scale_arr(scale, C);        \
    ConstEigenVectorArrayMap<T> bias_arr(bias, C);          \
    const int stride = C * HxW;                             \
    const T* X_ptr = X;                                     \
    T* Y_ptr = Y;                                           \
    for (int i = 0; i < N; ++i) {                           \
      EigenArrayMap<T>(Y_ptr, HxW, C) =                     \
          (ConstEigenArrayMap<T>(X_ptr, HxW, C).rowwise() * \
           scale_arr.transpose())                           \
              .rowwise() +                                  \
          bias_arr.transpose();                             \
      X_ptr += stride;                                      \
      Y_ptr += stride;                                      \
    }                                                       \
  }                                                         \
  template <>                                               \
  void AffineChannel<T, CPUContext, StorageOrder::NHWC>(    \
      const int N,                                          \
      const int C,                                          \
      const int HxW,                                        \
      const T* X,                                           \
      const T* scale,                                       \
      const T* bias,                                        \
      T* Y,                                                 \
      CPUContext* /* context */) {                          \
    EigenArrayMap<T>(Y, C, N * HxW) =                       \
        (ConstEigenArrayMap<T>(X, C, N * HxW).colwise() *   \
         ConstEigenVectorArrayMap<T>(scale, C))             \
            .colwise() +                                    \
        ConstEigenVectorArrayMap<T>(bias, C);               \
  }
CAFFE2_SPECIALIZED_AFFINE_CHANNEL(float)
#undef CAFFE2_SPECIALIZED_AFFINE_CHANNEL

} // namespace math
} // namespace caffe2
