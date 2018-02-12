import torch
import torch.nn as nn
from torch.autograd import Variable


class Model(nn.Module):
    def __init__(self):
        super(Model, self).__init__()
        self.linear = nn.Linear(20, 2)

    def forward(self, input):
        out = self.linear(input[:, 10:30])
        return out.sum()


def main():
    data = Variable(torch.randn(10, 50).cuda())
    model = Model().cuda()
    optimizer = torch.optim.SGD(model.parameters(), lr=0.0001)
    for i in range(5):
        optimizer.zero_grad()
        loss = model(data)
        loss.backward()
        optimizer.step()


if __name__ == '__main__':
    main()
