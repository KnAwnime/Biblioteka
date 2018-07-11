from numbers import Number
import torch
from torch.distributions import constraints
from torch.distributions.distribution import Distribution
from torch.distributions.utils import broadcast_all, probs_to_logits, lazy_property, logits_to_probs


class NegativeBinomial(Distribution):
    r"""
        Creates a Negative Binomial distribution parameterized by
        a Bernoulli trial with probability `probs` of success that
        occur until we observe `total_count` number of failures.

    Args:
        total_count (int or Tensor): number of negative Bernoulli trials to stop
        probs (Tensor): Event probabilities of success
        logits (Tensor): Event log-odds for probabilities of success
    """

    arg_constraints = {'total_count': constraints.nonnegative_integer,
                       'probs': constraints.unit_interval}
    has_enumerate_support = True

    def __init__(self, total_count=1, probs=None, logits=None, validate_args=None):
        if (probs is None) == (logits is None):
            raise ValueError("Either `probs` or `logits` must be specified, but not both.")
        if probs is not None:
            self.total_count, self.probs, = broadcast_all(total_count, probs)
            self.total_count = self.total_count.type_as(self.logits)
            is_scalar = isinstance(self.probs, Number)
        else:
            self.total_count, self.logits, = broadcast_all(total_count, logits)
            self.total_count = self.total_count.type_as(self.logits)
            is_scalar = isinstance(self.logits, Number)

        self._param = self.probs if probs is not None else self.logits
        if is_scalar:
            batch_shape = torch.Size()
        else:
            batch_shape = self._param.size()
        super(NegativeBinomial, self).__init__(batch_shape, validate_args=validate_args)

    def _new(self, *args, **kwargs):
        return self._param.new(*args, **kwargs)

    @constraints.dependent_property
    def support(self):
        return constraints.integer_interval(0, self.total_count)

    @property
    def mean(self):
        return self.total_count * torch.exp(self.logits)

    @property
    def variance(self):
        return self.mean / torch.sigmoid(-self.logits)

    @lazy_property
    def logits(self):
        return probs_to_logits(self.probs, is_binary=True)

    @lazy_property
    def probs(self):
        return logits_to_probs(self.logits, is_binary=True)

    @property
    def param_shape(self):
        return self._param.size()

    def sample(self, sample_shape=torch.Size()):
        with torch.no_grad():
            rate = torch.gamma(concentration=self.total_count,
                               rate=(1-self.probs)/self.probs)
            return torch.poisson(rate)

    def log_prob(self, value):
        if self._validate_args:
            self._validate_sample(value)

        log_unnormalized_prob = self.total_count * torch.logsigmoid(-self.logits) +
            value * torch.logsigmoid(self.logits)

        log_normalization = -torch.lgamma(self.total_count + value) + torch.lgamma(1. + value) +
            torch.lgamma(self.total_count)

        return log_unnormalized_prob - log_normalization