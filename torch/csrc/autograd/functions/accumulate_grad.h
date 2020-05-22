#pragma once

#include <torch/csrc/autograd/function.h>
#include <torch/csrc/autograd/variable.h>
#include <torch/csrc/WindowsTorchApiMacro.h>

#include <mutex>

namespace torch { namespace autograd {

struct TORCH_API AccumulateGrad : public Node {
  explicit AccumulateGrad(Variable variable_);

  variable_list apply(variable_list&& grads) override;

  static at::Tensor callHooks(
      const Variable& variable,
      at::Tensor new_grad) {
    for (auto& hook : impl::hooks(variable)) {
      new_grad = (*hook)({new_grad})[0];
    }
    return new_grad;
  }

  // Given a variable with its current grad as variable_grad, accumulates
  // new_grad into variable_grad if in place accumulation is possible.
  // Otherwise, uses 'update_grad' to update the grad for the variable.

  // "Gradient Layout Contract"
  //
  // AccumulateGrad tries to stash strided (non-sparse) grads with memory layout
  // (strides) such that variables and grads interact efficiently in later
  // optimizer kernels, and grads interact efficiently with c10d::Reducer.cpp.
  //
  // Specifically, AccumulateGrad tries to ensure the following:
  //   (1) if variable.is_non_overlapping_and_dense(), the stashed grad's
  //       strides match variable.
  //   (2) else, stashed grad is rowmajor contiguous.
  static bool obeys_layout_contract(const at::Tensor& grad, const at::Tensor& variable) {
    TORCH_INTERNAL_ASSERT(!grad.is_sparse());
    TORCH_INTERNAL_ASSERT(!variable.is_sparse());
    // I want to discuss how this logic plays with broadcasted (stride-0) tensors.
    // It's not uncommon grad is broadcasted, eg from sum() backward.
    // I want to make sure it doesn't regress existing performance for any cases.
    return variable.is_non_overlapping_and_dense() ?
           (grad.strides() == variable.strides()) :
           grad.is_contiguous(at::MemoryFormat::Contiguous);
  }
  // If variable's grad does not exist (!variable_grad.defined())
  // AccumulateGrad steals new_grad if it's stealable and obeys the contract
  // already, otherwise it deep copies new_grad into an obedient clone.
  //
  // If variable's grad already exists (variable_grad.defined()), new_grad must
  // be added to variable_grad.  If we aren't setting up for double backward
  // (!GradMode::is_enabled()), AccumulateGrad performs "variable_grad += new_grad"
  // in-place, which keeps variable_grad's layout. We assume (hope) variable_grad
  // was created obeying (1) or (2) at some point in the past.
  //
  // If we are setting up for double backward, AccumulateGrad adds new_grad to
  // variable_grad out-of-place using operator+.  TensorIterator decides the
  // result's layout, typically matching strides of the first arg.
  // AccumulateGrad leverages this as best it can, stashing
  // "new_grad + variable_grad" if new_grad obeys, and
  // "variable_grad + new_grad" otherwise.
  //
  // AccumulateGrad does not enforce the contract with 100% certainty.  Examples:
  //  - If a user manually permutes a param or its grad, then runs a fwd+bwd,
  //    variable_grad += new_grad keeps variable_grad's layout without rechecking
  //    the contract.
  //  - If TensorIterator changes its corner cases about operator+'s result
  //    (for example, giving more or less priority to channels_last inputs, see
  //    https://github.com/pytorch/pytorch/pull/37968) the result may not obey.
  //
  // Fortunately, if a given grad doesn't satisfy (1) or (2), the penalty is
  // degraded performance in Reducer.cpp or optimizer kernels, not death by
  // assert or silently bad numerics.

