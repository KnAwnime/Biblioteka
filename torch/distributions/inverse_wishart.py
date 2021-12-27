import math
import warnings
from numbers import Number
from typing import Union

import torch
from torch.distributions import constraints
from torch.distributions.exp_family import ExponentialFamily
from torch.distributions.utils import lazy_property
from torch.distributions.multivariate_normal import _precision_to_scale_tril


_log_2 = math.log(2)


def _mvdigamma(x: torch.Tensor, p: int) -> torch.Tensor:
    assert x.gt((p - 1) / 2).all(), "Wrong domain for multivariate digamma function."
    return torch.digamma(x.unsqueeze(-1) - torch.arange(p).div(2).expand(x.shape + (-1,))).sum(-1)


class InverseWishart(ExponentialFamily):
    r"""
    Creates a Inverse Wishart distribution parameterized by a symmetric positive definite matrix :math:`\Sigma`,
    or its Cholesky decomposition :math:`\mathbf{\Sigma} = \mathbf{L}\mathbf{L}^\top`

    Example:
        >>> m = InverseWishart(torch.eye(2), torch.Tensor([2]))
        >>> m.sample()  # Inverse Wishart distributed with mean=`df * I` and
                        # variance(x_ij)=`df` for i != j and variance(x_ij)=`2 * df` for i == j
    Args:
        covariance_matrix (Tensor): positive-definite covariance matrix
        precision_matrix (Tensor): positive-definite precision matrix
        scale_tril (Tensor): lower-triangular factor of covariance, with positive-valued diagonal
        df (float or Tensor): real-valued parameter larger than the (dimension of Square matrix) - 1
    Note:
        Only one of :attr:`covariance_matrix` or :attr:`precision_matrix` or
        :attr:`scale_tril` can be specified.
        Using :attr:`scale_tril` will be more efficient: all computations internally
        are based on :attr:`scale_tril`. If :attr:`covariance_matrix` or
        :attr:`precision_matrix` is passed instead, it is only used to compute
        the corresponding lower triangular matrices using a Cholesky decomposition.
        Inverse Wishart distirbution is inverse transformed distribution of `torch.distributions.wishart.Wishart`
        wishart and inverse wishart distributions may exhibit other advanctages according to the use case.
        :math:`\mathbf{\Sigma}` follows an inverse Wishart distribution with df :math:`\nu` and covariance matrix :math:`\Psi`
        when :math:`\mathbf{\Sigma}^{-1}` follows an Wishart distribution with df :math:`\nu + p - 1` and
        covariance matrix :math:`\Psi^{-1}`
    """
    arg_constraints = {
        'covariance_matrix': constraints.positive_definite,
        'precision_matrix': constraints.positive_definite,
        'scale_tril': constraints.lower_cholesky,
        'df': constraints.greater_than(0),
    }
    support = constraints.positive_definite
    has_rsample = True
    _mean_carrier_measure = 0

    def __init__(self,
                 df: Union[torch.Tensor, Number],
                 covariance_matrix: torch.Tensor = None,
                 precision_matrix: torch.Tensor = None,
                 scale_tril: torch.Tensor = None,
                 validate_args=None):
        assert (covariance_matrix is not None) + (scale_tril is not None) + (precision_matrix is not None) == 1, \
            "Exactly one of covariance_matrix or precision_matrix or scale_tril may be specified."

        param = next(p for p in (covariance_matrix, precision_matrix, scale_tril) if p is not None)

        assert param.dim() > 1, \
            "scale_tril must be at least two-dimensional, with optional leading batch dimensions"

        if isinstance(df, Number):
            batch_shape = torch.Size(param.shape[:-2])
            self.df = torch.tensor(df, dtype=param.dtype, device=param.device)
        else:
            batch_shape = torch.broadcast_shapes(param.shape[:-2], df.shape)
            self.df = df.expand(batch_shape)
        event_shape = param.shape[-2:]

        if scale_tril is not None:
            self.scale_tril = param.expand(batch_shape + (-1, -1))
        elif covariance_matrix is not None:
            self.covariance_matrix = param.expand(batch_shape + (-1, -1))
        elif precision_matrix is not None:
            self.precision_matrix = param.expand(batch_shape + (-1, -1))

        self.arg_constraints['df'] = constraints.greater_than(event_shape[-1] - 1)
        if self.df.lt(event_shape[-1]).any():
            warnings.warn("Low df values detected. Singular samples are highly likely to occur for ndim - 1 < df < ndim.")

        super(InverseWishart, self).__init__(batch_shape, event_shape, validate_args=validate_args)
        self._batch_dims = [-(x + 1) for x in range(len(self._batch_shape))]

        if scale_tril is not None:
            self._unbroadcasted_scale_tril = scale_tril
        elif covariance_matrix is not None:
            self._unbroadcasted_scale_tril = torch.linalg.cholesky(covariance_matrix)
        else:  # precision_matrix is not None
            self._unbroadcasted_scale_tril = _precision_to_scale_tril(precision_matrix)

        # Chi2 distribution is needed for Bartlett decomposition sampling
        self._dist_chi2 = torch.distributions.chi2.Chi2(
            df=(
                self.df.unsqueeze(-1)
                - torch.arange(
                    self._event_shape[-1],
                    dtype=self._unbroadcasted_scale_tril.dtype,
                    device=self._unbroadcasted_scale_tril.device,
                ).expand(batch_shape + (-1,))
            )
        )

    def expand(self, batch_shape, _instance=None):
        new = self._get_checked_instance(InverseWishart, _instance)
        batch_shape = torch.Size(batch_shape)
        cov_shape = batch_shape + self.event_shape
        df_shape = batch_shape
        new._unbroadcasted_scale_tril = self._unbroadcasted_scale_tril.expand(cov_shape)
        new.df = self.df.expand(df_shape)

        new._batch_dims = [-(x + 1) for x in range(len(batch_shape))]

        if 'covariance_matrix' in self.__dict__:
            new.covariance_matrix = self.covariance_matrix.expand(cov_shape)
        if 'scale_tril' in self.__dict__:
            new.scale_tril = self.scale_tril.expand(cov_shape)
        if 'precision_matrix' in self.__dict__:
            new.precision_matrix = self.precision_matrix.expand(cov_shape)

        super(InverseWishart, new).__init__(batch_shape, self.event_shape, validate_args=False)
        new._validate_args = self._validate_args
        return new

    @lazy_property
    def scale_tril(self):
        return self._unbroadcasted_scale_tril.expand(
            self._batch_shape + self._event_shape)

    @lazy_property
    def covariance_matrix(self):
        return (
            self._unbroadcasted_scale_tril @ self._unbroadcasted_scale_tril.transpose(-2, -1)
        ).expand(self._batch_shape + self._event_shape)

    @lazy_property
    def precision_matrix(self):
        identity = torch.eye(
            self._event_shape[-1],
            device=self._unbroadcasted_scale_tril.device,
            dtype=self._unbroadcasted_scale_tril.dtype,
        )
        return torch.cholesky_solve(
            identity, self._unbroadcasted_scale_tril
        ).expand(self._batch_shape + self._event_shape)

    @property
    def mean(self):
        return self.covariance_matrix.div(self.df - self._event_shape[-1] - 1)

    @property
    def variance(self):
        nu = self.df # has shape (batch_shape)
        p = self._event_shape[-1]

        V = self.covariance_matrix  # has shape (batch_shape x event_shape)
        diag_V = V.diagonal(dim1=-2, dim2=-1)

        return (
            (v - p + 1) * V.pow(2)
            + (v - p - 1) * torch.einsum("...i,...j->...ij", diag_V, diag_V)
        ) / ((v - p) * (v - p - 1).pow(2) * (v - p - 3))

    def rsample(self, sample_shape=torch.Size(), max_try_correction=None):
        raise NotImplementedError

    def log_prob(self, value):
        if self._validate_args:
            self._validate_sample(value)
        nu = self.df  # has shape (batch_shape)
        p = self._event_shape[-1]  # has singleton shape
        return (
            - nu * p * _log_2 / 2
            - nu * self._unbroadcasted_scale_tril.diagonal(dim1=-2, dim2=-1).log().sum(-1)
            - torch.mvlgamma(nu / 2, p=p)
            - (nu + p + 1) / 2 * torch.linalg.slogdet(value).logabsdet
            - torch.cholesky_solve(value, self._unbroadcasted_scale_tril).diagonal(dim1=-2, dim2=-1).reciprocal().sum(dim=-1) / 2
        )

    def entropy(self):
        nu = self.df  # has shape (batch_shape)
        p = self._event_shape[-1]  # has singleton shape
        V = self.covariance_matrix  # has shape (batch_shape x event_shape)
        return (
            (p + 1) * self._unbroadcasted_scale_tril.diagonal(dim1=-2, dim2=-1).log().sum(-1)
            - p * (p + 1) * _log_2 / 2
            + torch.mvlgamma(nu / 2, p=p)
            - (nu + p + 1) / 2 * _mvdigamma(nu / 2, p=p)
            + nu * p / 2
        )

    @property
    def _natural_params(self):
        return (
            0.5 * self.df,
            - 0.5 * self.covariance_matrix
        )

    def _log_normalizer(self, x, y):
        raise NotImplementedError
