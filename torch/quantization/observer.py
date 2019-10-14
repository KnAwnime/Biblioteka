from __future__ import absolute_import, division, print_function, unicode_literals

import math
import warnings
from abc import ABCMeta, abstractmethod
from functools import partial

import torch
import torch.nn as nn
from torch._jit_internal import List, Optional

def _with_args(cls_or_self, **kwargs):
    r"""Wrapper that allows creation of class factories.

    This can be useful when there is a need to create classes with the same
    constructor arguments, but different instances.

    .. Example::

        >>> Foo.with_args = classmethod(_with_args)
        >>> foo_builder = Foo.with_args(a=3, b=4).with_args(answer=42)
        >>> foo_instance1 = foo_builder()
        >>> foo_instance2 = foo_builder()
        >>> id(foo_instance1) == id(foo_instance2)
        False
    """
    class _PartialWrapper(object):
        def __init__(self, p):
            self.p = p

        def __call__(self, *args, **keywords):
            return self.p(*args, **keywords)

        def __repr__(self):
            return self.p.__repr__()

        with_args = _with_args
    r = _PartialWrapper(partial(cls_or_self, **kwargs))
    return r


ABC = ABCMeta(str("ABC"), (object,), {})  # compatible with Python 2 *and* 3:


class Observer(ABC, nn.Module):
    r"""Base observer Module.
    Any observer implementation should derive from this class.

    Concrete observers should follow the same API. In forward, they will update
    the statistics of the observed Tensor. And they should provide a
    `calculate_qparams` function that computes the quantization parameters given
    the collected statistics.

    Args:
        dtype: Quantized data type
    """
    def __init__(self, dtype):
        super(Observer, self).__init__()
        self.dtype = dtype

    @abstractmethod
    def forward(self, x):
        pass

    @abstractmethod
    def calculate_qparams(self, **kwargs):
        pass

    with_args = classmethod(_with_args)


class _ObserverBase(Observer):
    r"""Internal common base for all qint/quint8 observers.

    This base is for commonly used paramters used internally.
    Users should use `~torch.quantization.observer.Observer` as a base class for
    custom observers.

    Args:
        dtype: Quantized data type
        qscheme: Quantization scheme to be used
        reduce_range: Reduces the range of the quantized data type by 1 bit.
                      This is sometimes required to avoid instruction overflow.
    """

    def __init__(self, dtype=torch.quint8, qscheme=torch.per_tensor_affine,
                 reduce_range=False):
        super(_ObserverBase, self).__init__(dtype=dtype)
        self.qscheme = qscheme
        self.reduce_range = reduce_range

        self.eps = torch.finfo(torch.float32).eps
        assert self.qscheme in (
            torch.per_tensor_affine,
            torch.per_tensor_symmetric,
            torch.per_channel_affine,
            torch.per_channel_symmetric,
        ), "Default Observer only works for per_tensor_affine, \
                per_tensor_symmetric, per_channel_affine and \
                per_channel_symmetric quantization scheme"
        assert self.dtype in (
            torch.qint8,
            torch.quint8,
        ), "Default Observer only works for qint8 and quint8 data type"

    def _calculate_per_channel_qparams(self, min_vals, max_vals):
        # type: (Optional[Tensor], Optional[Tensor]) -> Tuple[Tensor, Tensor]
        r"""Calculates the per channel quantization parameters, given min and max
        value tensors.

        Args:
            min_vals: Minimum values per channel
            max_vals: Maximum values per channel

        Returns:
            scales: Per channel scales tensor of shape (#channels,)
            zero_points: Per channel zero points tensor of shape (#channels,)
        """
        if min_vals is None or max_vals is None:
            warnings.warn(
                "must run observer before calling calculate_qparams.\
                                    Returning default scale and zero point "
            )
            return torch.tensor([1.0]), torch.tensor([0])

        for i in range(len(min_vals)):
            assert (
                min_vals[i] <= max_vals[i]
            ), "min {} should be less than max {}".format(min_vals[i], max_vals[i])

        scales = torch.empty(min_vals.size(), dtype=torch.float32)
        zero_points = torch.empty(min_vals.size(), dtype=torch.int64)

        for i in range(len(scales)):
            qparam = self._calculate_qparams(
                min_vals[i], max_vals[i]
            )
            scales[i] = float(qparam[0])
            zero_points[i] = int(qparam[1])

        return scales, zero_points

    def _calculate_qparams(self, min_val, max_val):
        # type: (Optional[Tensor], Optional[Tensor]) -> Tuple[Tensor, Tensor]
        r"""Calculates the per tensor quantization parameters, given the min/max.

        Args:
            min_val: Per tensor minimum value
            max_val: Per tensor maximum value

        Returns:
            scale: Scale as a tensor of shape (1,)
            zero_point: Zero point as a tensor of shape (1,)
        """

        if max_val is None or min_val is None:
            warnings.warn("Must run observer before calling calculate_qparams.\
                           Returning default scale and zero point.")
            return torch.tensor([1.0]), torch.tensor([0])

        assert min_val <= max_val, "min {} should be less than max {}".format(
            min_val, max_val
        )

        if self.dtype == torch.qint8:
            if self.reduce_range:
                qmin, qmax = -64, 63
            else:
                qmin, qmax = -128, 127
        else:
            if self.reduce_range:
                qmin, qmax = 0, 127
            else:
                qmin, qmax = 0, 255

        max_val, min_val = float(max_val), float(min_val)
        min_val = min(0.0, min_val)
        max_val = max(0.0, max_val)
        if max_val == min_val:
            scale = 1.0
            zero_point = 0
        else:
            if self.qscheme == torch.per_tensor_symmetric or self.qscheme == torch.per_channel_symmetric:
                max_val = max(-min_val, max_val)
                scale = max_val / ((qmax - qmin) / 2)
                scale = max(scale, self.eps)
                zero_point = 0 if self.dtype == torch.qint8 else 128
            else:
                scale = (max_val - min_val) / float(qmax - qmin)
                scale = max(scale, self.eps)
                zero_point = qmin - round(min_val / scale)
                zero_point = max(qmin, zero_point)
                zero_point = min(qmax, zero_point)
                zero_point = int(zero_point)

        return torch.tensor([scale]), torch.tensor([zero_point])


