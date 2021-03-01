# Copyright (c) Facebook, Inc. and its affiliates. All rights reserved.
#
# This source code is licensed under the BSD license found in the
# LICENSE file in the root directory of this source tree.

import collections
import copy
import io
from collections import OrderedDict
from itertools import chain
from typing import Any, Callable, Dict, List, Optional, Type

import torch
import torch.distributed as dist
from torch.nn import Parameter
from torch.optim import Optimizer

__all__ = ["ZeroRedundancyOptimizer"]


# Credits:  classy_vision/generic/distributed_util.py
def _recursive_copy_to_device(
    value: Any, non_blocking: bool, device: torch.device
) -> Any:
    """
    Recursively searches lists, tuples, dicts and copies tensors to device if
    possible. Non-tensor values are passed as-is in the result.

    .. note:  These are all copies, so if there are two objects that reference
    the same object, then after this call, there will be two different objects
    referenced on the device.
    """

    if isinstance(value, torch.Tensor):
        return value.to(device, non_blocking=non_blocking)

    if isinstance(value, (list, tuple)):
        values = [
            _recursive_copy_to_device(val, non_blocking=non_blocking, device=device)
            for val in value
        ]
        return values if isinstance(value, list) else tuple(values)

    if isinstance(value, collections.abc.Mapping):
        return {
            key: _recursive_copy_to_device(
                val, non_blocking=non_blocking, device=device
            )
            for key, val in value.items()
        }

    return value


def _broadcast_object(
    obj: Any,
    src_rank: int,
    group: object = dist.group.WORLD,
    dist_device: torch.device = torch.device("cpu"),
) -> Any:
    """
    Either broadcast from master to the fleet (default),
    or use the src setting as the original rank.
    """

    if dist.get_rank() == src_rank:
        # Emit data
        buffer = io.BytesIO()
        torch.save(obj, buffer)
        data = bytearray(buffer.getbuffer())
        length_tensor = torch.LongTensor([len(data)]).to(dist_device)
        data_send_tensor = torch.ByteTensor(data).to(dist_device)
        dist.broadcast(length_tensor, src=src_rank, group=group, async_op=False)
        dist.broadcast(data_send_tensor, src=src_rank, group=group, async_op=False)
    else:
        # Fetch from the source
        length_tensor = torch.LongTensor([0]).to(dist_device)
        dist.broadcast(length_tensor, src=src_rank, group=group, async_op=False)
        data_recv_tensor = torch.empty(
            [int(length_tensor.item())], dtype=torch.uint8, device=dist_device
        )
        dist.broadcast(data_recv_tensor, src=src_rank, group=group, async_op=False)
        buffer = io.BytesIO(data_recv_tensor.cpu().numpy())
        obj = torch.load(buffer, map_location=dist_device)
    return obj


def _get_global_rank(group: Any, rank: int) -> int:
    return rank if group is dist.group.WORLD else dist.distributed_c10d._get_global_rank(group, rank)  # type: ignore


