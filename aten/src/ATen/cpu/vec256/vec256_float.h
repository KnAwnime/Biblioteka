#pragma once

#include "intrinsics.h"
#include "vec256_base.h"
#include <sleef.h>

namespace at {
namespace vec256 {
namespace {

#ifdef __AVX__

template <> class Vec256<float> {
public:
  static constexpr int64_t size = 8;
  __m256 values;
  Vec256() {}
  Vec256(__m256 v) : values(v) {}
  Vec256(float val) {
    values = _mm256_set1_ps(val);
  }
  operator __m256() const {
    return values;
  }
  template <int64_t count>
  void load(const void* ptr) {
    __at_align32__ float tmp_values[size];
    std::memcpy(tmp_values, ptr, count * sizeof(float));
    __m256 tmp_vec = _mm256_load_ps(reinterpret_cast<const float*>(tmp_values));
    values = _mm256_blend_ps(values, tmp_vec, (1 << count) - 1);
  }
  void load(const void* ptr, int64_t count = size) {
    // TODO: Add bounds checking (error on switch default)?
    switch (count) {
      case 0:
        break;
      case 1:
        load<1>(ptr);
        break;
      case 2:
        load<2>(ptr);
        break;
      case 3:
        load<3>(ptr);
        break;
      case 4:
        load<4>(ptr);
        break;
      case 5:
        load<5>(ptr);
        break;
      case 6:
        load<6>(ptr);
        break;
      case 7:
        load<7>(ptr);
        break;
      case 8:
        values = _mm256_loadu_ps(reinterpret_cast<const float*>(ptr));
    }
  }
  static Vec256<float> s_load(const void* ptr) {
    Vec256<float> vec;
    vec.load(ptr);
    return vec;
  }
  void store(void* ptr, int64_t count = size) const {
    if (count == size) {
      _mm256_storeu_ps(reinterpret_cast<float*>(ptr), values);
    } else {
      float tmp_values[size];
      _mm256_storeu_ps(reinterpret_cast<float*>(tmp_values), values);
      std::memcpy(ptr, tmp_values, count * sizeof(float));
    }
  }
  Vec256<float> map(float (*f)(float)) const {
    __at_align32__ float tmp[8];
    store(tmp);
    for (int64_t i = 0; i < 8; i++) {
      tmp[i] = f(tmp[i]);
    }
    return s_load(tmp);
  }
  Vec256<float> abs() const {
    auto mask = _mm256_set1_ps(-0.f);
    return _mm256_andnot_ps(mask, values);
  }
  Vec256<float> acos() const {
    return Vec256<float>(Sleef_acosf8_u10(values));
  }
  Vec256<float> asin() const {
    return Vec256<float>(Sleef_asinf8_u10(values));
  }
  Vec256<float> atan() const {
    return Vec256<float>(Sleef_atanf8_u10(values));
  }
  Vec256<float> erf() const {
    return Vec256<float>(Sleef_erff8_u10(values));
  }
  Vec256<float> exp() const {
    return Vec256<float>(Sleef_expf8_u10(values));
  }
  Vec256<float> expm1() const {
    return Vec256<float>(Sleef_expm1f8_u10(values));
  }
  Vec256<float> log() const {
    return Vec256<float>(Sleef_logf8_u10(values));
  }
  Vec256<float> log2() const {
    return Vec256<float>(Sleef_log2f8_u10(values));
  }
  Vec256<float> log10() const {
    return Vec256<float>(Sleef_log10f8_u10(values));
  }
  Vec256<float> log1p() const {
    return Vec256<float>(Sleef_log1pf8_u10(values));
  }
  Vec256<float> sin() const {
    return map(std::sin);
  }
  Vec256<float> cos() const {
    return map(std::cos);
  }
  Vec256<float> ceil() const {
    return _mm256_ceil_ps(values);
  }
  Vec256<float> floor() const {
    return _mm256_floor_ps(values);
  }
  Vec256<float> round() const {
    return _mm256_round_ps(values, (_MM_FROUND_TO_NEAREST_INT | _MM_FROUND_NO_EXC));
  }
  Vec256<float> tanh() const {
    return Vec256<float>(Sleef_tanhf8_u10(values));
  }
  Vec256<float> trunc() const {
    return _mm256_round_ps(values, (_MM_FROUND_TO_ZERO | _MM_FROUND_NO_EXC));
  }
  Vec256<float> sqrt() const {
    return _mm256_sqrt_ps(values);
  }
};

template <>
Vec256<float> inline operator+(const Vec256<float>& a, const Vec256<float>& b) {
  return _mm256_add_ps(a, b);
}

template <>
Vec256<float> inline operator-(const Vec256<float>& a, const Vec256<float>& b) {
  return _mm256_sub_ps(a, b);
}

template <>
Vec256<float> inline operator*(const Vec256<float>& a, const Vec256<float>& b) {
  return _mm256_mul_ps(a, b);
}

template <>
Vec256<float> inline operator/(const Vec256<float>& a, const Vec256<float>& b) {
  return _mm256_div_ps(a, b);
}

template <>
Vec256<float> inline max(const Vec256<float>& a, const Vec256<float>& b) {
  return _mm256_max_ps(a, b);
}

#endif

}}}