  // variable: the variable whose grad we're accumulating.
  // variable_grad: the current grad for the variable.
  // new_grad: new grad we want to acummulate for the variable.
  // num_expected_refs: the number of refs we expect to hold internally
  //                    such that it is safe to avoid cloning the grad
  //                    if use_count() of the grad is less than or equal
  //                    to this value (in addition to post_hooks).
  // update_grad: Function that is used to update grad for the variable.
  //              The argument to the function is a Tensor which
  //              is used to set a new value for the grad.
  template <typename T>
  static void accumulateGrad(
      const Variable& variable,
      at::Tensor& variable_grad,
      const at::Tensor& new_grad,
      size_t num_expected_refs,
      const T& update_grad) {
    if (!variable_grad.defined()) {
      if (!GradMode::is_enabled() &&
          !new_grad.is_sparse() &&
          new_grad.use_count() <= num_expected_refs &&
          obeys_layout_contract(new_grad, variable)) {
        // we aren't setting up for double-backward
        // not sparse
        // no other user-visible tensor references new_grad
        // new_grad obeys the "Gradient Layout Contract"
        // Under these conditions, we can steal new_grad without a deep copy.
        update_grad(new_grad.detach());
      } else if (
          !GradMode::is_enabled() && new_grad.is_sparse() &&
          new_grad._indices().is_contiguous() &&
          new_grad._values().is_contiguous() &&
          // Use count for indices and values should always be <=1 since the
          // SparseTensor should be the only one holding a reference to these.
          new_grad._indices().use_count() <= 1 &&
          new_grad._values().use_count() <= 1 &&
          new_grad.use_count() <= num_expected_refs) {
        // Can't detach sparse tensor (since metadata changes are not allowed
        // after detach), so just create a new one for the grad which is a
        // shallow copy. We need a shallow copy so that modifying the original
        // grad tensor doesn't modify the grad we accumulate.
        // We only skip clone if indices and values themselves are contiguous
        // for backward compatiblity reasons. Since without this optimization,
        // earlier we would clone the entire SparseTensor which cloned indices
        // and values.
        // For details see https://github.com/pytorch/pytorch/issues/34375.
        update_grad(at::_sparse_coo_tensor_unsafe(
            new_grad._indices(),
            new_grad._values(),
            new_grad.sizes(),
            new_grad.options()));
      } else {
        if (new_grad.is_sparse()) {
          update_grad(new_grad.clone());
        } else {
          // Deep copies new_grad according to the "Gradient Layout Contract."
          if (variable.is_non_overlapping_and_dense()) {
            // (1)
            if (variable.strides() == new_grad.strides()) {
              update_grad(new_grad.clone(at::MemoryFormat::Preserve));
            } else {
              // Does this dicey-looking sequence attach updated grad to new_grad's
              // history if GradMode::is_enabled()?  Yes, and @alband says it should.
              update_grad(std::move(at::empty_strided(variable.sizes(), variable.strides(),
                                    variable.options().memory_format(c10::nullopt))
                                    .copy_(new_grad)));
            }
          } else {
            // (2)
            update_grad(new_grad.clone(at::MemoryFormat::Contiguous));
          }
        }
      }
    } else if (!GradMode::is_enabled()) {
      // This case is not strictly necessary, but it makes the first-order only
      // case slightly more efficient.
      if (variable_grad.is_sparse() && !new_grad.is_sparse()) {
        // If `variable_grad` is sparse and `new_grad` is not sparse, their
        // sum is not sparse, and we must change the TensorImpl type of
        // `variable_grad` for it to store the result. However, changing the
        // TensorImpl type of a tensor requires changing the tensor itself, and
        // thus in this case we have to change the grad tensor.
        update_grad(new_grad + variable_grad);
      } else {
        // In this case we can avoid changing the grad tensor. There are three
        // scenarios when we'll hit this case:
        //
        // 1. `variable_grad` is sparse, and `new_grad` is sparse.
        // 2. `variable_grad` is dense, and `new_grad` is sparse.
        // 3. `variable_grad` is dense, and `new_grad` is dense.
        //
        // In all of these three cases, `variable_grad += new_grad` is a
        // valid operation which adds `new_grad` to `variable_grad` in
        // place. `variable_grad` is thus still referring to the same tensor
        // after the operation.
        variable_grad += new_grad;
        // ^ We could enforce the contract more aggressively here by writing:
        // if (variable_grad.is_sparse() || new_grad.is_sparse()) {
        //   variable_grad += new_grad;
        // } else if (obeys_layout_contract(variable_grad, variable)) {
        //   variable_grad += new_grad;
        // } else {
        //   result = at::empty_strided(variable.sizes(), variable.strides(),
        //                              variable.options().memory_format(c10::nullopt));
        //   update_grad(at::native::add_out(result, variable_grad, new_grad, 1.0);
        // }
        // However, that accumulation is sometimes in place and sometimes not,
        // which may break user code.
      }
    } else {
      // Assumes operator+ result typically matches strides of first arg.
      if (obeys_layout_contract(new_grad, variable)) {
        update_grad(new_grad + variable_grad);
      } else {
        update_grad(variable_grad + new_grad);
      }
      // ^ We could enforce the contract more aggressively here by saying
      // auto result = variable_grad + new_grad (or vice versa), checking result's
      // layout, and copying to an obedient clone if necessary before update_grad.
      // The copy would require another gmem pass.  We can't create empty result with
      // the right layout then add_out into it with a single kernel, because GradMode
      // is enabled in this branch, and add_out isn't differentiable.
      // Maybe more trouble than it's worth.
    }
  }

  Variable variable;
};

} // namespace autograd
} // namespace torch
