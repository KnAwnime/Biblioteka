import torch
import torch.optim
import torch.nn as nn
import torch.backends.cudnn as cudnn

import torch.nn.functional as F
from torch.autograd import Variable

import unittest, time, sys

import models.optimized.densenet_new as densenet_optim
import models.optimized.resnet_new as resnet_optim
import models.optimized.vnet_new as vnet_optim
import models.optimized.word_language_model_new as wlm_optim


class TestMemoryOptimized(unittest.TestCase):

    def test_densenet_optim(self):
        N = 32
        # N = 72
        chunks = 4
        total_iters = 20    # (warmup + benchmark)
        iterations = 1

        x = Variable(torch.randn(N, 3, 224, 224).fill_(1.0), requires_grad=True)
        target = Variable(torch.randn(N).fill_(1)).type("torch.LongTensor")
        # model = densenet_optimized.densenet100()
        # model = densenet_optimized.densenet121()
        # model = densenet_optimized.densenet201()
        model = densenet_optim.densenet264()

        # switch the model to train mode
        model.train()

        # convert the model and input to cuda
        model = model.cuda()
        input_var = x.cuda()
        target_var = target.cuda()

        # declare the optimizer and criterion
        criterion = nn.CrossEntropyLoss().cuda()
        optimizer = torch.optim.SGD(model.parameters(), 0.01, momentum=0.9, weight_decay=1e-4)

        start = torch.cuda.Event(enable_timing=True)
        end = torch.cuda.Event(enable_timing=True)
        with cudnn.flags(enabled=True, benchmark=True):
            for i in range(total_iters):
                start.record()
                start_cpu = time.time()
                for j in range(iterations):
                    output = model(input_var, chunks=chunks)
                    loss = criterion(output, target_var)
                    optimizer.zero_grad()
                    loss.backward()
                    optimizer.step()

                end_cpu = time.time()
                end.record()
                torch.cuda.synchronize()
                gpu_msec = start.elapsed_time(end)
                print("Optimized densenet ({:2d}): ({:8.3f} usecs gpu) ({:8.3f} usecs cpu)".format(
                    i, gpu_msec * 1000, (end_cpu - start_cpu) * 1000000,
                    file=sys.stderr))

    def test_resnet_optim(self):
        N = 32
        # N = 51
        total_iters = 20    # (warmup + benchmark)
        iterations = 1
        chunks = 6

        target = Variable(torch.randn(N).fill_(1)).type("torch.LongTensor")
        # x = Variable(torch.randn(N, 3, 224, 224).fill_(1.0), requires_grad=True)
        x = Variable(torch.randn(N, 3, 32, 32).fill_(1.0), requires_grad=True)
        # model = resnet_optim.resnet200()
        # model = resnet_optim.resnet101()
        # model = resnet_optim.resnet50()
        model = resnet_optim.resnet1001()

        # switch the model to train mode
        model.train()

        # convert the model and input to cuda
        model = model.cuda()
        input_var = x.cuda()
        target_var = target.cuda()

        # declare the optimizer and criterion
        criterion = nn.CrossEntropyLoss().cuda()
        optimizer = torch.optim.SGD(model.parameters(), 0.01, momentum=0.9, weight_decay=1e-4)
        optimizer.zero_grad()

        start = torch.cuda.Event(enable_timing=True)
        end = torch.cuda.Event(enable_timing=True)
        with cudnn.flags(enabled=True, benchmark=True):
            for i in range(total_iters):
                start.record()
                start_cpu = time.time()
                for j in range(iterations):
                    output = model(input_var, chunks=chunks)
                    loss = criterion(output, target_var)
                    loss.backward()
                    optimizer.step()

                end_cpu = time.time()
                end.record()
                torch.cuda.synchronize()
                gpu_msec = start.elapsed_time(end)
                print("Optimized resnet ({:2d}): ({:8.3f} usecs gpu) ({:8.3f} usecs cpu)".format(
                    i, gpu_msec * 1000, (end_cpu - start_cpu) * 1000000,
                    file=sys.stderr))

    def weights_init(self, m):
        classname = m.__class__.__name__
        if classname.find('Conv3d') != -1:
            nn.init.kaiming_normal(m.weight)
            m.bias.data.zero_()

    def test_vnet_optim(self):
        # optimized
        N = 8
        total_iters = 20    # (warmup + benchmark)
        iterations = 1

        # baseline
        # N = 4
        # total_iters = 10    # (warmup + benchmark)
        # iterations = 2

        target = Variable(torch.randn(N, 1, 128, 128, 64).fill_(1)).type("torch.LongTensor")
        x = Variable(torch.randn(N, 1, 128, 128, 64).fill_(1.0), requires_grad=True)
        model = vnet_optim.VNet(elu=False, nll=True)
        bg_weight = 0.5
        fg_weight = 0.5
        weights = torch.FloatTensor([bg_weight, fg_weight])
        weights = weights.cuda()
        model.train()

        # convert the model and input to cuda
        model = model.cuda()
        input_var = x.cuda()
        target_var = target.cuda()
        target = target_var.view(target_var.numel())

        # declare the optimizer and criterion
        optimizer = torch.optim.SGD(model.parameters(), lr=1e-1, momentum=0.99, weight_decay=1e-8)
        optimizer.zero_grad()
        model.apply(self.weights_init)
        start = torch.cuda.Event(enable_timing=True)
        end = torch.cuda.Event(enable_timing=True)

        with cudnn.flags(enabled=True, benchmark=True):
            for i in range(total_iters):
                start.record()
                start_cpu = time.time()
                for j in range(iterations):
                    output = model(input_var)
                    loss = F.nll_loss(output, target, weight=weights)
                    loss.backward()
                    optimizer.step()

                end_cpu = time.time()
                end.record()
                torch.cuda.synchronize()
                gpu_msec = start.elapsed_time(end)
                print("Optimized vnet ({:2d}): ({:8.3f} usecs gpu) ({:8.3f} usecs cpu)".format(
                    i, gpu_msec * 1000, (end_cpu - start_cpu) * 1000000,
                    file=sys.stderr))

    def repackage_hidden(self, h):
        """Wraps hidden states in new Variables, to detach them from their history."""
        if type(h) == Variable:
            return Variable(h.data)
        else:
            return tuple(self.repackage_hidden(v) for v in h)

    def test_wlm_optim(self):
        total_iters = 20
        iterations = 1
        chunks = 4

        model_name = 'LSTM'
        ntokens = 33278
        emsize = 200
        nhid = 200
        nlayers = 1
        dropout = 0.2
        tied = False
        batchsize = 20
        bptt = 8100

        data = Variable(torch.LongTensor(bptt, batchsize).fill_(1), volatile=False)
        target_var = Variable(torch.LongTensor(bptt * batchsize).fill_(1))
        targets = target_var.cuda()
        input_data = data.cuda()

        model = wlm_optim.RNNModel(model_name, ntokens, emsize, nhid, nlayers, dropout, tied)
        model = model.cuda()
        model.train()
        criterion = nn.CrossEntropyLoss().cuda()
        hidden = model.init_hidden(batchsize)

        start = torch.cuda.Event(enable_timing=True)
        end = torch.cuda.Event(enable_timing=True)
        with cudnn.flags(enabled=True, benchmark=True):
            for i in range(total_iters):
                start.record()
                start_cpu = time.time()
                for j in range(iterations):
                    hidden = self.repackage_hidden(hidden)
                    output, hidden = model(input_data, hidden, targets, chunks=chunks)
                    model.backward(output)

                end_cpu = time.time()
                end.record()
                torch.cuda.synchronize()
                gpu_msec = start.elapsed_time(end)
                print("Optimized WLM ({:2d}): ({:8.3f} usecs gpu) ({:8.3f} usecs cpu)".format(
                    i, gpu_msec * 1000, (end_cpu - start_cpu) * 1000000,
                    file=sys.stderr))


if __name__ == '__main__':
    unittest.main()
