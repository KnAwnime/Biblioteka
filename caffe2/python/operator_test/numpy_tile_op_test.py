from __future__ import absolute_import
from __future__ import division
from __future__ import print_function
from __future__ import unicode_literals

import numpy as np

from hypothesis import given
import hypothesis.strategies as st
import unittest

from caffe2.python import core, workspace
import caffe2.python.hypothesis_test_util as hu


class TestNumpyTile(hu.HypothesisTestCase):
    @given(ndim=st.integers(min_value=1, max_value=4),
           seed=st.integers(min_value=0, max_value=65536),
           **hu.gcs_cpu_only)
    def test_numpy_tile(self, ndim, seed, gc, dc):
        np.random.seed(seed)

        input_dims = np.random.randint(1, 4, size=ndim)
        input = np.random.randn(*input_dims)
        repeats = np.random.randint(1, 5, size=ndim)

        op = core.CreateOperator(
            'NumpyTile', ['input', 'repeats'], 'out',
        )

        def tile_ref(input, repeats):
            tiled_data = np.tile(input, repeats)
            return (tiled_data,)

        # Check against numpy reference
        self.assertReferenceChecks(gc, op, [input, repeats],
                                   tile_ref)

    @given(ndim=st.integers(min_value=1, max_value=4),
           seed=st.integers(min_value=0, max_value=65536),
           **hu.gcs_cpu_only)
    def test_numpy_tile_zero_dim(self, ndim, seed, gc, dc):
        np.random.seed(seed)

        input_dims = np.random.randint(0, 4, size=ndim)
        input = np.random.randn(*input_dims)
        repeats = np.random.randint(0, 5, size=ndim)

        op = core.CreateOperator(
            'NumpyTile', ['input', 'repeats'], 'out',
        )

        def tile_ref(input, repeats):
            tiled_data = np.tile(input, repeats)
            return (tiled_data,)

        # Check against numpy reference
        self.assertReferenceChecks(gc, op, [input, repeats],
                                   tile_ref)


if __name__ == "__main__":
    unittest.main()
