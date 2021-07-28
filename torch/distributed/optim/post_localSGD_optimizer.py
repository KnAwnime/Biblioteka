from typing import Any, Iterator, Type

import torch
import torch.distributed.algorithms.model_averaging.averagers as averagers


class PostLocalSGDOptimizer(torch.optim.Optimizer):
    r"""
    Wraps an arbitrary :class:`torch.optim.Optimizer` and runs `post-local SGD <https://arxiv.org/abs/1808.07217>`_,
    This optimizer runs local optimizer at every step.
    After the warm-up stage, it averages parameters periodically afer the local optimizer is applied.

    Args:
        params: All the parameters.
        optimizer_class: The class of the local optimizer.
        averager: A model averager instance to run post-localSGD algorithm.
        **defaults: A dict containing default values of optimization options,
            which are forwarded to the local optimizer.

    Example::

        >>>  import torch
        >>>  import torch.distributed as dist
        >>>  import torch.distributed.algorithms.model_averaging.averagers as averagers
        >>>  import torch.nn as nn
        >>>  from torch.distributed.optim import PostLocalSGDOptimizer
        >>>
        >>>  model = nn.parallel.DistributedDataParallel(
        >>>     module, device_ids=[rank], output_device=rank
        >>>  )
        >>>
        >>>  # Register a post-localSGD communication hook.
        >>>  subgroup, subgroups = dist.new_subgroups()
        >>>  state = PostLocalSGDState(subgroup=subgroup, start_localSGD_iter=100)
        >>>  model.register_comm_hook(state, post_localSGD_hook)
        >>>
        >>>  # Create a post-localSGD optimizer that wraps a local optimizer.
        >>>  # Note that ``warmup_steps`` used in ``PostLocalSGDOptimizer`` must be the same as
        >>>  # ``start_localSGD_iter`` used in ``PostLocalSGDState``.
        >>>  opt = PostLocalSGDOptimizer(
        >>>      model.parameters(),
        >>>      optimizer_class=torch.optim.SGD,
        >>>      averager=averagers.PeriodicModelAverager(model.parameters(), period=4, warmup_steps=100),
        >>>      lr=0.01
        >>>  )
        >>>
        >>>  # In the first 100 steps, DDP runs global gradient averaging at every step.
        >>>  # After 100 steps, DDP runs gradient averaging within each subgroup (intra-node by default),
        >>>  # and post-localSGD optimizer runs global model averaging every 4 steps after applying the local optimizer.
        >>>  for step in range(0, 20):
        >>>     opt.zero_grad()
        >>>     loss = loss_fn(output, labels)
        >>>     loss.backward()
        >>>     opt.step()

    .. warning ::
        `PostLocalSDGOptimizer` is experimental and subject to change.
    """

    def __init__(
        self,
        params: Iterator[torch.nn.Parameter],
        optimizer_class: Type[torch.optim.Optimizer],
        averager: averagers.ModelAverager,
        **defaults: Any,
    ):
        self.local_optimizer = optimizer_class(params, **defaults)
        self.param_groups = self.local_optimizer.param_groups
        self.averager = averager

        self.steps = 0

    def step(self):
        r"""
        Performs a single optimization step (parameter update).
        """
        self.local_optimizer.step()
        self.averager.average_parameters()

    def zero_grad(self):
        self.local_optimizer.zero_grad()

    def state_dict(self):
        raise NotImplementedError

    def load_state_dict(self, state_dict):
        raise NotImplementedError
