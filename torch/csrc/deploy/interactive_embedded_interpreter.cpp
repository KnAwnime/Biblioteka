/*
 * The tool provides a shell to the embedded interpreter. Useful to inspect the
 * state of the embedding interpreter interactively.
 */
#include <torch/csrc/deploy/deploy.h>

DEFINE_string(
    pylib_root,
    "",
    "The root of the installed python libraries in the system");
DEFINE_string(pyscript, "", "The path of the python script to execute");

// NOLINTNEXTLINE(bugprone-exception-escape)
int main(int argc, char** argv) {
  gflags::ParseCommandLineFlags(&argc, &argv, true);

  if (FLAGS_pylib_root.size() > 0) {
    LOG(INFO) << "Will add " << FLAGS_pylib_root << " to python sys.path";
  }
  // create multiple interpreter instances so the tool does not just cover the
  // simplest case with a single interpreter instance.
  torch::deploy::InterpreterManager m(2, FLAGS_pylib_root);
  auto I = m.acquireOne();

  if (FLAGS_pyscript.size() > 0) {
    auto realpath = I.global("os", "path").attr("expanduser")({FLAGS_pyscript});
    I.global("runpy", "run_path")({realpath});
  } else {
    c10::ArrayRef<torch::deploy::Obj> noArgs;
    I.global("pdb", "set_trace")(noArgs);
  }
  return 0;
}
