import torch
from torch.ao.quantization.observer import ObserverBase


class ModelReportObserver(ObserverBase):
    r"""This observer is used to record additional information regarding keeping track
    of S = average_batch_activation_range/epoch_activation_range.

    The purpose of this information is to prepare a report to present to users on whether
    Dynamic or Static Quantization is more appropriate for their model given the general
    distributions of their data.

    Args:
        ch_axis (int, optional): The channel axis for which the range and outlier stats are computed
            Default: 1
        comp_percentile (float, optional): The percentile to compare against 100 percentile to find outliers
            Should be between 0 and 1 exclusive
            Default: 0.9

    * :attr:`num_batches_tracked` specifies number of batches passed through the observer

    * :attr:`average_batch_activation_range` defines average across the ranges of each batch passed through

    * :attr:`epoch_activation_min` defines the minimum value passed through the observer

    * :attr:`epoch_activation_max` defines the maximum value passed through the observer

    * :attr:`ch_axis` defines the channel being used to compute per channel min max stats

    * :attr:`min_val` defines the per channel minimum values passed through

    * :attr:`max_val` defines the per channel maximum values passed through

    * :attr:`comp_percentile` defines comparison percentile to find outliers

    * :attr:`average_percentile_ratio` defines the per channel average percentile ratios

    * :attr:`percentile_batches_tracked` defines the number of percentile batches tracked for each channel

    Note: this tool is meant for FX Graph Mode Quantization
    """

    def __init__(self, ch_axis: int = 1, comp_percentile: float = 0.9):
        super().__init__(torch.qint8)
        self.num_batches_tracked = 0

        # keep track of the min and mix of the range for average batch and epoch as a whole
        self.average_batch_activation_range: torch.Tensor = torch.tensor(float(0))
        self.epoch_activation_min = torch.tensor(float("inf"))
        self.epoch_activation_max = torch.tensor(float("-inf"))

        # keep track of per channel min max information using the given channel
        self.ch_axis: int = ch_axis
        self.min_val: torch.Tensor = torch.tensor([])
        self.max_val: torch.Tensor = torch.tensor([])

        # keep track of percentile ratio information per channel
        self.comp_percentile: torch.Tensor = torch.tensor([comp_percentile])
        self.average_percentile_ratio: torch.Tensor = torch.tensor([])
        self.percentile_batches_tracked: torch.Tensor = torch.tensor([])

    def forward(self, x):
        x_copy = x.detach()  # avoid keeping autograd tape
        x_copy = x_copy.to(self.epoch_activation_min.dtype)

        x_copy = self._calculate_range_stats(x_copy)
        x_copy = self._calculate_min_max_stats(x_copy)
        x_copy = self._calculate_percentile_stats(x_copy)

        # return the passed in the value
        return x

    def _calculate_range_stats(self, x_copy):
        r"""Calculates and stores range stats with forward values.

        Args
            x_copy: A copy of the forward data

        Returns the passed in x_copy
        """
        # get the min, max values of the data
        min_val_cur, max_val_cur = torch.aminmax(x_copy)

        # calculate new epoch range values
        epoch_min_val = torch.min(self.epoch_activation_min, min_val_cur)
        epoch_max_val = torch.max(self.epoch_activation_max, max_val_cur)

        self.epoch_activation_min.copy_(epoch_min_val)
        self.epoch_activation_max.copy_(epoch_max_val)

        # calculate the average batch activation range
        current_batch_range = max_val_cur - min_val_cur
        new_range = (
            self.average_batch_activation_range * self.num_batches_tracked
            + current_batch_range
        ) / (self.num_batches_tracked + 1)

        self.average_batch_activation_range = new_range
        self.num_batches_tracked += 1  # new batch was processed

        return x_copy

    def _calculate_min_max_stats(self, x_copy):
        r"""Calculates and stores the per_channel min, max stats with forward values.
        Does calculation based on channel axis: self.ch_axis

        Args
            x_copy: A copy of the forward data

        Returns the passed in x_copy
        """
        # get the current min and max vals
        min_val = self.min_val
        max_val = self.max_val
        x_dim = x_copy.size()

        new_axis_list = [i for i in range(len(x_dim))]  # noqa: C416
        new_axis_list[self.ch_axis] = 0
        new_axis_list[0] = self.ch_axis
        y = x_copy.permute(new_axis_list)
        # Need to match dtype of min/max because the updates to buffers
        # are done in place and types need to match for comparisons
        y = y.to(self.min_val.dtype)
        y = torch.flatten(y, start_dim=1)
        if min_val.numel() == 0 or max_val.numel() == 0:
            min_val, max_val = torch.aminmax(y, dim=1)
        else:
            min_val_cur, max_val_cur = torch.aminmax(y, dim=1)
            min_val = torch.min(min_val_cur, min_val)
            max_val = torch.max(max_val_cur, max_val)

        self.min_val.resize_(min_val.shape)
        self.max_val.resize_(max_val.shape)
        self.min_val.copy_(min_val)
        self.max_val.copy_(max_val)

        return x_copy

    def _calculate_percentile_stats(self, x_copy):
        r"""Calculates and stores the per_channel percentile stats with forward values.
        Does calculation based on channel axis: self.ch_axis

        Args
            x_copy: A copy of the forward data

        Returns the passed in x_copy
        """
        # get the dimension of the copy
        x_dim = x_copy.size()

        new_axis_list = [i for i in range(len(x_dim))]  # noqa: C416
        new_axis_list[self.ch_axis] = 0
        new_axis_list[0] = self.ch_axis
        y = x_copy.permute(new_axis_list)
        # Need to match dtype of min/max because the updates to buffers
        # are done in place and types need to match for comparisons
        y = y.to(self.min_val.dtype)
        y = torch.flatten(y, start_dim=1)
        y = y.to(self.min_val.dtype)

        # find the percentile values along the axis
        # we want both 100th percentile and comp_percentile
        quantiles_list = [self.comp_percentile, 1.00]
        quantiles_to_find = torch.tensor(quantiles_list, dtype=self.min_val.dtype)

        # find the quantiles
        comp_quantile = torch.quantile(y, quantiles_to_find[0], dim=self.ch_axis, interpolation="lower")
        hundreth_quartile = torch.quantile(y, quantiles_to_find[1], dim=self.ch_axis, interpolation="lower")

        # if any of the channels have 0s, we ignore that channel for this calculation
        any_non_zero_quantile_value: torch.Tensor = (comp_quantile != torch.tensor([0])) | (hundreth_quartile != torch.tensor([0]))
        any_non_zero_quantile_value = any_non_zero_quantile_value.int()  # transform boolean values to int values

        # possibilities to get nan as an answer
        #   will ignore any of these three cases with 0s and just not deal with them for now
        # case (1) 0 in numerator: issue if 0 is largest, all negative, and rest are really negative
        # case (2) 0 in denominator: is possible unless case 3, we just ignore
        # case (3) 0 in both: not outlier, channel just kinda useless, ignore

        # get the ratio and get rid of nan values
        quantile_ratios = hundreth_quartile / comp_quantile
        quantile_ratios = torch.nan_to_num(quantile_ratios)
        # update averages, remembering to only update if didn't have zeros
        ratio_if_not_zero = any_non_zero_quantile_value * quantile_ratios

        # if num_batches and average_ratio are not initialized, we want to initialize them
        if self.percentile_batches_tracked.shape[0] == 0 or self.average_percentile_ratio.shape[0] == 0:
            self.percentile_batches_tracked = torch.zeros_like(any_non_zero_quantile_value)
            self.average_percentile_ratio = torch.zeros_like(ratio_if_not_zero)

        # get current num batches and average ratio
        num_batches = self.percentile_batches_tracked
        average_ratio = self.average_percentile_ratio

        # calculate new_number of batches, new_ratios, and get rid of nans because of 0 size batches
        new_number_of_batches: torch.Tensor = num_batches + any_non_zero_quantile_value
        new_ratios: torch.Tensor = ((average_ratio * num_batches) + ratio_if_not_zero) / new_number_of_batches
        new_ratios = torch.nan_to_num(new_ratios)

        # update the values locally
        self.percentile_batches_tracked.copy_(new_number_of_batches)
        self.average_percentile_ratio.copy_(new_ratios)

        return x_copy



    @torch.jit.export
    def get_batch_to_epoch_ratio(self):
        epoch_activation_range = self.epoch_activation_max - self.epoch_activation_min

        if epoch_activation_range == torch.tensor(float(0)):
            raise ValueError("Range for Epoch is 0")
        elif epoch_activation_range == torch.tensor(float("inf")):
            raise ValueError(
                "No data has been run through observer or infinity value present"
            )
        else:
            return self.average_batch_activation_range / epoch_activation_range

    @torch.jit.export
    def reset_batch_and_epoch_values(self):
        # set all the values back to their original defaults for a new epoch
        self.num_batches_tracked = 0
        self.average_batch_activation_range = torch.tensor(float(0))
        self.epoch_activation_min = torch.tensor(float("inf"))
        self.epoch_activation_max = torch.tensor(float("-inf"))
        self.min_val = torch.tensor([])
        self.max_val = torch.tensor([])
        self.average_percentile_ratio = torch.tensor([])
        self.percentile_batches_tracked = torch.tensor([])

    @torch.jit.export
    def calculate_qparams(self):
        raise Exception(
            "calculate_qparams should not be called for ModelReportObserver"
        )
