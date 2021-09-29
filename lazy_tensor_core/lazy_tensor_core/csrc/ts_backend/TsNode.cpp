#include "lazy_tensor_core/csrc/ts_backend/TsNode.h"

#include "lazy_tensors/computation_client/sys_util.h"

namespace torch_lazy_tensors {
namespace ir {

TsNode::TsNode(OpKind op, OpList operands, lazy_tensors::Shape shape,
               size_t num_outputs, torch::lazy::hash_t hash_seed)
    : Node(op, operands, shape, num_outputs, hash_seed), shape_(shape) {}

TsNode::TsNode(OpKind op, OpList operands,
               const std::function<lazy_tensors::Shape()>& shape_fn,
               size_t num_outputs, torch::lazy::hash_t hash_seed)
    : Node(op, operands, shape_fn, num_outputs, hash_seed),
      shape_(lazy_tensors::Shape()) {
  shape_ = TsGetOpShape(shape_fn);
}

TsNode::TsNode(OpKind op, OpList operands, size_t num_outputs,
               torch::lazy::hash_t hash_seed)
    : Node(op, operands, num_outputs, hash_seed),
      shape_(lazy_tensors::Shape()) {}

void TsNode::SetShapeDeferred(
    const std::function<lazy_tensors::Shape()>& shape_fn) {
  shape_ = TsGetOpShape(shape_fn);
}

TsNode::TsNode(OpKind op, lazy_tensors::Shape shape, size_t num_outputs,
               torch::lazy::hash_t hash_seed)
    : Node(op, shape, num_outputs, hash_seed), shape_(shape) {}

const lazy_tensors::Shape& TsNode::shape() const {
  return shape_;
}

const lazy_tensors::Shape& TsNode::shape(size_t output_index) const {
  if (shape_.IsTuple()) {
    return shape_.tuple_shapes(output_index);
  }
  LTC_CHECK_EQ(output_index, 0);
  return shape_;
}

using ShapeCache =
    lazy_tensors::util::Cache<torch::lazy::hash_t, lazy_tensors::Shape,
                              lazy_tensors::util::HashReducer>;

ShapeCache* GetShapeCache() {
  static lazy_tensors::int64 shape_cache_size =
      lazy_tensors::sys_util::GetEnvInt("LTC_IR_SHAPE_CACHE_SIZE", 4096);
  static ShapeCache* cache = new ShapeCache(shape_cache_size);
  return cache;
}

lazy_tensors::Shape TsNode::TsGetOpShape(
    const std::function<lazy_tensors::Shape()>& shape_fn) const {
  ShapeCache* shape_cache = GetShapeCache();
  auto shape = shape_cache->Get(hash());
  if (shape == nullptr) {
    shape = shape_cache->Add(hash(),
                             std::make_shared<lazy_tensors::Shape>(shape_fn()));
  }
  return *shape;
}

}  // namespace ir
}  // namespace torch_lazy_tensors