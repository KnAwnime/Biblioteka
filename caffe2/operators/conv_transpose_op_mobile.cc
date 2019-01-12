#include "caffe2/operators/conv_transpose_op_mobile.h"
#include "caffe2/operators/conv_transpose_op_mobile_impl.h"

namespace caffe2 {

#ifndef C10_MOBILE
#error "mobile build state not defined"
#endif

#if C10_MOBILE
// mobile-only implementation (tiled + vectorized + multithreaded)
REGISTER_CPU_OPERATOR_WITH_ENGINE(
    ConvTranspose,
    MOBILE,
    ConvTransposeMobileOp<float, CPUContext>);
#endif

} // namespace caffe2
