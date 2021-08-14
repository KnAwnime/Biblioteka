#include <fstream>
#include <gtest/gtest.h>
#include <test/cpp/jit/test_utils.h>
#include <torch/csrc/jit/api/module.h>
#include <torch/csrc/jit/frontend/resolver.h>
#include <torch/csrc/jit/mobile/import.h>
#include <torch/csrc/jit/mobile/module.h>
#include <torch/csrc/jit/mobile/profiler_edge.h>

#include <unordered_set>

#ifdef EDGE_PROFILER_USE_KINETO
namespace torch {
namespace jit {
namespace mobile {

namespace {
bool checkModuleHierarchyForOp(
    const std::string& op_name,
    const std::string& module_hier,
    std::ifstream& trace_file) {
  std::string line;
  while (std::getline(trace_file, line) ) {
    if (line.find(op_name) != std::string::npos) {
      while (std::getline(trace_file, line) ) {
        if (line.find("Module Hierarchy") != std::string::npos) {
          return (line.find(module_hier) != std::string::npos);
        }
      }
    }
  }
  return false;
}
} // namespace

TEST(MobileProfiler, ModuleHierarchy) {
  std::string filePath(__FILE__);
  auto testModelFile = filePath.substr(0, filePath.find_last_of("/\\") + 1);
  testModelFile.append("to_be_profiled_module.ptl");

  std::vector<IValue> inputs;
  inputs.emplace_back(at::rand({64, 64}));
  inputs.emplace_back(at::rand({64, 64}));
  std::string trace_file_name("/tmp/test_trace.trace");

  mobile::Module bc = _load_for_mobile(testModelFile);
  {
    KinetoEdgeCPUProfiler profiler(
        bc,
        trace_file_name,
        false, // record input_shapes
        false, // profile memory
        true, // record callstack
        false, // record flops
        true); // record module hierarchy
    bc.forward(inputs);
  } // End of profiler
  std::ifstream trace_file(trace_file_name);
  std::string line;
  ASSERT_TRUE(trace_file.is_open());
  trace_file.seekg(0, std::ios_base::beg);
  ASSERT_TRUE(checkModuleHierarchyForOp("aten::sub", "top(C)::<unknown>.A0(A)::forward.aten::sub", trace_file));
  trace_file.seekg(0, std::ios_base::beg);
  ASSERT_TRUE(checkModuleHierarchyForOp("aten::mul", "top(C)::<unknown>.A0(A)::forward.SELF(A)::forward_impl_.SELF(A)::my_new_method.aten::mul", trace_file));
  trace_file.seekg(0, std::ios_base::beg);
  ASSERT_TRUE(checkModuleHierarchyForOp("aten::add", "top(C)::<unknown>.A0(A)::forward.SELF(A)::forward_impl_.aten::add", trace_file));
  ASSERT_TRUE(checkModuleHierarchyForOp("aten::add", "top(C)::<unknown>.SELF(C)::call_b.B0(B)::forward.aten::add", trace_file));
  ASSERT_TRUE(checkModuleHierarchyForOp("aten::add", "top(C)::<unknown>.aten::add", trace_file));
}

} // namespace mobile
} // namespace jit
} // namespace torch
#endif