class MinMaxObserver(_ObserverBase):
    r"""Observer module for computing the quantization parameters based on the
    running min and max values.

    This observer uses the tensor min/max statistics to compute the quantization
    parameters. The module records the running minimum and maximum of incoming
    tensors, and uses this statistic to compute the quantization parameters.

    Args:
        dtype: Quantized data type
        qscheme: Quantization scheme to be used
        reduce_range: Reduces the range of the quantized data type by 1 bit

    .. note:: Only works with ``torch.per_tensor_affine`` quantization shceme.

    .. note:: If the running minimum equals to the running maximum, the scale
              and zero_point are set to 1.0 and 0.
    """

    __annotations__ = {
        "min_val": Optional[torch.Tensor],
        "max_val": Optional[torch.Tensor],
    }

    def __init__(self, dtype=torch.quint8, qscheme=torch.per_tensor_affine,
                 reduce_range=False):
        # For x86 quantized kernels, we need to ensure that the vpmaddubsw
        # instruction does not overflow. We allow for a reduce_range argument to
        # observers that reduces the quantized range to (0,127) or (-64, 63).
        # For more details see aten/src/ATen/native/quantized/cpu/qconv.cpp
        # This is not an optimal choice for non x86 backends as it loses a bit
        # of precision for activations.

        super(MinMaxObserver, self).__init__(dtype=dtype,
                                             qscheme=qscheme,
                                             reduce_range=reduce_range)
        self.min_val = None
        self.max_val = None
        if self.qscheme == torch.per_tensor_symmetric and \
           self.reduce_range and \
           self.dtype == torch.quint8:
            raise NotImplementedError("Cannot reduce range for symmetric \
                                       quantization for quint8")

    def forward(self, x_orig):
        r"""Records the running minimum and maximum of ``x``."""
        x = x_orig.detach()
        min_val = self.min_val
        max_val = self.max_val
        if min_val is None or max_val is None:
            min_val = torch.min(x)
            max_val = torch.max(x)
        else:
            min_val = torch.min(torch.min(x), min_val)
            max_val = torch.max(torch.max(x), max_val)
        self.min_val = min_val
        self.max_val = max_val
        return x_orig

    @torch.jit.export
    def calculate_qparams(self):
        r"""Calculates the quantization parameters.

        Given running min/max as :math:`r_\text{min}` and :math:`r_\text{max}`,
        scale :math:`s` and zero point :math:`z` are computed as:

        .. math::

            \begin{aligned}
                \text{if Symmetric:}&\\
                &s = 2 r_\text{max} / \left( Q_\text{max} - Q_\text{min} \right) \\
                &z = \begin{cases}
                    0 & \text{if dtype is qint8} \\
                    128 & \text{otherwise}
                \end{cases}\\
                \text{Otherwise:}&\\
                    &s = \left( r_\text{max} - r_\text{min}  \right ) / \left( Q_\text{max} - Q_\text{min} \right ) \\
                    &z = Q_\text{min} - \text{round}(r_\text{min} / s)
            \end{aligned}

        where :math:`Q_\text{min}` and :math:`Q_\text{max}` are the minimum and
        maximum of the quantized data type.
        """
        return self._calculate_qparams(self.min_val, self.max_val)

    @torch.jit.export
    def extra_repr(self):
        return "min_val={}, max_val={}".format(self.min_val, self.max_val)

    def _save_to_state_dict(self, destination, prefix, keep_vars):
        super(MinMaxObserver, self)._save_to_state_dict(destination, prefix, keep_vars)
        destination[prefix + 'min_val'] = self.min_val
        destination[prefix + 'max_val'] = self.max_val

    def _load_from_state_dict(self, state_dict, prefix, local_metadata, strict,
                              missing_keys, unexpected_keys, error_msgs):

        self.min_val = state_dict.pop(prefix + 'min_val')
        self.max_val = state_dict.pop(prefix + 'max_val')
        super(MinMaxObserver, self)._load_from_state_dict(state_dict, prefix, local_metadata, False,
                                                          missing_keys, unexpected_keys, error_msgs)


