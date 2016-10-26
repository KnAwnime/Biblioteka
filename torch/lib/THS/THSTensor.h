#ifndef THS_TENSOR_INC
#define THS_TENSOR_INC

#include "TH.h"

#define THSTensor          TH_CONCAT_3(THS,Real,Tensor)
#define THSTensor_(NAME)   TH_CONCAT_4(THS,Real,Tensor_,NAME)

#include "generic/THSTensor.h"
#include "THSGenerateAllTypes.h"

#include "generic/THSTensorMath.h"
#include "THSGenerateAllTypes.h"

#endif

