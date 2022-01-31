import torch

class TestVersionedDivTensorExampleV7(torch.nn.Module):
    def __init__(self):
        super(TestVersionedDivTensorExampleV7, self).__init__()

    def forward(self, a, b):
        result_0 = a / b
        result_1 = torch.div(a, b)
        result_2 = a.div(b)
        return result_0, result_1, result_2

class TestVersionedLinspaceV7(torch.nn.Module):
    def __init__(self):
        super(TestVersionedLinspaceV7, self).__init__()

    def forward(self, a, b):
        c = torch.linspace(a, b, steps=5)
        d = torch.linspace(a, b)
        return c, d

class TestVersionedLinspaceOutV7(torch.nn.Module):
    def __init__(self):
        super(TestVersionedLinspaceOutV7, self).__init__()

    def forward(self, a, b):
        out = torch.empty(100, )
        return torch.linspace(a, b, out=out)
