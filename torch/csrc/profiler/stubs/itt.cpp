#include <sstream>

#include <c10/util/irange.h>
#include <torch/csrc/itt_wrapper.h>
#include <torch/csrc/profiler/stubs/base.h>

namespace torch {
namespace profiler {
namespace impl {
namespace {

struct ITTMethods : public ProfilerStubs {
  void record(int* device, ProfilerVoidEventStub* event, int64_t* cpu_ns)
      const override {}

  float elapsed(
      const ProfilerVoidEventStub* event,
      const ProfilerVoidEventStub* event2) const override {
    return 0;
  }

  void mark(const char* name) const override {
    torch::profiler::itt_mark(name);
  }

  void rangePush(const char* name) const override {
    torch::profiler::itt_range_push(name);
  }

  void rangePop() const override {
    torch::profiler::itt_range_pop();
  }

  void onEachDevice(std::function<void(int)> op) const override {}

  void synchronize() const override {}

  bool enabled() const override {
    return true;
  }
};

struct RegisterITTMethods {
  RegisterITTMethods() {
    static ITTMethods methods;
    registerITTMethods(&methods);
  }
};
RegisterITTMethods reg;

} // namespace
} // namespace impl
} // namespace profiler
} // namespace torch