class MovingAverageMinMaxObserver(MinMaxObserver):
    def __init__(self, averaging_constant=0.01, **kwargs):
        self.averaging_constant = averaging_constant
        super(MovingAverageMinMaxObserver, self).__init__(**kwargs)

    def forward(self, x_orig):
        x = x_orig.detach()  # avoid keeping autograd tape
        min_val = self.min_val
        max_val = self.max_val
        if min_val is None or max_val is None:
            min_val = torch.min(x)
            max_val = torch.max(x)
        else:
            min_val = min_val + self.averaging_constant * (torch.min(x) - min_val)
            max_val = max_val + self.averaging_constant * (torch.max(x) - max_val)
        self.min_val = min_val
        self.max_val = max_val
        return x_orig


class PerChannelMinMaxObserver(_ObserverBase):
    r"""Per Channel Observer Module
    The module will record the running average of max and min value for each
    channel of the observed Tensor and calculate_qparams will calculate
    scales and zero_points for each channel
    """

    def __init__(self, ch_axis=0, **kwargs):
        super(PerChannelMinMaxObserver, self).__init__(**kwargs)
        self.ch_axis = ch_axis
        self.register_buffer('min_vals', None)
        self.register_buffer('max_vals', None)
        if (
            self.qscheme == torch.per_channel_symmetric
            and self.reduce_range
            and self.dtype == torch.quint8
        ):
            raise NotImplementedError(
                "Cannot reduce range for symmetric quantization for quint8"
            )

    def forward(self, x_orig):
        x = x_orig.detach()  # avoid keeping autograd tape
        min_vals = self.min_vals
        max_vals = self.max_vals
        x_dim = x.size()

        new_axis_list = list(range(len(x_dim)))
        new_axis_list[self.ch_axis] = 0
        new_axis_list[0] = self.ch_axis
        y = x.permute(tuple(new_axis_list))
        y = torch.flatten(y, start_dim=1)
        if min_vals is None or max_vals is None:
            min_vals = torch.min(y, 1)[0]
            max_vals = torch.max(y, 1)[0]
        else:
            min_vals = torch.min(torch.min(y, 1)[0], min_vals)
            max_vals = torch.max(torch.max(y, 1)[0], max_vals)
        self.min_vals = min_vals
        self.max_vals = max_vals
        return x_orig

    def calculate_qparams(self):
        return self._calculate_per_channel_qparams(self.min_vals, self.max_vals)

    def extra_repr(self):
        return "min_val={}, max_val={}".format(self.min_vals, self.max_vals)

    def _load_from_state_dict(self, state_dict, prefix, local_metadata, strict,
                              missing_keys, unexpected_keys, error_msgs):
        # We have to handle min_vals and max_vals manually even though they are registered as buffers
        # as they are initialized to None
        self.min_vals = state_dict.pop(prefix + 'min_vals')
        self.max_vals = state_dict.pop(prefix + 'max_vals')
        super(PerChannelMinMaxObserver, self)._load_from_state_dict(state_dict, prefix, local_metadata, False,
                                                                    missing_keys, unexpected_keys, error_msgs)

