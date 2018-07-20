#pragma once

// ${generated_comment}

$th_headers

#include "ATen/Tensor.h"
#include "ATen/TensorImpl.h"
#include "ATen/TensorMethods.h"

namespace at {

struct ${Tensor} final : public TensorImpl {
public:
  ${Tensor}(THTensor * tensor);
  virtual ~${Tensor}();
  virtual const char * toString() const override;
  virtual IntList sizes() const override;
  virtual IntList strides() const override;
  virtual int64_t dim() const override;
  virtual Scalar localScalar() override;
  virtual void * unsafeGetTH(bool retain) override;
  virtual std::unique_ptr<Storage> storage() override;
  virtual void release_resources() override;
  static const char * typeString();

  THTensor * tensor;
};

} // namespace at
