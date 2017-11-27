#pragma once

#include "ATen/Tensor.h"
#include "ATen/TensorGeometry.h"
#include "ATen/Utils.h"

// This file contains utility functions for checking that arguments
// make sense.  This is particularly useful for native functions,
// which do NO argument checking by default.
//
// It's NOT in Utils.h, because this file has a dep on Tensor.h

namespace at {

struct TensorArg {
  Tensor tensor;
  const char* name;
  int pos; // 1-indexed
  TensorArg(Tensor tensor, const char* name, int pos)
    : tensor(std::move(tensor)), name(name), pos(pos) {}
  const Tensor* operator->() const { return &tensor; }
  const Tensor& operator*() const { return tensor; }
};

struct TensorGeometryArg {
  TensorGeometry tensor;
  const char* name;
  int pos; // 1-indexed
  /* implicit */ TensorGeometryArg(TensorArg arg)
    : tensor(TensorGeometry{arg.tensor}), name(arg.name), pos(arg.pos) {}
  TensorGeometryArg(TensorGeometry tensor, const char* name, int pos)
    : tensor(tensor), name(name), pos(pos) {}
  const TensorGeometry* operator->() const { return &tensor; }
  const TensorGeometry& operator*() const { return tensor; }
};

// A string describing which function did checks on its input
// arguments.
// TODO: Consider generalizing this into a call stack.
using CheckedFrom = const char*;

// The undefined convention: singular operators assume their arguments
// are defined, but functions which take multiple tensors will
// implicitly filter out undefined tensors (to make it easier to perform
// tests which should apply if the tensor is defined, and should not
// otherwise.)
//
// NB: This means that the n-ary operators take lists of TensorArg,
// not TensorGeometryArg, because the Tensor to TensorGeometry
// conversion will blow up if you have undefined tensors.

std::ostream& operator<<(std::ostream & out, TensorGeometryArg t);
void checkDim(CheckedFrom c, TensorGeometryArg t, int64_t dim);
// NB: this is an inclusive-exclusive range
void checkDimRange(CheckedFrom c, TensorGeometryArg t, int64_t dim_start, int64_t dim_end);
void checkSameDim(CheckedFrom c, TensorGeometryArg t1, TensorGeometryArg t2);
void checkContiguous(CheckedFrom c, at::ArrayRef<TensorArg> ts);
void checkSize(CheckedFrom c, TensorGeometryArg t, IntList sizes);
void checkSize(CheckedFrom c, TensorGeometryArg t, int64_t dim, int64_t size);
void checkNumel(CheckedFrom c, TensorGeometryArg t, int64_t numel);
void checkSameNumel(CheckedFrom c, TensorGeometryArg t1, TensorGeometryArg t2);
void checkSameNumel(CheckedFrom c, ArrayRef<TensorArg> tensors);
void checkScalarType(CheckedFrom c, TensorArg t, ScalarType s);
void checkSameGPU(CheckedFrom c, TensorArg t1, TensorArg t2);
void checkSameGPU(CheckedFrom c, ArrayRef<TensorArg> tensors);
void checkSameType(CheckedFrom c, TensorArg t1, TensorArg t2);
void checkSameType(CheckedFrom c, ArrayRef<TensorArg> tensors);
void checkSameSize(CheckedFrom c, TensorGeometryArg t1, TensorGeometryArg t2);
void checkDefined(CheckedFrom c, at::ArrayRef<TensorArg> t);

}
