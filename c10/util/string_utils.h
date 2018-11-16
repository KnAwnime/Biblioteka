#pragma once

#include <string>
#include <sstream>

using std::string;

namespace c10 {

// to_string, stoi and stod implementation for Android related stuff.
// Note(jiayq): Do not use the CAFFE2_TESTONLY_FORCE_STD_STRING_TEST macro
// outside testing code that lives under common_test.cc
#if defined(__ANDROID__) || defined(CAFFE2_TESTONLY_FORCE_STD_STRING_TEST)
#define CAFFE2_TESTONLY_WE_ARE_USING_CUSTOM_STRING_FUNCTIONS 1
template <typename T>
std::string to_string(T value)
{
    std::ostringstream os;
    os << value;
    return os.str();
}

inline int stoi(const string& str) {
    std::stringstream ss;
    int n = 0;
    ss << str;
    ss >> n;
    return n;
}

inline uint64_t stoull(const string& str) {
    std::stringstream ss;
    uint64_t n = 0;
    ss << str;
    ss >> n;
    return n;
}

inline double stod(const string& str, std::size_t* pos = 0) {
    std::stringstream ss;
    ss << str;
    double val = 0;
    ss >> val;
    if (pos) {
        if (ss.tellg() == std::streampos(-1)) {
            *pos = str.size();
        }
        else {
            *pos = ss.tellg();
        }
    }
    return val;
}
#else
#define CAFFE2_TESTONLY_WE_ARE_USING_CUSTOM_STRING_FUNCTIONS 0
using std::to_string;
using std::stoi;
using std::stoull;
using std::stod;
#endif // defined(__ANDROID__) || defined(CAFFE2_FORCE_STD_STRING_FALLBACK_TEST)

}

