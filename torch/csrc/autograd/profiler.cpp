#include "torch/csrc/autograd/profiler.h"
#include "torch/csrc/autograd/function.h"

namespace torch { namespace autograd { namespace profiler {

bool profiling = false;
bool using_nvprof;
bool using_cuda;
uint32_t next_thread_id = 0;
std::mutex all_event_lists_mutex;
std::list<std::shared_ptr<RangeEventList>> all_event_lists;
thread_local std::shared_ptr<RangeEventList> event_list;
thread_local int32_t thread_id;

void RecordFunction::pushFunctionRange(Function* fn) {
  pushRange(fn->name());
}

void enableProfiler(bool use_nvprof, bool use_cuda) {
#ifndef WITH_CUDA
  if (use_nvprof)
    throw std::runtime_error("Can't use CUDA profiler - PyTorch was compiled without CUDA");
#endif
  if (profiling) {
    if (use_nvprof != using_nvprof)
      throw std::runtime_error("can't change use_nvprof flag while profiler is running");
    return;
  }
  profiling = true;
  using_nvprof = use_nvprof;
  using_cuda = use_cuda;
  mark("__start_profile");
}

thread_event_lists disableProfiler() {
  if (!profiling) {
    throw std::runtime_error("can't disable profiler when it's not running");
  }
  mark("__stop_profile");
  profiling = false;
  if (using_nvprof) {
    return thread_event_lists();
  } else {
    thread_event_lists result;
    std::lock_guard<std::mutex> guard(all_event_lists_mutex);
    for (auto it = all_event_lists.begin(); it != all_event_lists.end();) {
      auto & list = *it;
      result.emplace_back(list->consolidate());
      // GC lists that are not held by any threads
      if (list.use_count() == 1) {
        auto current_it = it;
        ++it;
        all_event_lists.erase(current_it);
      } else {
        ++it;
      }
    }
    return result;
  }
}

}}}
