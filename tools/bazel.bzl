load("@rules_cc//cc:defs.bzl", "cc_binary", "cc_library", "cc_test")
load("@rules_cuda//cuda:defs.bzl", "requires_cuda_enabled")
load("//c10/macros:cmake_configure_file.bzl", "cmake_configure_file")
load("//tools/config:defs.bzl", "if_cuda")

def _genrule(**kwds):
    if _enabled(**kwds):
        native.genrule(**kwds)

def _is_cpu_static_dispatch_build():
    return False

def _py_library(name, **kwds):
    deps = [dep for dep in kwds.pop("deps", []) if dep != None]
    native.py_library(name = name, deps = deps, **kwds)

def _requirement(_pypi_project):
    return None

def pytorch_cc_test(name, srcs, tags = [], **kwargs):
    """PyTorch cc test rule.

    One of the reasons to proxy all tests through this central location
    is ability to change the behavior on all of them instead of one-by-one.

    For example, somebody using Remote Build Execution can add exec_properties for tests here.
    """
    if "gpu-required" in tags:
        exec_properties = {
            "test.dockerRuntime": "nvidia",
        }
    else:
        exec_properties = {}
    native.cc_test(name=name, srcs=srcs, tags=tags, exec_properties=exec_properties, **kwargs)

# Rules implementation for the Bazel build system. Since the common
# build structure aims to replicate Bazel as much as possible, most of
# the rules simply forward to the Bazel definitions.
rules = struct(
    cc_binary = cc_binary,
    cc_library = cc_library,
    cc_test = pytorch_cc_test,
    cmake_configure_file = cmake_configure_file,
    filegroup = native.filegroup,
    genrule = _genrule,
    glob = native.glob,
    if_cuda = if_cuda,
    is_cpu_static_dispatch_build = _is_cpu_static_dispatch_build,
    py_binary = native.py_binary,
    py_library = _py_library,
    requirement = _requirement,
    requires_cuda_enabled = requires_cuda_enabled,
    select = select,
    test_suite = native.test_suite,
)

def _enabled(tags = [], **_kwds):
    """Determines if the target is enabled."""
    return "-bazel" not in tags