class MovingAveragePerChannelMinMaxObserver(PerChannelMinMaxObserver):
    r"""Per Channel Observer Module
    The module will record the running average of max and min value for each
    channel of the observed Tensor and calculate_qparams will calculate
    scales and zero_points for each channel
    """

    def __init__(self, averaging_constant=0.01, **kwargs):
        self.averaging_constant = averaging_constant
        super(MovingAveragePerChannelMinMaxObserver, self).__init__(**kwargs)

    def forward(self, x_orig):
        x = x_orig.detach()  # avoid keeping autograd tape
        min_vals = self.min_vals
        max_vals = self.max_vals
        x_dim = x.size()

        new_axis_list = list(range(len(x_dim)))
        new_axis_list[self.ch_axis] = 0
        new_axis_list[0] = self.ch_axis
        y = x.permute(tuple(new_axis_list))
        y = torch.flatten(y, start_dim=1)
        if min_vals is None or max_vals is None:
            min_vals = torch.min(y, 1)[0]
            max_vals = torch.max(y, 1)[0]
        else:
            min_vals = min_vals + self.averaging_constant * (torch.min(y, 1)[0] - min_vals)
            max_vals = max_vals + self.averaging_constant * (torch.max(y, 1)[0] - max_vals)
        self.min_vals = min_vals
        self.max_vals = max_vals
        return x_orig