class ZeroRedundancyOptimizer(Optimizer):
    """Wraps an arbitrary :class:`optim.Optimizer <torch.optim.Optimizer>`
    optimizer and shards its state as described by ZeRO_.
    ::

        opt = ZeroRedundancyOptimizer(params, optim=torch.optim.Adam, lr=0.01)


    We use a greedy algorithm to pack a number of parameters at each rank.
    Each parameter belongs to a single rank and is not divided among ranks.
    The partition is arbitrary and does not correspond to the information flow for instance.

    After each rank completed their parameter update, they broadcast
    the new version of the parameters to all other ranks to synchronize
    the parameters for next round forward/backward computation.

    Arguments:
        params (list of tensors):
            parameters to be optimized
    Keyword Args:
        optim (torch.nn.Optimizer): optimizer to shard
        group (group): torch.distributed group (default: group.WORLD)
        bucket_cap (int): the size of the buffer used to batch the small parameter tensors,
            in number of elements (default 16M)
        **default: all trailing arguments will be forwarded to the requested optimizer

    .. warning: ZeroRedundancyOptimizer is experimental and subject to change.

    .. _ZeRO: https://arxiv.org/abs/1910.02054

    """

    def __init__(
        self,
        params,
        optim: Type[Optimizer],
        group: Optional[Any] = None,
        bucket_cap_kb: int = 2 ** 24,
        **default: Any,
    ):
        # Hold all the model params in the root .param_groups
        # NOTE: the default constructor uses `add_param_group` which is partially overloaded here
        # we introduce the `initialized` flag for be able to dissociate the behaviour of
        # `add_param_group` in between super() and ZeroRedundancyOptimizer
        self.initialized = False
        super().__init__(params, default)

        # Partition information. lazy evaluation, computed if requested
        self._per_device_params: "OrderedDict[torch.device, List[List[Parameter]]]" = (
            OrderedDict()
        )  # device, rank, params
        self._param_rank: Dict[torch.Tensor, int] = {}
        self._param_to_index_cache: Dict[int, int] = {}
        self._partition_parameters: List[List[Dict]] = []
        self._index_to_param_cache: Dict[int, torch.Tensor] = {}

        # Build the wrapped optimizer, responsible for a shard of the params
        self.group = group if group is not None else dist.group.WORLD
        self.world_size = dist.get_world_size(self.group)
        self.rank = dist.get_rank(self.group)
        self.global_rank = _get_global_rank(self.group, self.rank)

        self.optim = optim(self.partition_parameters()[self.rank], **default)

        # - Sync local and global param_groups keys
        for global_group, local_group in zip(
            self.param_groups, self.optim.param_groups
        ):
            for k, v in local_group.items():
                if k != "params":
                    global_group[k] = v

        #  Optional consolidated optimizer state
        self._all_states: List[Dict[str, Any]] = []

        # Current default device is set by the parameters allocated to this rank
        self._device = list(self.per_device_params.keys())[0]
        self.buckets: Dict[torch.device, List[torch.Tensor]] = {}
        self.bucket_max_size = bucket_cap_kb

        self.should_bucket_param: List[bool] = []
        self._setup_bucket_strategy()
        self.initialized = True

    def _clear_cache(self) -> None:
        self._partition_parameters.clear()
        self._per_device_params.clear()
        self._param_rank.clear()
        self._index_to_param_cache.clear()
        self._param_to_index_cache.clear()

    def add_param_group(self, param_group: dict) -> None:
        """Add a param group to the :class:`Optimizer` s `param_groups`.

        This can be useful when fine tuning a pre-trained network as frozen layers can be made
        trainable and added to the :class:`Optimizer` as training progresses.

        Arguments:
            param_group (dict): Specifies what Tensors should be optimized along with group
                specific optimization options

        .. warning: This handles updating the shards on all partitions, but needs to be called on all ranks.
            Calling this on a subset of the ranks will cause the training to hang, because communication primitives
            are called depending on the managed parameters, and expect all the ranks to participate.
        """

        super().add_param_group(param_group)
        if self.initialized:
            # Force a re-partitioning
            self._clear_cache()

            param_groups = self.partition_parameters()[self.rank]
            if len(param_groups) == len(self.optim.param_groups) + 1:
                self.optim.add_param_group(param_groups[-1])

            # Update the bucketing strategy accordingly
            self._setup_bucket_strategy()

    def consolidate_state_dict(self, recipient_rank: int = 0) -> None:
        """Update the consolidated state_dict list, one per rank.

        .. warning: This needs to be called on all replicas"""

        # Sync lr and other attributes in case its been updated
        self._sync_param_groups(self.param_groups, self.optim.param_groups)

        empty_messenger = torch.tensor([0], dtype=torch.uint8, device=self._device)

        # Pull the sharded state from all the other replicas
        # Store all the states in order, rank by rank

        # NOTE: In practice, `broadcast` is used, which is wasteful (gather would have been appropriate)
        # compatibility issues with some backends make the use of broadcast mandatory for now.
        # a possible follow up would be to move all sharded state management to RPC RRef

        self._all_states = []
        for rank in range(self.world_size):
            global_rank = _get_global_rank(self.group, rank)

            # This rank collects the whole state
            if self.rank == recipient_rank:
                if rank == self.rank:
                    self._all_states.append(
                        _recursive_copy_to_device(
                            self.local_state_dict(),
                            non_blocking=True,
                            device=torch.device("cpu"),
                        )
                    )
                else:
                    # Fetch the optim state from the other replicas
                    replica_state = _broadcast_object(
                        empty_messenger,
                        src_rank=global_rank,
                        group=self.group,
                        dist_device=self._device,
                    )

                    self._all_states.append(
                        _recursive_copy_to_device(
                            replica_state, non_blocking=True, device=torch.device("cpu")
                        )
                    )
            else:
                # Acknowledge broadcasts, and send this rank's shard when needed
                # Default to CPU space to gain some memory headroom
                if rank == self.rank:
                    # Send the state to the reference replica
                    _ = _broadcast_object(
                        self.local_state_dict(),
                        src_rank=self.global_rank,
                        group=self.group,
                        dist_device=self._device,
                    )

                elif rank != recipient_rank:
                    # Discard this tensor/rank, broadcast was being use for compatibility reasons
                    _ = _broadcast_object(
                        empty_messenger,
                        src_rank=global_rank,
                        group=self.group,
                        dist_device=self._device,
                    )

    def partition_parameters(self) -> List[List[Dict]]:
        """Partitions parameters across distributed data parallel ranks.

        Returns: a list of ``param_groups`` (which is a list of dict) where each
            element of the list contains the param_groups for a rank. Element 0
            corresponds to rank 0, etc. We need all the ranks for the broadcast
            inside ``step()``.
        """
        if len(self._partition_parameters) == 0:
            self._partition_parameters = [list() for _ in range(self.world_size)]
            sizes = [0] * self.world_size
            for param_group in self.param_groups:
                param_lists: List[List] = [list() for _ in range(self.world_size)]
                for param in param_group["params"]:
                    # Add this param to rank with smallest size.
                    rank = sizes.index(min(sizes))
                    param_lists[rank].append(param)
                    sizes[rank] += param.numel()

                for rank, params in enumerate(param_lists):
                    param_group_rank = copy.copy(param_group)
                    param_group_rank["params"] = params
                    self._partition_parameters[rank].append(param_group_rank)

        return self._partition_parameters

    @property
    def per_device_params(self) -> Dict[torch.device, List[List[Parameter]]]:
        """Sorted list of all the params, first per device then per rank.

        Within a list params are sorted per number of elements to allow for an easy bucketing.
        """
        if len(self._per_device_params) == 0:
            # Go through all params, log them per device
            # The ordering is important here, needs to be the same on all ranks
            # So that ulterior broadcast calls are matching
            for param_group in self.param_groups:
                for param in param_group["params"]:
                    device = param.device
                    if self._per_device_params.get(device) is None:
                        self._per_device_params[device] = [
                            [] for _ in range(self.world_size)
                        ]
                    self._per_device_params[device][self.param_to_rank[param]] += [
                        param
                    ]

            # Sort param_lists by size
            for k in self._per_device_params.keys():
                for r in self._per_device_params[k]:
                    r.sort(key=lambda x: x.numel())

        return self._per_device_params

    @property
    def param_to_rank(self) -> Dict[torch.Tensor, int]:
        """Look up table to match a given param with a data parallel rank"""
        if len(self._param_rank) == 0:
            for rank, param_groups in enumerate(self.partition_parameters()):
                for param_group in param_groups:
                    for param in param_group["params"]:
                        self._param_rank[param] = rank
        return self._param_rank

    @property
    def _param_to_index(self) -> Dict[int, int]:
        """Hash table in between parameter indices in the global optimizer scheme, and the actual params"""
        if len(self._param_to_index_cache) == 0:
            self._param_to_index_cache = {
                id(p): i
                for i, p in enumerate(chain(*(g["params"] for g in self.param_groups)))
            }

        return self._param_to_index_cache

    @property
    def _index_to_param(self) -> Dict[int, torch.Tensor]:
        """Hash table in between parameter indices in the global optimizer scheme, and the actual params"""
        if len(self._index_to_param_cache) == 0:
            self._index_to_param_cache = {
                i: p
                for i, p in enumerate(chain(*(g["params"] for g in self.param_groups)))
            }

        return self._index_to_param_cache

    def step(
        self, closure: Optional[Callable[[], float]] = None, **kwargs: Any
    ) -> Optional[float]:
        """Performs a single optimization step (parameter update).

        Arguments:
            closure (callable): A closure that reevaluates the model and
                returns the loss. Optional for most optimizers.
        Returns:
            optional loss, depends on the underlying optimizer

        .. note: Any extra parameter is passed to the base optimizer as-is"""

        # Sync oss param_groups attributes in case they've been updated by a scheduler.
        self._sync_param_groups(self.param_groups, self.optim.param_groups)

        # Run the optimizer step on this shard only:
        if closure is not None:
            loss = self.optim.step(closure=closure, **kwargs)  # type: ignore
        else:
            loss = self.optim.step(**kwargs)

        # Sync all the updated shards in between the ranks
        self._broadcast_params()

        # Sync hypothethical new results from the wrapped optimizer to the exposed param_groups
        self._sync_param_groups(self.optim.param_groups, self.param_groups)

        return loss

    def load_state_dict(self, state_dict: Dict[str, Any]) -> None:
        """Restore the global parameter groups as well as the shard.

        Arguments:
            state_dict (dict): optimizer state. Should be an object returned
                from a call to :meth:`state_dict`
        """

        for key, value in state_dict["state"].items():
            param = self._index_to_param[key]

            # Populate the sharded optimizer state on the fly
            if self.param_to_rank[param] != self.rank:
                state_dict["state"][key] = None
            else:
                self.optim.state[param] = _recursive_copy_to_device(
                    value, non_blocking=True, device=param.device
                )

        super().load_state_dict(state_dict)

        # Sync with the optimizer param groups
        ZeroRedundancyOptimizer._sync_param_groups(
            state_dict["param_groups"], self.param_groups
        )
        ZeroRedundancyOptimizer._sync_param_groups(
            self.param_groups, self.optim.param_groups
        )

    def local_state_dict(self) -> Dict:
        """Gets this rank's ``state_dict``.

        Returns:
            The state of the optimizer as a :class:`dict`.
            It contains two entries:

            * state - a dict holding current optimization state. Its content
                differs between optimizer classes.
            * param_groups - a dict containing all parameter groups
        """
        return self.optim.state_dict()

    def state_dict(self) -> Dict[str, Any]:
        """
        Returns:
            the last known global optimizer state, which consist of a list of the shards.

        .. warning:
            If the state has not been consolidated, this returns a shard's worth, not the global state.

        .. warning:
            Returning the global state is limited to the replica which was responsible for the consolidation.
            The state may also not be up to date, depending on when `consolidate_state_dict` was last called.
        """

        if len(self._all_states) == 0:
            raise RuntimeError(
                "Optimizer state has not been consolidated on this rank. \
                Please call `consolidate_state_dict()` on all ranks beforehand if you meant to save the global state"
            )

        # Unify the shard states and the state that pytorch would expect, given the model.
        # Indexation needs several redirections, since each shard only knows a limited scope of the model
        # - get the pytorch compliant parameter indexing
        state_dict = super().state_dict()

        # - go through the per-shard states, which are all indexed locally
        for rank, s in enumerate(self._all_states):
            # -- match the local indexing and the global partition, update the corresponding saved state globally
            for local_pg, global_pg in zip(
                s["param_groups"], self.partition_parameters()[rank]
            ):
                local_index_to_param_id = {
                    i_param: id(global_pg["params"][i])
                    for i, i_param in enumerate(local_pg["params"])
                }

                for local_param_index in local_pg["params"]:
                    # Update the state, if any
                    if local_param_index in s["state"].keys():
                        global_id = self._param_to_index[
                            local_index_to_param_id[local_param_index]
                        ]
                        state_dict["state"][global_id] = s["state"][local_param_index]

        # Make sure that the parameters are sorted in the state, as expected
        state_dict["state"] = dict(sorted(state_dict["state"].items()))
        return state_dict

    @staticmethod
    def rank_local_state_dict(rank: int, state_dict: dict) -> dict:
        """Returns the local_state_dict for a given rank.

        Arguments:
            rank (int): rank to get local_state_dict for
            state_dict (dict): global state_dict
        """
        param_groups = state_dict["param_groups"][
            state_dict["partition"][rank][0] : state_dict["partition"][rank][1]
        ]
        return {"state": state_dict["state"][rank], "param_groups": param_groups}

    @staticmethod
    def _sync_param_groups(
        source: List[Dict[Any, Any]], destination: List[Dict[Any, Any]]
    ) -> None:
        """Sync learning rate and other optimizer attributes (needed to support schedulers)."""

        for source_group, destination_group in zip(source, destination):
            # Sync everything but the parameters
            for k in filter(lambda x: x != "params", source_group.keys()):
                destination_group[k] = source_group[k]

    def _broadcast_params(self) -> None:
        """Helper function to broadcast all the parameters from a given device"""

        i_param = 0
        handles: List[Any] = []

        for (
            device,
            device_params,
        ) in (
            self.per_device_params.items()
        ):  # all the params on this device (inc all ranks)
            buckets = self.buckets[device]
            # Bucket and issue all the async calls
            for (src_rank, params), bucket in zip(enumerate(device_params), buckets):
                global_src_rank = _get_global_rank(self.group, src_rank)

                # Direct broadcasts only
                for param in params:
                    if not self.should_bucket_param[i_param]:
                        handles.append(
                            dist.broadcast(
                                tensor=param.data,
                                src=global_src_rank,
                                group=self.group,
                                async_op=True,
                            )
                        )

                    i_param += 1

                # Bucket broadcasts
                if bucket.numel() > 0:
                    handles.append(
                        dist.broadcast(
                            tensor=bucket,
                            src=global_src_rank,
                            group=self.group,
                            async_op=True,
                        )
                    )

        # Wait for all the calls
        _ = list(map(lambda x: x.wait(), handles))

    def _setup_bucket_strategy(self) -> None:
        """Tag parameters to either bucket them or broadcast/reduce them directly.

        The parameters are ordered (smallest first), the bucket will hold the smallest elements,
        the remaining ones will be directly sent.

        Generating the partition once and for all allows us to save some time at runtime, and to know when all the
        network requests have been issued. The parameters which are part of a bucket become tensor views.
        """

        # Allocate one buffer per rank and per device to group the small parameters
        for device, per_device in self.per_device_params.items():
            self.buckets[device] = [
                torch.zeros(
                    self.bucket_max_size, dtype=per_device[0][0].dtype, device=device
                )
                for _ in range(len(per_device))
            ]

        # Pack the smallest elements in a bucket, depending on their owner shard-wise
        for device, per_rank_params in self.per_device_params.items():
            for dst_rank, params in enumerate(per_rank_params):
                offset = 0

                for param in params:
                    # Criteria to decide whether this parameter is to be bucketed or not:
                    # - enough room in the bucket
                    if (
                        param.requires_grad
                        and (offset + param.numel()) < self.bucket_max_size
                    ):
                        self.should_bucket_param.append(True)

                        # This parameter becomes a view of the bucket
                        offset_next = offset + param.numel()

                        self.buckets[device][dst_rank][
                            offset:offset_next
                        ] = param.data.flatten()
                        param.data = self.buckets[device][dst_rank][
                            offset:offset_next
                        ].view_as(param.data)
                        offset = offset_next
                    else:
                        self.should_bucket_param.append(False)

                # Resize the bucket to remove lost space in the end
                self.buckets[device][dst_rank].resize_(offset)
