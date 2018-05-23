#ifndef TH_GENERIC_FILE
#define TH_GENERIC_FILE "generic/utils.cpp"
#else

#if defined(THD_GENERIC_FILE) || defined(TH_REAL_IS_HALF)
#define GENERATE_SPARSE 0
#else
#define GENERATE_SPARSE 1
#endif

template<>
void THPPointer<THWStorage>::free() {
  if (ptr)
    THWStorage_(free)(LIBRARY_STATE ptr);
}

template<>
void THPPointer<THTensor>::free() {
  if (ptr)
    THTensor_(free)(LIBRARY_STATE ptr);
}

template<>
void THPPointer<THPStorage>::free() {
  if (ptr)
    Py_DECREF(ptr);
}

#if GENERATE_SPARSE
template<>
void THPPointer<THSTensor>::free() {
  if (ptr)
    THSTensor_(free)(LIBRARY_STATE ptr);
}
#endif


template class THPPointer<THWStorage>;
template class THPPointer<THTensor>;
template class THPPointer<THPStorage>;
#if GENERATE_SPARSE
template class THPPointer<THSTensor>;
#endif

#undef GENERATE_SPARSE

#endif
