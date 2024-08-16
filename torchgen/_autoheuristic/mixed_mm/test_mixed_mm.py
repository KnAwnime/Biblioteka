import os
import sys
import unittest


sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from expecttest import TestCase

from test_utils import read_file_to_string, run_bash  # type: ignore[import-not-found]


class TestMixedMM(TestCase):
    def test_mixedmm_a100(self) -> None:
        run_bash("get_mixedmm_dataset.sh")
        run_bash("gen_mixedmm_heuristic_a100.sh")
        file_path = "../../../torch/_inductor/autoheuristic/artifacts/_MixedMMA100.py"
        a100_heuristic_generated_code = read_file_to_string(file_path)

        self.assertExpectedInline(
            a100_heuristic_generated_code,
            """\
# flake8: noqa: B950
# fmt: off
# This file was generated by AutoHeuristic. Do not modify it manually!
# To regenerate this file, take a look at the steps in the README.md file inside torchgen/_autoheuristic/mixed_mm/
from typing import List, Optional, Tuple

from torch._inductor.autoheuristic.autoheuristic_utils import (
    AHContext,
    AHMetadata,
    Choice,
)
from torch._inductor.autoheuristic.learnedheuristic_interface import (
    LearnedHeuristicDecision,
)


class MixedMMA100(LearnedHeuristicDecision):

    def __init__(self) -> None:
        self.choices: List[Choice] = []
        self.fill_choices()

    def check_precondition(self, metadata: AHMetadata, context: AHContext,) -> bool:
        return (
            metadata.name == self.get_name()
            and metadata.shared_memory == 166912
            and str(metadata.device_capa) == "(8, 0)"
        )

    def get_confidence_threshold(self) -> float:
        return 0.0

    def get_choice(self, idx: int) -> Optional[str]:
        if idx < len(self.choices):
            return self.choices[idx]
        return None

    def fill_choices(self) -> None:
        self.choices.append('extern_fallback_mixed_mm')
        self.choices.append('type=triton_BLOCK-M=128_BLOCK-K=32_BLOCK-N=128_numstages=3_numwarps=4')
        self.choices.append('type=triton_BLOCK-M=128_BLOCK-K=64_BLOCK-N=128_numstages=3_numwarps=4')
        self.choices.append('type=triton_BLOCK-M=16_BLOCK-K=128_BLOCK-N=128_numstages=4_numwarps=4')
        self.choices.append('type=triton_BLOCK-M=16_BLOCK-K=128_BLOCK-N=32_numstages=2_numwarps=2')
        self.choices.append('type=triton_BLOCK-M=16_BLOCK-K=128_BLOCK-N=32_numstages=5_numwarps=2')
        self.choices.append('type=triton_BLOCK-M=16_BLOCK-K=128_BLOCK-N=64_numstages=5_numwarps=4')
        self.choices.append('type=triton_BLOCK-M=16_BLOCK-K=256_BLOCK-N=128_numstages=3_numwarps=4')
        self.choices.append('type=triton_BLOCK-M=16_BLOCK-K=256_BLOCK-N=128_numstages=5_numwarps=8')
        self.choices.append('type=triton_BLOCK-M=16_BLOCK-K=64_BLOCK-N=128_numstages=5_numwarps=8')
        self.choices.append('type=triton_BLOCK-M=16_BLOCK-K=64_BLOCK-N=64_numstages=3_numwarps=4')
        self.choices.append('type=triton_BLOCK-M=32_BLOCK-K=128_BLOCK-N=128_numstages=4_numwarps=4')
        self.choices.append('type=triton_BLOCK-M=32_BLOCK-K=128_BLOCK-N=32_numstages=2_numwarps=4')
        self.choices.append('type=triton_BLOCK-M=32_BLOCK-K=128_BLOCK-N=32_numstages=5_numwarps=4')
        self.choices.append('type=triton_BLOCK-M=64_BLOCK-K=128_BLOCK-N=128_numstages=4_numwarps=4')
        self.choices.append('type=triton_BLOCK-M=64_BLOCK-K=128_BLOCK-N=32_numstages=5_numwarps=4')
        self.choices.append('type=triton_BLOCK-M=64_BLOCK-K=128_BLOCK-N=64_numstages=5_numwarps=4')
        self.choices.append('type=triton_BLOCK-M=64_BLOCK-K=32_BLOCK-N=128_numstages=3_numwarps=4')
        self.choices.append('type=triton_BLOCK-M=64_BLOCK-K=32_BLOCK-N=128_numstages=4_numwarps=8')
        self.choices.append('type=triton_BLOCK-M=64_BLOCK-K=32_BLOCK-N=64_numstages=3_numwarps=4')
        self.choices.append('type=triton_BLOCK-M=64_BLOCK-K=64_BLOCK-N=128_numstages=3_numwarps=4')
        self.choices.append('type=triton_BLOCK-M=64_BLOCK-K=64_BLOCK-N=128_numstages=5_numwarps=8')

    def get_name(self) -> str:
        return 'mixed_mm'

    def get_best_choices(self, context: AHContext) -> Optional[List[Tuple[float, int]]]:
        if str(context.get_value('1LEQmLEQ16')) != 'True':
            if context.get_value('m') <= 32.5:
                if context.get_value('n') <= 6976.0:
                    if context.get_value('n') <= 3520.0:
                        if context.get_value('m*n') <= 37632.0:
                            return None
                        else:
                            return [(1.000, 13)]
                    else:
                        if context.get_value('m*k') <= 452352.0:
                            return [(0.590, 13), (0.256, 8), (0.103, 7), (0.051, 11)]
                        else:
                            return [(0.778, 8), (0.222, 13)]
                else:
                    if context.get_value('k*n') <= 102776832.0:
                        if context.get_value('n') <= 14656.0:
                            return [(1.000, 11)]
                        else:
                            return [(0.889, 11), (0.111, 13)]
                    else:
                        return [(1.000, 11)]
            else:
                if context.get_value('m*n') <= 446464.0:
                    if context.get_value('m*n') <= 223424.0:
                        if context.get_value('mat1_stride_0') <= 3968.0:
                            return None
                        else:
                            return None
                    else:
                        if context.get_value('m*n') <= 346112.0:
                            return [(0.960, 16), (0.040, 7)]
                        else:
                            return [(0.750, 16), (0.136, 14), (0.114, 7)]
                else:
                    if str(context.get_value('33LEQmLEQ64')) != 'True':
                        if context.get_value('n') <= 6976.0:
                            return [(1.000, 14)]
                        else:
                            return [(0.753, 2), (0.222, 1), (0.015, 7), (0.007, 16), (0.004, 12)]
                    else:
                        if context.get_value('n') <= 13888.0:
                            return [(0.710, 14), (0.275, 21), (0.014, 12)]
                        else:
                            return [(0.374, 19), (0.339, 20), (0.106, 21), (0.101, 16), (0.066, 17), (0.009, 14), (0.004, 18)]
        else:
            if context.get_value('n') <= 3520.0:
                if context.get_value('arith_intensity') <= 3.994754433631897:
                    if str(context.get_value('mat2_dtype')) != 'torch.uint8':
                        if context.get_value('m*k') <= 18944.0:
                            return [(0.577, 5), (0.423, 6)]
                        else:
                            return [(0.988, 5), (0.012, 6)]
                    else:
                        if context.get_value('arith_intensity') <= 2.9899919033050537:
                            return None
                        else:
                            return None
                else:
                    if context.get_value('arith_intensity') <= 7.956453561782837:
                        if context.get_value('k*n') <= 9244032.0:
                            return [(0.822, 5), (0.178, 6)]
                        else:
                            return [(0.977, 5), (0.023, 0)]
                    else:
                        if context.get_value('m*k') <= 978944.0:
                            return [(1.000, 5)]
                        else:
                            return [(0.971, 5), (0.029, 0)]
            else:
                if context.get_value('n') <= 13632.0:
                    if context.get_value('n') <= 6976.0:
                        return [(1.000, 6)]
                    else:
                        if context.get_value('k') <= 3968.0:
                            return [(0.617, 3), (0.111, 5), (0.099, 7), (0.086, 9), (0.062, 6), (0.025, 8)]
                        else:
                            return [(0.779, 8), (0.119, 5), (0.053, 7), (0.035, 6), (0.013, 3)]
                else:
                    if context.get_value('k*n') <= 39518208.0:
                        return [(0.385, 4), (0.327, 3), (0.192, 6), (0.038, 7), (0.038, 10), (0.019, 5)]
                    else:
                        if context.get_value('n') <= 20800.0:
                            return [(0.821, 6), (0.121, 7), (0.029, 4), (0.014, 5), (0.007, 3), (0.007, 8)]
                        else:
                            return [(0.530, 7), (0.386, 6), (0.046, 8), (0.021, 3), (0.015, 4), (0.002, 5)]
""",
        )

    def test_mixedmm_h100(self) -> None:
        run_bash("get_mixedmm_dataset.sh")
        run_bash("gen_mixedmm_heuristic_h100.sh")
        file_path = "../../../torch/_inductor/autoheuristic/artifacts/_MixedMMH100.py"
        h100_heuristic_generated_code = read_file_to_string(file_path)

        self.assertExpectedInline(
            h100_heuristic_generated_code,
            """\
# flake8: noqa: B950
# fmt: off
# This file was generated by AutoHeuristic. Do not modify it manually!
# To regenerate this file, take a look at the steps in the README.md file inside torchgen/_autoheuristic/mixed_mm/
from typing import List, Optional, Tuple

from torch._inductor.autoheuristic.autoheuristic_utils import (
    AHContext,
    AHMetadata,
    Choice,
)
from torch._inductor.autoheuristic.learnedheuristic_interface import (
    LearnedHeuristicDecision,
)


class MixedMMH100(LearnedHeuristicDecision):

    def __init__(self) -> None:
        self.choices: List[Choice] = []
        self.fill_choices()

    def check_precondition(self, metadata: AHMetadata, context: AHContext,) -> bool:
        return (
            metadata.name == self.get_name()
            and metadata.shared_memory == 232448
            and str(metadata.device_capa) == "(9, 0)"
        )

    def get_confidence_threshold(self) -> float:
        return 0.0

    def get_choice(self, idx: int) -> Optional[str]:
        if idx < len(self.choices):
            return self.choices[idx]
        return None

    def fill_choices(self) -> None:
        self.choices.append('extern_fallback_mixed_mm')
        self.choices.append('type=triton_BLOCK-M=128_BLOCK-K=32_BLOCK-N=128_numstages=3_numwarps=4')
        self.choices.append('type=triton_BLOCK-M=128_BLOCK-K=32_BLOCK-N=64_numstages=3_numwarps=4')
        self.choices.append('type=triton_BLOCK-M=128_BLOCK-K=64_BLOCK-N=128_numstages=5_numwarps=8')
        self.choices.append('type=triton_BLOCK-M=16_BLOCK-K=128_BLOCK-N=128_numstages=4_numwarps=4')
        self.choices.append('type=triton_BLOCK-M=16_BLOCK-K=128_BLOCK-N=32_numstages=2_numwarps=2')
        self.choices.append('type=triton_BLOCK-M=16_BLOCK-K=128_BLOCK-N=32_numstages=5_numwarps=2')
        self.choices.append('type=triton_BLOCK-M=16_BLOCK-K=128_BLOCK-N=64_numstages=5_numwarps=4')
        self.choices.append('type=triton_BLOCK-M=16_BLOCK-K=256_BLOCK-N=128_numstages=3_numwarps=4')
        self.choices.append('type=triton_BLOCK-M=16_BLOCK-K=256_BLOCK-N=128_numstages=5_numwarps=8')
        self.choices.append('type=triton_BLOCK-M=16_BLOCK-K=64_BLOCK-N=128_numstages=5_numwarps=8')
        self.choices.append('type=triton_BLOCK-M=16_BLOCK-K=64_BLOCK-N=64_numstages=3_numwarps=4')
        self.choices.append('type=triton_BLOCK-M=32_BLOCK-K=128_BLOCK-N=128_numstages=4_numwarps=4')
        self.choices.append('type=triton_BLOCK-M=32_BLOCK-K=128_BLOCK-N=32_numstages=2_numwarps=4')
        self.choices.append('type=triton_BLOCK-M=32_BLOCK-K=128_BLOCK-N=32_numstages=5_numwarps=4')
        self.choices.append('type=triton_BLOCK-M=32_BLOCK-K=32_BLOCK-N=64_numstages=5_numwarps=8')
        self.choices.append('type=triton_BLOCK-M=64_BLOCK-K=128_BLOCK-N=128_numstages=4_numwarps=4')
        self.choices.append('type=triton_BLOCK-M=64_BLOCK-K=128_BLOCK-N=32_numstages=5_numwarps=4')
        self.choices.append('type=triton_BLOCK-M=64_BLOCK-K=128_BLOCK-N=64_numstages=5_numwarps=4')
        self.choices.append('type=triton_BLOCK-M=64_BLOCK-K=64_BLOCK-N=128_numstages=3_numwarps=4')
        self.choices.append('type=triton_BLOCK-M=64_BLOCK-K=64_BLOCK-N=64_numstages=3_numwarps=8')

    def get_name(self) -> str:
        return 'mixed_mm'

    def get_best_choices(self, context: AHContext) -> Optional[List[Tuple[float, int]]]:
        if context.get_value('arith_intensity') <= 15.988086223602295:
            if context.get_value('n') <= 25280.0:
                if context.get_value('n') <= 1344.0:
                    if context.get_value('mat1_stride_0') <= 7808.0:
                        return [(0.581, 7), (0.419, 6)]
                    else:
                        if context.get_value('m*n') <= 7680.0:
                            return [(0.875, 0), (0.125, 6)]
                        else:
                            return [(0.833, 0), (0.167, 7)]
                else:
                    if context.get_value('n') <= 8512.0:
                        if str(context.get_value('mat2_dtype')) != 'torch.int8':
                            return [(0.763, 6), (0.237, 7)]
                        else:
                            return [(0.725, 7), (0.275, 6)]
                    else:
                        if str(context.get_value('mat1_dtype')) != 'torch.bfloat16':
                            return [(0.736, 7), (0.197, 9), (0.048, 6), (0.014, 8), (0.005, 10)]
                        else:
                            return [(0.473, 7), (0.398, 6), (0.097, 9), (0.032, 10)]
            else:
                if context.get_value('n') <= 42254.0:
                    if context.get_value('n') <= 33856.0:
                        if context.get_value('k*n') <= 68157440.0:
                            return [(0.370, 4), (0.370, 5), (0.074, 7), (0.074, 8), (0.074, 11), (0.037, 6)]
                        else:
                            return [(0.916, 8), (0.036, 7), (0.036, 9), (0.012, 4)]
                    else:
                        return [(0.659, 5), (0.341, 6)]
                else:
                    if context.get_value('k*n') <= 326052992.0:
                        if context.get_value('n') <= 55232.0:
                            return [(0.571, 6), (0.321, 7), (0.036, 4), (0.036, 8), (0.036, 9)]
                        else:
                            return [(0.506, 6), (0.325, 8), (0.104, 7), (0.039, 5), (0.026, 9)]
                    else:
                        if context.get_value('n') <= 57024.0:
                            return [(0.462, 9), (0.385, 7), (0.115, 6), (0.038, 8)]
                        else:
                            return [(0.598, 8), (0.223, 9), (0.107, 6), (0.071, 7)]
        else:
            if context.get_value('m*n') <= 543936.0:
                if str(context.get_value('17LEQmLEQ32')) != 'True':
                    if context.get_value('m*n') <= 262272.0:
                        if context.get_value('n') <= 1592.5:
                            return [(0.860, 0), (0.140, 9)]
                        else:
                            return None
                    else:
                        if context.get_value('m*k') <= 1294336.0:
                            return [(0.833, 17), (0.150, 18), (0.017, 15)]
                        else:
                            return [(0.917, 17), (0.083, 8)]
                else:
                    if context.get_value('n') <= 12416.0:
                        if context.get_value('m*n') <= 43008.0:
                            return None
                        else:
                            return [(0.853, 14), (0.147, 9)]
                    else:
                        return [(0.625, 12), (0.375, 14)]
            else:
                if context.get_value('m') <= 32.5:
                    if context.get_value('mat2_stride_1') <= 6656.0:
                        if context.get_value('n') <= 69184.0:
                            return [(0.611, 12), (0.361, 14), (0.028, 13)]
                        else:
                            return [(1.000, 12)]
                    else:
                        if context.get_value('mat2_stride_1') <= 20864.0:
                            return [(1.000, 12)]
                        else:
                            return [(0.958, 12), (0.042, 9)]
                else:
                    if context.get_value('m*n') <= 1085440.0:
                        if context.get_value('n') <= 9152.0:
                            return [(1.000, 18)]
                        else:
                            return [(0.780, 18), (0.160, 16), (0.060, 20)]
                    else:
                        if context.get_value('m') <= 67.0:
                            return [(0.650, 16), (0.203, 19), (0.122, 18), (0.016, 20), (0.008, 1)]
                        else:
                            return [(0.561, 3), (0.185, 16), (0.096, 20), (0.083, 19), (0.076, 2)]
""",
        )


if __name__ == "__main__":
    unittest.main()
