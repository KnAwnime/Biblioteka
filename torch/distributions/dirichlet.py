from numbers import Number

import torch
from torch.autograd import Function, Variable
from torch.autograd.function import once_differentiable
from torch.distributions.distribution import Distribution
from torch.distributions.utils import expand_n


def _dirichlet_sample_nograd(alpha):
    gammas = torch._C._standard_gamma(alpha)
    return gammas / gammas.sum(-1, True)


class _Dirichlet(Function):
    @staticmethod
    def forward(ctx, alpha):
        x = _dirichlet_sample_nograd(alpha)
        ctx.save_for_backward(x, alpha)
        return x

    @staticmethod
    @once_differentiable
    def backward(ctx, grad_output):
        x, alpha = ctx.saved_tensors
        total = alpha.sum(-1, True).expand_as(alpha)
        grad = torch._C._dirichlet_grad(x, alpha, total)
        return grad_output * grad


class Dirichlet(Distribution):
    r"""
    Creates a Dirichlet distribution parameterized by concentration `alpha`.

    Example::

        >>> m = Dirichlet(torch.Tensor([0.5, 0.5]))
        >>> m.sample()  # Dirichlet distributed with concentrarion alpha
         0.1046
         0.8954
        [torch.FloatTensor of size 2]

    Args:
        alpha (Tensor or Variable): concentration parameter of the distribution
    """
    has_rsample = True

    def __init__(self, alpha):
        self.alpha = alpha

    def _sample(self, alpha):
        if not isinstance(alpha, Variable):
            return _dirichlet_sample_nograd(alpha)
        return _Dirichlet.apply(alpha)

    def sample(self):
        return self._sample(self.alpha)

    def sample_n(self, n):
        return self._sample(expand_n(self.alpha, n))

    def log_prob(self, value):
        return ((torch.log(value) * (self.alpha - 1.0)).sum(-1) +
                torch.lgamma(self.alpha.sum(-1)) -
                torch.lgamma(self.alpha).sum(-1))
