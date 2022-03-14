import torch


class TensorOpsModule(torch.nn.Module):
    def __init__(self):
        super(TensorOpsModule, self).__init__()

    def forward(self):
        return self.tensor_general_ops()

    def tensor_general_ops(self):
        a = torch.randn(4)
        b = torch.tensor([1.5])
        x = torch.ones((2,))
        c = torch.randn(4, dtype=torch.cfloat)
        w = torch.rand(4, 4, 4, 4)
        v = torch.rand(4, 4, 4, 4)
        return (
            # torch.is_tensor(a),
            # torch.is_storage(a),
            torch.is_complex(a),
            torch.is_conj(a),
            torch.is_floating_point(a),
            torch.is_nonzero(b),
            # torch.set_default_dtype(torch.float32),
            # torch.get_default_dtype(),
            # torch.set_default_tensor_type(torch.DoubleTensor),
            torch.numel(a),
            # torch.set_printoptions(),
            # torch.set_flush_denormal(False),
            # https://pytorch.org/docs/stable/tensors.html#tensor-class-reference
            # x.new_tensor([[0, 1], [2, 3]]),
            x.new_full((3, 4), 3.141592),
            x.new_empty((2, 3)),
            x.new_ones((2, 3)),
            x.new_zeros((2, 3)),
            x.is_cuda,
            x.is_quantized,
            x.is_meta,
            x.device,
            x.dim(),
            c.real,
            c.imag,
            x.add(1),
            x.add(x),
            x.add_(x),
            # x.backward(),
            x.clone(),
            w.contiguous(),
            w.contiguous(memory_format=torch.channels_last),
            w.copy_(v),
            x.cpu(),
            # x.cuda(),
            # x.data_ptr(),
            # x.dense_dim(),
            w.fill_diagonal_(0),
            w.element_size(),
            w.exponential_(),
            w.fill_(0),
            w.geometric_(0.5),
            w.is_contiguous(),
            c.is_complex(),
            w.is_conj(),
            w.is_floating_point(),
            w.is_leaf,
            w.is_pinned(),
            w.is_set_to(w),
            # w.is_shared,
            w.is_signed(),
            w.is_sparse,
            torch.tensor([1]).item(),
            x.log_normal_(),
            # x.masked_scatter_(),
            # x.masked_scatter(),
            # w.normal(),
            w.numel(),
            # w.pin_memory(),
            # w.put_(0, torch.tensor([0, 1], w)),
            x.repeat(4, 2),
        )


class TensorCreationOpsModule(torch.nn.Module):
    def __init__(self):
        super(TensorCreationOpsModule, self).__init__()

    def forward(self):
        return self.tensor_creation_ops()

    def tensor_creation_ops(self):
        i = torch.tensor([[0, 1, 1], [2, 0, 2]])
        v = torch.tensor([3, 4, 5], dtype=torch.float32)
        real = torch.tensor([1, 2], dtype=torch.float32)
        imag = torch.tensor([3, 4], dtype=torch.float32)
        inp = torch.tensor([-1.5, 0.0, 2.0])
        values = torch.tensor([0.5])
        quantized = torch.quantize_per_channel(
            torch.tensor([[-1.0, 0.0], [1.0, 2.0]]),
            torch.tensor([0.1, 0.01]),
            torch.tensor([10, 0]),
            0,
            torch.quint8,
        )
        return (
            torch.tensor([[0.1, 1.2], [2.2, 3.1], [4.9, 5.2]]),
            # torch.sparse_coo_tensor(i, v, [2, 4]), # not work for iOS
            torch.as_tensor([1, 2, 3]),
            torch.as_strided(torch.randn(3, 3), (2, 2), (1, 2)),
            torch.zeros(2, 3),
            torch.zeros((2, 3)),
            torch.zeros([2, 3]),
            torch.zeros(5),
            torch.zeros_like(torch.empty(2, 3)),
            torch.ones(2, 3),
            torch.ones((2, 3)),
            torch.ones([2, 3]),
            torch.ones(5),
            torch.ones_like(torch.empty(2, 3)),
            torch.arange(5),
            torch.arange(1, 4),
            torch.arange(1, 2.5, 0.5),
            torch.range(1, 4),
            torch.range(1, 4, 0.5),
            # torch.linspace(3., 3., steps=1), # TODO need to fix
            # torch.logspace(start=2, end=2, steps=1, base=2.0), # TODO need to fix
            torch.eye(3),
            torch.empty(2, 3),
            torch.empty_like(torch.empty(2, 3), dtype=torch.int64),
            torch.empty_strided((2, 3), (1, 2)),
            torch.full((2, 3), 3.141592),
            torch.full_like(torch.full((2, 3), 3.141592), 2.71828),
            torch.quantize_per_tensor(
                torch.tensor([-1.0, 0.0, 1.0, 2.0]), 0.1, 10, torch.quint8
            ),
            torch.dequantize(quantized),
            torch.complex(real, imag),
            torch.polar(real, imag),
            torch.heaviside(inp, values),
        )


class TensorIndexingOpsModule(torch.nn.Module):
    def __init__(self):
        super(TensorIndexingOpsModule, self).__init__()

    def forward(self):
        return self.tensor_indexing_ops()

    def tensor_indexing_ops(self):
        x = torch.randn(2, 4)
        y = torch.randn(2, 4, 2)
        t = torch.tensor([[0, 0], [1, 0]])
        mask = x.ge(0.5)
        i = [0, 1]
        return (
            torch.cat((x, x, x), 0),
            torch.concat((x, x, x), 0),
            torch.conj(x),
            torch.chunk(x, 2),
            torch.dsplit(y, i),
            torch.column_stack((x, x)),
            torch.dstack((x, x)),
            torch.gather(x, 0, t),
            torch.hsplit(x, i),
            torch.hstack((x, x)),
            torch.index_select(x, 0, torch.tensor([0, 1])),
            torch.masked_select(x, mask),
            torch.movedim(x, 1, 0),
            torch.moveaxis(x, 1, 0),
            torch.narrow(x, 0, 0, 2),
            torch.nonzero(x),
            torch.permute(x, (0, 1)),
            torch.reshape(x, (-1,)),
        )


class TensorTypingOpsModule(torch.nn.Module):
    def __init__(self):
        super(TensorTypingOpsModule, self).__init__()

    def forward(self):
        return self.tensor_typing_ops()

    def tensor_typing_ops(self):
        x = torch.randn(2, 2)
        return (
            x.to(torch.float),
            x.to(torch.double),
            x.to(torch.cfloat),
            x.to(torch.cdouble),
            x.to(torch.half),
            x.to(torch.bfloat16),
            x.to(torch.uint8),
            x.to(torch.int8),
            x.to(torch.short),
            x.to(torch.int),
            x.to(torch.long),
            x.to(torch.bool),
        )


class TensorViewOpsModule(torch.nn.Module):
    def __init__(self):
        super(TensorViewOpsModule, self).__init__()

    def forward(self):
        return self.tensor_view_ops()

    def tensor_view_ops(self):
        x = torch.randn(4, 4, 1)
        y = torch.randn(4, 4, 2)
        return (
            x[0, 2:],
            x.detach(),
            x.detach_(),
            x.diagonal(),
            x.expand(-1, -1, 3),
            x.expand_as(y),
            x.select(0, 1),
            x.unflatten(1, (2, 2)),
            x.unfold(1, 2, 2),
            x.view(16),
            x.view_as(torch.randn(16)),
        )
