# Copyright (c) Meta Platforms, Inc. and affiliates
# Owner(s): ["oncall: distributed"]

import itertools

import torch

from torch.distributed._tensor import distribute_tensor
from torch.distributed._tensor.placement_types import Replicate, Shard
from torch.testing._internal.common_utils import run_tests
from torch.testing._internal.distributed._tensor.common_dtensor import (
    DTensorTestBase,
    skip_unless_torch_gpu,
    with_comms,
)


class DistMathOpsTest(DTensorTestBase):
    def linear_op_reductions(self, op_str):
        device_mesh = self.build_device_mesh()
        shard_spec = [Shard(0)]

        tensor = torch.randn(12, 8, 8)
        dtensor = distribute_tensor(tensor, device_mesh, shard_spec)

        op = getattr(tensor, op_str)
        op_dt = getattr(dtensor, op_str)

        keep_dim_or_not = [True, False, None]
        for dim in range(tensor.ndim):
            for keep_dim in keep_dim_or_not:
                args = (dim, keep_dim) if keep_dim is not None else (dim,)
                if op_str in ("max", "min"):
                    # min and max return a tuple when dim specified
                    dim_reduced_tensor, _ = op(*args)
                    dt_reduced, _ = op_dt(*args)
                else:
                    dim_reduced_tensor = op(*args)
                    dt_reduced = op_dt(*args)
                dt_dim_reduced_tensor = dt_reduced.redistribute(
                    device_mesh, [Replicate()] * device_mesh.ndim
                )
                self.assertEqual(dt_dim_reduced_tensor.to_local(), dim_reduced_tensor)

        full_reduced_tensor = op()
        dt_full_reduced = op_dt().redistribute(
            device_mesh, [Replicate()] * device_mesh.ndim
        )
        self.assertEqual(dt_full_reduced.to_local(), full_reduced_tensor)

    @with_comms
    def test_linear_op_reductions(self):
        for op_str in ("all", "sum", "prod", "max", "min"):
            self.linear_op_reductions(op_str)

    @with_comms
    @skip_unless_torch_gpu
    def test_mean(self):
        self.linear_op_reductions("mean")

    # TODO: forward test can be removed once test_softmax_with_bwd passes on CPU
    @with_comms
    def test_softmax_fwd(self):
        device_mesh = self.build_device_mesh()

        x = torch.rand(8, 12, 16, device=self.device_type)
        dims = range(3)  # used to convert -1 to the actual dim
        softmax_dims = [-1, 0, 1, 2]
        shard_dims = [-1, 0, 1, 2]
        test_list = list(itertools.product(softmax_dims, shard_dims))

        for softmax_dim, shard_dim in test_list:
            local_y = torch.nn.functional.softmax(
                x, dim=softmax_dim, dtype=torch.float32
            )
            dist_x = distribute_tensor(x, device_mesh, [Shard(shard_dim)])
            if dims[shard_dim] == dims[softmax_dim]:
                with self.assertRaisesRegex(
                    Exception, "Cannot run .* on sharding dimension!$"
                ):
                    dist_y = torch.nn.functional.softmax(
                        dist_x, dim=softmax_dim, dtype=torch.float32
                    )
            else:
                dist_y = torch.nn.functional.softmax(
                    dist_x, dim=softmax_dim, dtype=torch.float32
                )
                shard_dim = shard_dim + dist_y.ndim if shard_dim < 0 else shard_dim
                self.assertTrue(dist_y.placements[0].is_shard(dim=shard_dim))
                dist_y = dist_y.redistribute(device_mesh, [Replicate()])
                self.assertEqual(dist_y.to_local(), local_y)

    # TODO: get test_softmax_with_bwd pass on CPU
    # DTensor's _softmax_backward_data produces wrong result on CPU on certain dimension.
    # fail_on_cpu_list = [(0, -1), (1, -1)]
    @with_comms
    @skip_unless_torch_gpu
    def test_softmax_with_bwd(self):
        device_mesh = self.build_device_mesh()

        dims = range(3)  # used to convert -1 to the actual dim
        softmax_dims = [-1, 0, 1, 2]
        shard_dims = [-1, 0, 1, 2]
        test_list = list(itertools.product(softmax_dims, shard_dims))

        for params in test_list:
            softmax_dim, shard_dim = params
            x = torch.rand(8, 12, 16, device=self.device_type, requires_grad=True)
            self.assertTrue(x.requires_grad)
            local_y = torch.nn.functional.softmax(
                x, dim=softmax_dim, dtype=torch.float32
            ).sum()
            local_y.backward()

            dist_x = distribute_tensor(x, device_mesh, [Shard(shard_dim)])
            self.assertTrue(dist_x.requires_grad)
            if dims[softmax_dim] == dims[shard_dim]:
                with self.assertRaisesRegex(
                    Exception, "Cannot run .* on sharding dimension!$"
                ):
                    dist_softmax = dist_x.softmax(dim=softmax_dim)
            else:
                dist_softmax = dist_x.softmax(dim=softmax_dim)
                shard_dim = shard_dim + dist_x.ndim if shard_dim < 0 else shard_dim
                self.assertTrue(dist_softmax.placements[0].is_shard(dim=shard_dim))
                dist_y = dist_softmax.sum()
                dist_y = dist_y.redistribute(device_mesh, [Replicate()])
                self.assertEqual(dist_y.to_local(), local_y)
                self.assertIsNone(dist_x.grad)
                dist_y.backward()
                self.assertIsNotNone(dist_x.grad)
                dist_x_grad = dist_x.grad.redistribute(device_mesh, [Replicate()])
                self.assertEqual(dist_x_grad.to_local(), x.grad)


if __name__ == "__main__":
    run_tests()