class HistogramObserver(_ObserverBase):
    r"""
    The module records the running histogram of tensor values along with
    min/max values. calculate_qparams will calculate scale and zero_point
    """

    __annotations__ = {
        "min_val": Optional[torch.Tensor],
        "max_val": Optional[torch.Tensor],
    }

    def __init__(self, bins=2048, **kwargs):
        # bins: The number of bins used for histogram calculation.
        super(HistogramObserver, self).__init__(**kwargs)
        self.bins = bins
        self.register_buffer('histogram', torch.zeros(self.bins))
        self.min_val = None
        self.max_val = None

    @staticmethod
    def _get_norm(delta_begin, delta_end, density, norm_type):
        r"""
        Compute the norm of the values uniformaly distributed between
        delta_begin and delta_end.

        norm = density * (integral_{begin, end} x^2)
             = density * (end^3 - begin^3) / 3
        """
        assert norm_type == "L2", "Only L2 norms are currently supported"
        norm = 0.0
        if norm_type == "L2":
            norm = (
                delta_end * delta_end * delta_end
                - delta_begin * delta_begin * delta_begin
            ) / 3
        return density * norm

    def _compute_quantization_error(self, next_start_bin, next_end_bin, norm_type):
        r"""
        Compute the quantization error if we use start_bin to end_bin as the
        min and max to do the quantization.
        """
        dst_nbins = 2 ** torch.iinfo(self.dtype).bits
        bin_width = (self.max_val.item() - self.min_val.item()) / self.bins

        norm = 0.0
        dst_bin_width = bin_width * (next_end_bin - next_start_bin + 1) / dst_nbins
        if dst_bin_width == 0.0:
            return 0.0
        for src_bin in range(self.bins):
            # distances from the beginning of first dst_bin to the beginning and
            # end of src_bin
            src_bin_begin = (src_bin - next_start_bin) * bin_width
            src_bin_end = src_bin_begin + bin_width

            # which dst_bins the beginning and end of src_bin belong to?
            dst_bin_of_begin = min(
                dst_nbins - 1, max(0.0, math.floor(src_bin_begin / dst_bin_width))
            )
            dst_bin_of_end = min(
                dst_nbins - 1, max(0.0, math.floor(src_bin_end / dst_bin_width))
            )
            dst_bin_of_begin_center = (
                dst_bin_of_begin * dst_bin_width + dst_bin_width / 2
            )

            density = self.histogram[src_bin] / bin_width
            if dst_bin_of_begin == dst_bin_of_end:
                # if src_bin is entirely within 1 dst_bin
                delta_begin = src_bin_begin - dst_bin_of_begin_center
                delta_end = src_bin_end - dst_bin_of_begin_center
                norm = norm + self._get_norm(delta_begin, delta_end, density, norm_type)
            else:
                delta_begin = src_bin_begin - dst_bin_of_begin_center
                delta_end = dst_bin_width / 2
                norm = norm + self._get_norm(delta_begin, delta_end, density, norm_type)

                norm = norm + (dst_bin_of_end - dst_bin_of_begin - 1) * self._get_norm(
                    -dst_bin_width / 2, dst_bin_width / 2, density, norm_type
                )

                dst_bin_of_end_center = (
                    dst_bin_of_end * dst_bin_width + dst_bin_width / 2
                )

                delta_begin = -dst_bin_width / 2
                delta_end = src_bin_end - dst_bin_of_end_center
                norm = norm + self._get_norm(delta_begin, delta_end, density, norm_type)
        return norm

    def _non_linear_param_search(self):
        r"""
        An approximation for L2 error minimization for selecting min/max.
        By selecting new min/max, we filter out outliers in input distribution.
        This follows the implementation of NormMinimization::NonlinearQuantizationParamsSearch in
        caffe2/quantization/server/norm_minimization.cc
        """
        assert self.histogram.size()[0] == self.bins, "bins mistmatch"
        bin_width = (self.max_val - self.min_val) / self.bins

        # cumulative sum
        total = sum(self.histogram)
        cSum = torch.cumsum(self.histogram, dim=0)

        stepsize = 1e-5
        alpha = 0.0
        beta = 1.0
        start_bin = 0
        end_bin = self.bins - 1
        norm_min = float("inf")

        while alpha < beta:
            next_alpha = alpha + stepsize
            next_beta = beta - stepsize

            # find the left and right bins between the quantile bounds
            l = start_bin
            r = end_bin
            while l < end_bin and cSum[l] < next_alpha * total:
                l = l + 1
            while r > start_bin and cSum[r] > next_beta * total:
                r = r - 1

            next_start_bin = start_bin
            next_end_bin = end_bin
            if (l - start_bin) > (end_bin - r):
                next_start_bin = l
                alpha = next_alpha
            else:
                next_end_bin = r
                beta = next_beta

            if next_start_bin == start_bin and next_end_bin == end_bin:
                continue

            # calculate the quantization error using next_start_bin and next_end_bin
            norm = self._compute_quantization_error(next_start_bin, next_end_bin, "L2")

            if norm > norm_min:
                break
            norm_min = norm
            start_bin = next_start_bin
            end_bin = next_end_bin

        new_min = self.min_val + bin_width * start_bin
        new_max = self.min_val + bin_width * (end_bin + 1)
        return new_min, new_max

    def _combine_histograms(
        self, dst_histogram, dst_min, dst_max, src_histogram, src_min, src_max
    ):
        bins_dst = dst_histogram.size()[0]
        bins_src = src_histogram.size()[0]

        dst_bin_width = (dst_max - dst_min) / bins_dst
        src_bin_width = (src_max - src_min) / bins_src

        for i in range(bins_src):
            src_bin_count = src_histogram[i].item()
            if src_bin_count == 0:
                continue

            src_bin_begin = src_min + src_bin_width * i
            src_bin_end = src_bin_begin + src_bin_width

            dst_bin = 0
            if dst_bin_width:
                dst_bin = int((src_bin_begin - dst_min) / dst_bin_width)

            dst_bin_begin = dst_min + dst_bin_width * dst_bin
            dst_bin_end = dst_bin_begin + dst_bin_width

            dst_bin2 = 0
            if dst_bin_width:
                dst_bin2 = min(
                    int((src_bin_end - dst_min) / dst_bin_width), bins_dst - 1
                )

            assert dst_bin2 <= dst_bin + 2, "1 src_bin is mapped to at most 2 dst_bins"
            # dst_bin_cnt is the count from src_bin that should go to dst_bin
            # the remainder should go to dst_bin2
            dst_bin_cnt = 0
            if src_bin_width == 0 or dst_bin_width == 0:
                dst_bin_cnt = src_bin_count
            else:
                # We divide counts in src_bin in proportion to range overlap with dst_bin
                dst_bin_cnt = min(
                    round(
                        (dst_bin_end - src_bin_begin) / src_bin_width * src_bin_count
                    ),
                    src_bin_count,
                )

            dst_histogram[dst_bin] += dst_bin_cnt

            # remaining should go to dst_bin2
            if dst_bin_cnt < src_bin_count:
                dst_histogram[dst_bin2] += src_bin_count - dst_bin_cnt

    def forward(self, x):
        with torch.no_grad():
            min_val = self.min_val
            max_val = self.max_val
            if min_val is None or max_val is None:
                min_val = torch.min(x)
                max_val = torch.max(x)
                self.min_val = min_val
                self.max_val = max_val
                self.histogram = torch.histc(x, self.bins, min=min_val, max=max_val)
            else:
                new_min = torch.min(x)
                new_max = torch.max(x)
                new_histogram = torch.histc(x, self.bins, min=new_min, max=new_max)
                # combine the existing histogram and new histogram into 1 histogram
                combined_histogram = torch.zeros_like(self.histogram)
                combined_min = torch.min(new_min, self.min_val)
                combined_max = torch.max(new_max, self.max_val)
                self._combine_histograms(
                    combined_histogram,
                    combined_min.item(),
                    combined_max.item(),
                    self.histogram,
                    self.min_val.item(),
                    self.max_val.item(),
                )
                self._combine_histograms(
                    combined_histogram,
                    combined_min.item(),
                    combined_max.item(),
                    new_histogram,
                    new_min.item(),
                    new_max.item(),
                )
                self.histogram = combined_histogram
                self.min_val = combined_min
                self.max_val = combined_max
        return x

    def calculate_qparams(self):
        if self.min_val is None or self.max_val is None:
            warnings.warn(
                "must run observer before calling calculate_qparams.\
                                    Returning default scale and zero point "
            )
            return torch.tensor([1.0]), torch.tensor([0])
        assert self.bins == len(self.histogram), (
            "The number of bins in histogram should be equal to the number of bins "
            "supplied while making this observer"
        )

        new_min, new_max = self._non_linear_param_search()

        return self._calculate_qparams(new_min.item(), new_max.item())

    def _save_to_state_dict(self, destination, prefix, keep_vars):
        super(HistogramObserver, self)._save_to_state_dict(destination, prefix, keep_vars)
        destination[prefix + 'min_val'] = self.min_val
        destination[prefix + 'max_val'] = self.max_val

    def _load_from_state_dict(self, state_dict, prefix, local_metadata, strict,
                              missing_keys, unexpected_keys, error_msgs):
        self.min_val = state_dict.pop(prefix + 'min_val')
        self.max_val = state_dict.pop(prefix + 'max_val')
        super(HistogramObserver, self)._load_from_state_dict(state_dict, prefix, local_metadata, False,
                                                             missing_keys, unexpected_keys, error_msgs)

