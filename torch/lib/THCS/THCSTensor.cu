#include "THCSTensor.h"
#include "THCApply.cuh"
#include "THCTensorMathPointwise.cuh"
#include "stdio.h"

template <typename IndexType, typename Real, typename Op>
__device__ void applyOp2(
    Op op, IndexType blockSize,
    TensorInfo<Real, IndexType> values1, IndexType idx1,
    TensorInfo<Real, IndexType> values2, IndexType idx2) {
  for (IndexType k = blockIdx.x * blockDim.x + threadIdx.x;
       k < blockSize;
       k += gridDim.x * blockDim.x) {
    op(values1.data + idx1 * blockSize + k, values2.data + idx2 * blockSize + k);
  }
}

template <typename IndexType, typename Real, typename Op>
__device__ void applyOp3(
    Op op, IndexType blockSize,
    TensorInfo<Real, IndexType> values1, IndexType idx1,
    TensorInfo<Real, IndexType> values2, IndexType idx2,
    TensorInfo<Real, IndexType> values3, IndexType idx3) {
  for (IndexType k = blockIdx.x * blockDim.x + threadIdx.x;
       k < blockSize;
       k += gridDim.x * blockDim.x) {
    op(values1.data + idx1 * blockSize + k,
       values2.data + idx2 * blockSize + k,
       values3.data + idx3 * blockSize + k);
  }
}

template <typename Op, typename IndexType, typename Real>
__global__ void THCSTensor_sparseElementwiseKernel(
    Op op,
    TensorInfo<Real, IndexType> dense,
    TensorInfo<integer, IndexType> indices,
    TensorInfo<Real, IndexType> values,
    const IndexType nnz) {
  IndexType indskip = indices.strides[0];
  IndexType valueSize = values.strides[0];
  for (IndexType linearId = blockIdx.x;
       linearId < nnz;
       linearId += gridDim.x) {
    IndexType index = 0;
    for (IndexType d = 0; d < indices.sizes[0]; d++) {
      index = dense.sizes[d] * index + indices.data[d * indskip + linearId];
    }
    Real *dst = dense.data + index * valueSize;
    Real *src = values.data + linearId * valueSize;
    for (IndexType linearId2 = threadIdx.x; linearId2 < valueSize; linearId2 += blockDim.x) {
      op(dst + linearId2, src + linearId2);
    }
  }
}

template <typename Op, typename IndexType, typename Real>
__global__ void THCSTensor_sparseElementwiseKernelScalar(
    Op op,
    TensorInfo<Real, IndexType> dense,
    TensorInfo<integer, IndexType> indices,
    TensorInfo<Real, IndexType> values,
    const IndexType nnz) {
  IndexType indskip = indices.strides[0];
  for (IndexType linearId = blockIdx.x * blockDim.x + threadIdx.x;
       linearId < nnz;
       linearId += gridDim.x * blockDim.x) {
    IndexType index = 0;
    for (IndexType d = 0; d < indices.sizes[0]; d++) {
      index = dense.sizes[d] * index + indices.data[d * indskip + linearId];
    }
    op(dense.data + index, values.data + linearId);
  }
}

template <typename OpBoth, typename OpLeft, typename OpRight, typename IndexType, typename Real>
__global__ void THCSTensor_valueSparseUnionKernel(
    OpBoth opBoth,
    OpLeft opLeft,
    OpRight opRight,
    TensorInfo<integer, IndexType> r_indices,
    TensorInfo<integer, IndexType> t_indices,
    TensorInfo<integer, IndexType> s_indices,
    TensorInfo<Real, IndexType> r_values,
    TensorInfo<Real, IndexType> t_values,
    TensorInfo<Real, IndexType> s_values,
    const IndexType t_nnz, const IndexType s_nnz) {
  IndexType t_indskip = t_indices.strides[0];
  IndexType s_indskip = s_indices.strides[0];
  long cmp, d;
  long nDimI = r_indices.sizes[0];
  IndexType valueSize = r_values.strides[0];
  IndexType r_i = 0, t_i = 0, s_i = 0;
  while (t_i < t_nnz || s_i < s_nnz) {
    if (t_i >= t_nnz) {
      cmp = -1;
    } else if (s_i >= s_nnz) {
      cmp = 1;
    } else {
      cmp = 0;
      for (d = 0; d < nDimI; d++) {
        if (t_indices.data[d * t_indskip + t_i] < s_indices.data[d * s_indskip + s_i]) {
          cmp = 1;
          break;
        }
        if (t_indices.data[d * t_indskip + t_i] > s_indices.data[d * s_indskip + s_i]) {
          cmp = -1;
          break;
        }
      }
    }
    if (cmp == 0) applyOp3(opBoth, valueSize, r_values, r_i, t_values, t_i++, s_values, s_i++);
    else if (cmp > 0) applyOp2(opLeft, valueSize, r_values, r_i, t_values, t_i++);
    else if (cmp < 0) applyOp2(opRight, valueSize, r_values, r_i, s_values, s_i++);
    r_i++;
  }
}

