import torch
from torch.autograd.function import Function
from torch._thnn import type2backend

from . import _all_functions


class Embedding(Function):

    def __init__(self, padding_idx, max_norm, norm_type, scale_grad_by_freq,
                 sparse=False):
        super(Embedding, self).__init__()
        self.padding_idx = padding_idx
        self.max_norm = max_norm
        self.norm_type = norm_type
        self.scale_grad_by_freq = scale_grad_by_freq
        self._indices = None
        self.sparse = sparse

    def _renorm(self, indices, weight):
        if indices.dim() == 2:
            indices = indices.clone().view(-1)

        self._backend.LookupTable_renorm(
            self._backend.library_state,
            indices,
            weight,
            self.max_norm,
            self.norm_type
        )

    def forward(self, indices, weight):
        assert indices.dim() <= 2
        assert not self.needs_input_grad[0], "Embedding doesn't " \
            "compute the gradient w.r.t. the indices"

        self._backend = type2backend[type(weight)]
        self._weight_size = weight.size()

        if not indices.is_contiguous():
            self._indices = indices.contiguous()
            indices = self._indices
        else:
            self.save_for_backward(indices)

        output = weight.new()
        if self.max_norm is not None:
            self._renorm(indices, weight)

        if indices.dim() == 1:
            output = torch.index_select(weight, 0, indices)
        else:
            output = torch.index_select(weight, 0, indices.view(-1))
            output = output.view(indices.size(0), indices.size(1), weight.size(1))

        return output

    def backward(self, grad_output):
        if self._indices is not None:
            indices = self._indices
        else:
            indices, = self.saved_tensors

        grad_output = grad_output.contiguous()
        if not self.sparse:
            if indices.dim() == 2:
                indices = indices.view(-1)

            with torch.cuda.device_of(grad_output):
                if grad_output.is_cuda:
                    _sorted = torch.cuda.LongTensor()
                    _indices = torch.cuda.LongTensor()
                    _count = torch.cuda.LongTensor()
                else:
                    _count = torch.IntTensor()
                    _sorted = _indices = None

            grad_weight = grad_output.new(self._weight_size).zero_()
            self._backend.LookupTable_accGradParameters(
                self._backend.library_state,
                indices,
                grad_output,
                grad_weight,
                _count,
                _sorted,
                _indices,
                self.scale_grad_by_freq,
                self.padding_idx,
                1
            )
        else:
            tensor_type = type(grad_output).__name__
            if grad_output.is_cuda:
                SparseTensor = getattr(torch.cuda.sparse, tensor_type)
            else:
                SparseTensor = getattr(torch.sparse, tensor_type)
            grad_weight = SparseTensor(
                indices.view(1, -1),
                grad_output.view(-1, self._weight_size[1]),
                self._weight_size,
            )
        return None, grad_weight


_all_functions.append(Embedding)


MODE_SUM = 0
MODE_MEAN = 1

class EmbeddingBag(Function):

    def __init__(self, max_norm, norm_type, scale_grad_by_freq, mode):
        super(EmbeddingBag, self).__init__()
        self.max_norm = max_norm
        self.norm_type = norm_type
        self.scale_grad_by_freq = scale_grad_by_freq
        self._indices = None
        assert mode is not None
        if mode == 'sum':
            self.mode = MODE_SUM
        elif mode == 'mean':
            self.mode = MODE_MEAN
        else:
            raise ValueError("mode needs to be 'sum' or 'mean', but got {}"
                             .format( mode))

    def _renorm(self, indices, weight):
        self._backend.LookupTable_renorm(
            self._backend.library_state,
            indices,
            weight,
            self.max_norm,
            self.norm_type
        )

    def forward(self, weight, indices, offsets):
        assert not self.needs_input_grad[1], "EmbeddingBag doesn't " \
            "compute the gradient w.r.t. the indices"

        assert not self.needs_input_grad[2], "EmbeddingBag doesn't " \
            "compute the gradient w.r.t. the offsets"

        assert indices.dim() == 1
        if offsets.dim() != 1:
            raise ValueError("offsets has to be a 1D Tensor")

        if offsets[0] != 0:
            raise ValueError("offsets[0] has to be 0, i.e. the first sequence"
                             " in the mini-batch has to start from position 0."
                             "However, got {}".format(offsets[0]))
        if offsets[-1] > indices.size(0):
            raise ValueError("offsets[-1] has to be smaller than indices's length"
                             " ({}), but got offsets[-1] of {}"
                             .format(indices.size(0), offsets[-1]))

        self._backend = type2backend[type(weight)]
        self._weight_size = weight.size()
        self._offset2bag = offsets.new()

        self.save_for_backward(indices)

        indices = indices.contiguous().view(-1)
        output = weight.new()
        if self.max_norm is not None:
            self._renorm(indices, weight)

        if weight.is_cuda:
            if self.mode == MODE_MEAN:
                self.sequence_length = offsets.new().resize_(offsets.size())
            else:
                self.sequence_length = None

            self._backend.LookupTableBag_updateOutput(
                self._backend.library_state,
                indices,
                offsets,
                weight,
                output,
                self._offset2bag,
                self.mode,
                self.sequence_length
            )
        else:
            # slow CPU implementation
            index_output = torch.index_select(weight, 0, indices)
            # indices = [1, 2, 30, 100, 12], offsets = [0, 2, 3]
            self._offset2bag.resize_(indices.size(0)).zero_()  # offset2bag = [0 0 0 0 0]
            self._offset2bag.index_fill_(0, offsets, 1)  # offset2bag = [1 0 1 0 1]
            self._offset2bag[0] = 0  # offset2bag = [0 0 1 0 1]
            self._offset2bag = self._offset2bag.cumsum(0)  # offset2bag = [0 0 1 1 2]
            output.resize_(offsets.size(0), weight.size(1)).zero_()
            output.index_add_(0, self._offset2bag, index_output)
            if self.mode == MODE_MEAN:
                self.sequence_length = weight.new().resize_(offsets.size())
                self.sequence_length[:-1] = offsets[1:] - offsets[:-1]
                self.sequence_length[-1] = indices.size(0) - offsets[-1]
                self.sequence_length = self.sequence_length[:, None].expand_as(output)
                output /= self.sequence_length

        return output

    def backward(self, grad_output):
        indices, = self.saved_tensors
        indices = indices.contiguous().view(-1)
        grad_output = grad_output.contiguous()

        with torch.cuda.device_of(grad_output):
            if grad_output.is_cuda:
                _sorted = torch.cuda.LongTensor()
                _indices = torch.cuda.LongTensor()
                _count = torch.cuda.LongTensor()
            else:
                _count = torch.IntTensor()
                _sorted = _indices = None

        grad_weight = grad_output.new(self._weight_size).zero_()

        if grad_output.is_cuda:
            self._backend.LookupTableBag_accGradParameters(
                self._backend.library_state,
                indices,
                grad_output,
                grad_weight,
                self._offset2bag,
                _count,
                _sorted,
                _indices,
                self.scale_grad_by_freq,
                self.mode,
                self.sequence_length,
                1
            )
        else:
            # slow CPU implementation
            if self.mode == MODE_MEAN:
                # divide by average count
                grad_output = grad_output / self.sequence_length

            index_grad_output = grad_output.index_select(0, self._offset2bag)
            self._backend.LookupTable_accGradParameters(
                self._backend.library_state,
                indices,
                index_grad_output,
                grad_weight,
                _count,
                _sorted,
                _indices,
                self.scale_grad_by_freq,
                -1,
                1
            )

        return grad_weight, None, None


_all_functions.append(EmbeddingBag)
