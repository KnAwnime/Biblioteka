#pragma once

#include <c10/util/Exception.h>
#include <c10/util/Optional.h>
#include <torch/csrc/WindowsTorchApiMacro.h>

#include <cstdio>
#include <cstdlib>
#include <string>

#if !defined(_WIN32)
#include <unistd.h>
#endif

namespace torch {
namespace detail {
// Creates the filename pattern passed to and completed by `mkstemp`.
// Returns std::vector<char> because `mkstemp` needs a (non-const) `char*`.
#if !defined(_WIN32)
inline std::vector<char> make_filename(std::string name_prefix) {
  // The filename argument to `mkstemp` needs "XXXXXX" at the end according to
  // http://pubs.opengroup.org/onlinepubs/009695399/functions/mkstemp.html
  static const std::string kRandomPattern = "XXXXXX";
  static const char* env_variables[] = {"TMPDIR", "TMP", "TEMP", "TEMPDIR"};

  std::string tmp_directory = "/tmp";
  for (const char* variable : env_variables) {
    if (const char* path = getenv(variable)) {
      tmp_directory = path;
      break;
    }
  }

  std::vector<char> filename;
  filename.reserve(
      tmp_directory.size() + 1 + name_prefix.size() + kRandomPattern.size());

  filename.insert(filename.end(), tmp_directory.begin(), tmp_directory.end());
  filename.push_back('/');
  filename.insert(filename.end(), name_prefix.begin(), name_prefix.end());
  filename.insert(filename.end(), kRandomPattern.begin(), kRandomPattern.end());

  return filename;
}
#endif // !defined(_WIN32)
} // namespace detail

struct TORCH_API TempFile {
#if !defined(_WIN32)
  TempFile(std::string name, int fd) : fd(fd), name(std::move(name)) {}

  ~TempFile() {
    unlink(name.c_str());
    close(fd);
  }

  const int fd;
#endif // !defined(_WIN32)

  const std::string name;
};

/// Attempts to return a temporary file or returns nullopt if an error ocurred.
/// The file returned follows the pattern
/// `<tmp-dir>/<name-prefix><random-pattern>`, where `<tmp-dir>` is the value of
/// the `"TMPDIR"`, `"TMP"`, `"TEMP"` or
/// `"TEMPDIR"` environment variable if any is set, or otherwise `/tmp`;
/// `<name-prefix>` is the value supplied to this function, and
/// `<random-pattern>` is a random sequence of numbers.
/// On Windows, `name_prefix` is ignored.
inline c10::optional<TempFile> try_make_tempfile(
    std::string name_prefix = "torch-file-") {
#if defined(_WIN32)
  return TempFile{std::tmpnam(nullptr)};
#else
  std::vector<char> filename = detail::make_filename(std::move(name_prefix));
  const int fd = mkstemp(filename.data());
  if (fd == -1) {
    return c10::nullopt;
  }
  return TempFile(std::string(filename.begin(), filename.end()), fd);
#endif // defined(_WIN32)
}

/// Like `try_make_tempfile`, but throws an exception if a temporary file could
/// not be returned.
inline TempFile make_tempfile(std::string name_prefix = "torch-file-") {
  if (auto tempfile = try_make_tempfile(std::move(name_prefix))) {
    return *tempfile;
  }
  AT_ERROR("Error generating temporary filename");
}
} // namespace torch