class RecordingObserver(_ObserverBase):
    r"""
    The module is mainly for debug and records the tensor values during runtime
    """
    __annotations__ = {"tensor_val": List[Optional[torch.Tensor]]}

    def __init__(self, **kwargs):
        super(RecordingObserver, self).__init__(**kwargs)
        self.tensor_val = []

    def forward(self, x):
        self.tensor_val.append(x.clone())
        return x

    @torch.jit.export
    def calculate_qparams(self):
        raise Exception("calculate_qparams should not be called for RecordingObserver")

    @torch.jit.export
    def get_tensor_value(self):
        return self.tensor_val


class NoopObserver(Observer):
    r"""
    Observer that doesn't do anything and just passes its configuration to the
    quantized module's ``.from_float()``.

    Primarily used for quantization to float16 which doesn't require determining
    ranges.
    """
    def __init__(self, dtype=torch.float16):
        if dtype != torch.float16:
            raise ValueError("Only float16 quantization can be used without calibration process")
        super(NoopObserver, self).__init__(dtype=dtype)

    def forward(self, x):
        return x

    def calculate_qparams(self):
        raise Exception("calculate_qparams should not be called for NoopObserver")


# Restrict activations to be in the range (0,127)
default_observer = MinMaxObserver.with_args(reduce_range=True)
default_debug_observer = RecordingObserver
default_weight_observer = MinMaxObserver.with_args(dtype=torch.qint8, qscheme=torch.per_tensor_symmetric)
default_histogram_observer = HistogramObserver.with_args(reduce_range=True)
default_per_channel_weight_observer = PerChannelMinMaxObserver.with_args(dtype=torch.qint8, qscheme=torch.per_channel_symmetric)
