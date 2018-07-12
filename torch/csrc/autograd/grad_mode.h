#pragma once

#include "torch/csrc/WindowsTorchApiMacro.h"

namespace torch { namespace autograd {

struct GradMode {
  static bool is_enabled() {
    return _enabled;
  }
  static void set_enabled(bool enabled) {
    _enabled = enabled;
  }
private:
  TORCH_API static thread_local bool _enabled;
};

// A RAII, thread local (!) guard that enables or disables grad mode upon
// construction, and sets it back to the original value upon destruction.
struct AutoGradMode {
  AutoGradMode(bool enabled) : prev_mode(GradMode::is_enabled()) {
    GradMode::set_enabled(enabled);
  }
  ~AutoGradMode() {
    GradMode::set_enabled(prev_mode);
  }
  bool prev_mode;
};

}}