// TODO find a way to parallelize this...
template <typename IndexType, typename Real>
__global__ void THCSTensor_indexSparseUnionKernel(
    TensorInfo<integer, IndexType> r_indices,
    TensorInfo<integer, IndexType> t_indices,
    TensorInfo<integer, IndexType> s_indices,
    const IndexType t_nnz, const IndexType s_nnz, IndexType *resultNnz) {
  IndexType r_indskip = r_indices.strides[0];
  IndexType t_indskip = t_indices.strides[0];
  IndexType s_indskip = s_indices.strides[0];
  long cmp, d;
  long nDimI = r_indices.sizes[0];
  IndexType r_i = 0, t_i = 0, s_i = 0;
  while (t_i < t_nnz || s_i < s_nnz) {
    if (t_i >= t_nnz) {
      cmp = -1;
    } else if (s_i >= s_nnz) {
      cmp = 1;
    } else {
      cmp = 0;
      for (d = 0; d < nDimI; d++) {
        if (t_indices.data[d * t_indskip + t_i] < s_indices.data[d * s_indskip + s_i]) {
          cmp = 1;
          break;
        }
        if (t_indices.data[d * t_indskip + t_i] > s_indices.data[d * s_indskip + s_i]) {
          cmp = -1;
          break;
        }
      }
    }
    if (cmp >= 0) {
      for (d = 0; d < nDimI; d++) {
        r_indices.data[d * r_indskip + r_i] = t_indices.data[d * t_indskip + t_i];
      }
      t_i++;
    }
    if (cmp <= 0) {
      for (d = 0; d < nDimI; d++) {
        r_indices.data[d * r_indskip + r_i] = s_indices.data[d * s_indskip + s_i];
      }
      s_i++;
    }
    r_i++;
  }
  *resultNnz = r_i;
}

template <typename Op, typename IndexType, typename Real>
__global__ void THCSTensor_valueSparseIntersectionKernel(
    Op op,
    TensorInfo<integer, IndexType> r_indices,
    TensorInfo<integer, IndexType> t_indices,
    TensorInfo<integer, IndexType> s_indices,
    TensorInfo<Real, IndexType> r_values,
    TensorInfo<Real, IndexType> t_values,
    TensorInfo<Real, IndexType> s_values,
    const IndexType t_nnz, const IndexType s_nnz) {
  IndexType t_indskip = t_indices.strides[0];
  IndexType s_indskip = s_indices.strides[0];
  long match, d;
  long nDimI = r_indices.sizes[0];
  IndexType valueSize = r_values.strides[0];
  IndexType r_i = 0, t_i = 0, s_i = 0;
  while (t_i < t_nnz && s_i < s_nnz) {
    match = 1;
    for (d = 0; d < nDimI; d++) {
      if (t_indices.data[d * t_indskip + t_i] < s_indices.data[d * s_indskip + s_i]) {
        t_i++;
        match = 0;
        break;
      }
      if (t_indices.data[d * t_indskip + t_i] > s_indices.data[d * s_indskip + s_i]) {
        s_i++;
        match = 0;
        break;
      }
    }
    if (!match) continue;
    applyOp3(op, valueSize, r_values, r_i++, t_values, t_i++, s_values, s_i++);
  }
}

// TODO find a way to parallelize this...
template <typename IndexType, typename Real>
__global__ void THCSTensor_indexSparseIntersectionKernel(
    TensorInfo<integer, IndexType> r_indices,
    TensorInfo<integer, IndexType> t_indices,
    TensorInfo<integer, IndexType> s_indices,
    const IndexType t_nnz, const IndexType s_nnz, IndexType *resultNnz) {
  IndexType r_indskip = r_indices.strides[0];
  IndexType t_indskip = t_indices.strides[0];
  IndexType s_indskip = s_indices.strides[0];
  long match, d;
  long nDimI = r_indices.sizes[0];
  IndexType r_i = 0, t_i = 0, s_i = 0;
  while (t_i < t_nnz && s_i < s_nnz) {
    match = 1;
    for (d = 0; d < nDimI; d++) {
      if (t_indices.data[d * t_indskip + t_i] < s_indices.data[d * s_indskip + s_i]) {
        t_i++;
        match = 0;
        break;
      }
      if (t_indices.data[d * t_indskip + t_i] > s_indices.data[d * s_indskip + s_i]) {
        s_i++;
        match = 0;
        break;
      }
    }
    if (!match) continue;
    for (d = 0; d < nDimI; d++) {
      r_indices.data[d * r_indskip + r_i] = t_indices.data[d * t_indskip + t_i];
    }
    r_i++; t_i++; s_i++;
  }
  *resultNnz = r_i;
}

#include "generic/THCSTensor.cu"
#include "THCSGenerateAllTypes.h"

#include "generic/THCSTensorMath.cu"
#include "THCSGenerateAllTypes.h"
