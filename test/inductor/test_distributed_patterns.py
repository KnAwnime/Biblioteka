# Owner(s): ["oncall: pt2"]
import dataclasses
import functools
from unittest.mock import patch

import torch
from torch import nn
from torch._dynamo import compiled_autograd
from torch._dynamo.test_case import run_tests, TestCase
from torch._dynamo.testing import CompileCounter
from torch.testing._internal.common_utils import IS_MACOS, parametrize
from torch.testing._internal.inductor_utils import HAS_CPU, requires_gpu

# Fake distributed
WORLD_SIZE = 2
RESIZE = True


def init_fake_distributed():
    @torch.no_grad
    def all_gather(t):
        return torch.cat([t] * WORLD_SIZE, 0)

    @torch.no_grad
    def reduce_scatter(t):
        return t.narrow(0, 0, t.size(0) // WORLD_SIZE)

    def fw_pre_hook(mod, inp):
        with torch.no_grad():
            mod.og_weight = mod.weight
            mod.weight = nn.Parameter(all_gather(mod.weight))

    # Forward:
    #   Before:
    #     mod.weight = local_shard
    #   After:
    #     mod.weight = local_shard
    #     mod.empty_weight  =zero-sized allgather

    def fw_post_hook(mod, inp, out):
        if RESIZE:
            # Drop the big weight
            mod.weight.untyped_storage().resize_(0)
        mod.empty_weight = mod.weight
        mod.weight = mod.og_weight
        del mod.og_weight

    def bw_pre_hook(mod, gO):
        if RESIZE:
            mod.empty_weight.untyped_storage().resize_(
                WORLD_SIZE * mod.weight.nelement() * mod.weight.element_size()
            )
        mod.og_weight = mod.weight
        full_weight = nn.Parameter(all_gather(mod.weight))
        with torch.no_grad(), torch.autograd._unsafe_preserve_version_counter(
            mod.empty_weight
        ):
            mod.empty_weight.copy_(full_weight)
        mod.weight = mod.empty_weight
        del mod.empty_weight

    # Backward:
    #   Before:
    #     mod.weight = local_shard
    #     mod.empty_weight = zero-sized allgather
    #   After:
    #     mod.weight = local_shard

    def bw_post_hook(mod, gI, gO):
        grad = mod.weight.grad
        new_grad = reduce_scatter(grad)
        # No need to re-empty the weight here, the graph has been cleared
        # This removes the last reference to the big Tensor
        mod.weight = mod.og_weight
        del mod.og_weight
        mod.weight.grad = new_grad

    torch.manual_seed(1234)
    m = nn.Linear(20, 10, bias=False)
    m.weight = nn.Parameter(reduce_scatter(m.weight))
    m.register_full_backward_pre_hook(bw_pre_hook)
    m.register_full_backward_hook(bw_post_hook)
    m.register_forward_pre_hook(fw_pre_hook)
    m.register_forward_hook(fw_post_hook)
    return m, torch.rand(2, 20, requires_grad=True)


def init_module_bw_hooks(allow_eager):
    def bw_pre_hook(mod, gO):
        assert allow_eager or torch._dynamo.is_compiling()
        assert mod.weight.size() == (10, 10)
        mod.hook_count_pre.add_(1)
        return (torch.sin(gO[0] + 1.2),)

    def bw_post_hook(mod, gI, gO):
        assert allow_eager or torch._dynamo.is_compiling()
        assert mod.weight.size() == (10, 10)
        mod.hook_count_post.add_(1)
        return (torch.sin(gI[0] + 3.4),)

    torch.manual_seed(1234)
    m = nn.Linear(10, 10)
    m.hook_count_pre = torch.tensor(0)
    m.hook_count_post = torch.tensor(0)
    m.register_full_backward_pre_hook(bw_pre_hook)
    m.register_full_backward_hook(bw_post_hook)
    return m, torch.rand(2, 10, requires_grad=True)


def steps(m, inp):
    for _ in range(4):
        out = m(inp)
        out.sum().backward()
    return out


class DistributedPatternTests(TestCase):
    def test_intermediate_hook_with_closure(self):
        @dataclasses.dataclass
        class CustomObj:
            val: torch.Tensor

        def fn(x, obj):
            y = x.sin()
            closure_var = y + 1
            y.register_hook(lambda grad: grad + obj.val + closure_var)
            z = y.sin()
            return z

        opt = torch.compile(fn, fullgraph=True)

        obj1 = CustomObj(torch.tensor(88))
        obj2 = CustomObj(torch.tensor(99))
        x0 = torch.ones(4, requires_grad=True)
        x1 = torch.ones(4, requires_grad=True)
        x2 = torch.ones(4, requires_grad=True)
        x3 = torch.ones(4, requires_grad=True)
        fn(x0, obj1).sum().backward()
        fn(x1, obj2).sum().backward()

        with compiled_autograd.enable(functools.partial(torch.compile, fullgraph=True)):
            opt(x2, obj1).sum().backward()
            opt(x3, obj2).sum().backward()

        self.assertEqual(x0.grad, x2.grad)
        self.assertEqual(x1.grad, x3.grad)

    @torch.no_grad()
    @requires_gpu()
    def test_storage_resize_zero(self):
        @torch.compile(fullgraph=True)
        def fn(x):
            y = torch.sin(x)
            x.untyped_storage().resize_(0)
            return torch.cos(y)

        for device in ("cpu", "cuda"):
            x = torch.randn(10, device=device)
            expected = torch.cos(torch.sin(x))
            y = fn(x)
            self.assertEqual(y, expected)
            self.assertEqual(x.untyped_storage().size(), 0)

    @torch.no_grad()
    @requires_gpu()
    def test_storage_resize_nonzero(self):
        @torch.compile(fullgraph=True)
        def fn(x, out):
            y = torch.sin(x)
            assert out.untyped_storage().size() == 0
            out.untyped_storage().resize_(x.untyped_storage().size())
            out.copy_(y.cos())

        for device in ("cpu", "cuda"):
            x = torch.randn(10, device=device)
            out = torch.randn(10, device=device)
            expected = torch.cos(torch.sin(x))
            out.untyped_storage().resize_(0)
            fn(x, out)
            self.assertEqual(out.untyped_storage().size(), x.untyped_storage().size())
            self.assertEqual(out, expected)

    @torch.no_grad()
    def test_unsafe_set_version_counter1(self):
        cnt = CompileCounter()

        @torch.compile(backend=cnt, fullgraph=True)
        def fn(w, x):
            x = x.sin()
            v = w._version
            w.copy_(x + 1)
            torch._C._autograd._unsafe_set_version_counter(w, v)
            return w, v

        for v in (3, 0, 1):
            w1 = torch.randn(16)
            for i in range(v):
                w1.fill_(i)  # bump w1._version
            self.assertEqual(w1._version, v)
            x1 = torch.randn(16)
            w2, v2 = fn(w1, x1)

            self.assertIs(w1, w2)
            self.assertEqual(w1, x1.sin() + 1)
            self.assertEqual(v2, v)
            self.assertEqual(w1._version, v)
            self.assertEqual(cnt.frame_count, 1)

    def test_unsafe_set_version_counter2(self):
        @torch.compile(backend="inductor", fullgraph=True)
        def fn(w, x):
            r = w.sin()
            with torch.no_grad():
                v = w._version
                w.copy_(x)
                torch._C._autograd._unsafe_set_version_counter(w, v)
            return r

        w1 = torch.randn(1, requires_grad=True)
        x1 = torch.randn(1)
        expected_r1 = w1.detach().sin()

        r1 = fn(w1, x1)
        r1.backward()
        self.assertEqual(r1, expected_r1)
        self.assertEqual(w1, x1)
        self.assertEqual(w1.grad, x1.cos())

    @torch.no_grad()
    def test_unsafe_preserve_version_counter1(self):
        @torch.compile(backend="eager", fullgraph=True)
        def fn(w, x):
            x = x.sin()
            with torch.autograd._unsafe_preserve_version_counter(w):
                w.copy_(x + 1)
            return w

        w1 = torch.randn(16).fill_(0).fill_(1)
        x1 = torch.randn(16)
        v1 = w1._version
        w2 = fn(w1, x1)
        v2 = w1._version

        self.assertIs(w1, w2)
        self.assertEqual(w1, x1.sin() + 1)
        self.assertEqual(v1, v2)

    def test_unsafe_preserve_version_counter2(self):
        @torch.compile(backend="inductor", fullgraph=True)
        def fn(w, x):
            r = w.sin()
            with torch.no_grad(), torch.autograd._unsafe_preserve_version_counter(w):
                w.copy_(x)
            return r

        w1 = torch.randn(1, requires_grad=True)
        x1 = torch.randn(1)
        expected_r1 = w1.detach().sin()

        r1 = fn(w1, x1)
        r1.backward()
        self.assertEqual(r1, expected_r1)
        self.assertEqual(w1, x1)
        self.assertEqual(w1.grad, x1.cos())

    def test_module_backward_hooks_eager(self):
        m1, inp1 = init_module_bw_hooks(True)
        out1 = steps(m1, inp1)

        m2, inp2 = init_module_bw_hooks(False)
        fw_cnt = CompileCounter()
        bw_cnt = CompileCounter()
        with compiled_autograd.enable(torch.compile(backend=bw_cnt, fullgraph=True)):
            m2 = torch.compile(m2, backend=fw_cnt, fullgraph=True)
            out2 = steps(m2, inp2)

        self.assertEqual(m1.hook_count_pre, m2.hook_count_pre)
        self.assertEqual(m1.hook_count_post, m2.hook_count_post)
        self.assertEqual(out1, out2)
        self.assertEqual(inp1.grad, inp2.grad)
        self.assertEqual(m1.weight.grad, m2.weight.grad)
        self.assertEqual(m1.bias.grad, m2.bias.grad)

        self.assertEqual(fw_cnt.frame_count, 1)
        self.assertEqual(fw_cnt.op_count, 5)
        self.assertEqual(bw_cnt.frame_count, 2)  # grad=None and grad!=None
        self.assertEqual(bw_cnt.op_count, 48)

    def test_module_backward_hooks_aot(self):
        m1, inp1 = init_module_bw_hooks(True)
        out1 = steps(m1, inp1)

        m2, inp2 = init_module_bw_hooks(True)
        m2 = torch.compile(m2, backend="aot_eager", fullgraph=True)
        with compiled_autograd.enable(lambda gm: gm):
            out2 = steps(m2, inp2)

        self.assertEqual(m1.hook_count_pre, m2.hook_count_pre)
        self.assertEqual(m1.hook_count_post, m2.hook_count_post)
        self.assertEqual(out1, out2)
        self.assertEqual(inp1.grad, inp2.grad)
        self.assertEqual(m1.weight.grad, m2.weight.grad)
        self.assertEqual(m1.bias.grad, m2.bias.grad)

    def test_module_backward_hooks_inductor(self):
        m1, inp1 = init_module_bw_hooks(True)
        out1 = steps(m1, inp1)

        m2, inp2 = init_module_bw_hooks(False)
        m2 = torch.compile(m2, fullgraph=True)
        with compiled_autograd.enable(torch.compile(fullgraph=True)):
            out2 = steps(m2, inp2)

        self.assertEqual(m1.hook_count_pre, m2.hook_count_pre)
        self.assertEqual(m1.hook_count_post, m2.hook_count_post)
        self.assertEqual(out1, out2)
        self.assertEqual(inp1.grad, inp2.grad)
        self.assertEqual(m1.weight.grad, m2.weight.grad)
        self.assertEqual(m1.bias.grad, m2.bias.grad)

    def test_module_backward_hooks_multi_layers(self):
        a1, inp1 = init_module_bw_hooks(True)
        b1, _ = init_module_bw_hooks(True)
        out1 = steps(torch.nn.Sequential(a1, b1), inp1)

        a2, inp2 = init_module_bw_hooks(False)
        b2, _ = init_module_bw_hooks(False)
        with compiled_autograd.enable(torch.compile(fullgraph=True)):
            out2 = steps(
                torch.compile(torch.nn.Sequential(a2, b2), fullgraph=True), inp2
            )

        self.assertEqual(a1.hook_count_pre, a2.hook_count_pre)
        self.assertEqual(a1.hook_count_post, a2.hook_count_post)
        self.assertEqual(b1.hook_count_pre, b2.hook_count_pre)
        self.assertEqual(b1.hook_count_post, b2.hook_count_post)
        self.assertEqual(out1, out2)
        self.assertEqual(inp1.grad, inp2.grad)
        self.assertEqual(a1.weight.grad, a2.weight.grad)
        self.assertEqual(a1.bias.grad, a2.bias.grad)
        self.assertEqual(b1.weight.grad, b2.weight.grad)
        self.assertEqual(b1.bias.grad, b2.bias.grad)

    # TODO(jansel): support bw hooks with graph break

    def _assert_same_grad(self, a, b):
        self.assertEqual(type(a), type(b))
        self.assertEqual(a, b)
        self.assertEqual(a.grad, b.grad)
        self.assertEqual(a.requires_grad, b.requires_grad)

    def test_nn_param_return1(self):
        def fn(x):
            p = torch.nn.Parameter(x)
            return p, p.sin()

        opt = torch.compile(fn, fullgraph=True)
        x1 = torch.randn(16)
        x2 = x1.clone()

        p1, r1 = fn(x1)
        r1.sum().backward()
        p2, r2 = opt(x2)
        r2.sum().backward()
        self._assert_same_grad(r1, r2)
        self._assert_same_grad(p1, p2)

    def test_nn_param_return2(self):
        def fn(x):
            p = torch.nn.Parameter(x, requires_grad=False)
            return p, x + 1

        opt = torch.compile(fn, fullgraph=True)
        x1 = torch.randn(16)
        x2 = x1.clone()

        p1, r1 = fn(x1)
        p2, r2 = opt(x2)
        self._assert_same_grad(r1, r2)
        self._assert_same_grad(p1, p2)

    def test_nn_param_return3(self):
        def fn(x):
            p = torch.nn.Parameter(x + 123)
            return p, p.sin()

        opt = torch.compile(fn, fullgraph=True)
        x1 = torch.randn(16)
        x2 = x1.clone()

        p1, r1 = fn(x1)
        r1.sum().backward()
        p2, r2 = opt(x2)
        r2.sum().backward()
        self._assert_same_grad(r1, r2)
        self._assert_same_grad(p1, p2)

    def test_nn_param_return4(self):
        def fn(x):
            p = torch.nn.Parameter(x + 123, requires_grad=False)
            return p, x + 1

        opt = torch.compile(fn, fullgraph=True)
        x1 = torch.randn(16)
        x2 = x1.clone()

        p1, r1 = fn(x1)
        p2, r2 = opt(x2)
        self._assert_same_grad(r1, r2)
        self._assert_same_grad(p1, p2)

    def test_fake_distributed_eager(self):
        m1, inp1 = init_fake_distributed()
        out1 = steps(m1, inp1)

        m2, inp2 = init_fake_distributed()
        fw_cnt = CompileCounter()
        m2 = torch.compile(m2, backend=fw_cnt, fullgraph=True)

        bw_cnt = CompileCounter()
        with compiled_autograd.enable(torch.compile(backend=bw_cnt, fullgraph=False)):
            for step in range(1, 5):
                out2 = m2(inp2)
                out2.sum().backward()

                # Graph break on TracableCreateParameter.backward
                # Recompile on grad==None/grad!=None
                self.assertEqual(bw_cnt.frame_count, min(step, 2) * 2)

        self.assertEqual(fw_cnt.frame_count, 1)
        self._assert_same_grad(m1.weight, m2.weight)
        self._assert_same_grad(inp1, inp2)
        self._assert_same_grad(out1, out2)

    def test_fake_distributed_aot_eager(self):
        m1, inp1 = init_fake_distributed()
        out1 = steps(m1, inp1)

        m2, inp2 = init_fake_distributed()
        m2 = torch.compile(m2, backend="aot_eager", fullgraph=True)
        bw_cnt = CompileCounter()
        with compiled_autograd.enable(torch.compile(backend=bw_cnt, fullgraph=True)):
            out2 = steps(m2, inp2)

        self._assert_same_grad(m1.weight, m2.weight)
        self._assert_same_grad(inp1, inp2)
        self._assert_same_grad(out1, out2)
        # Recompile on grad==None/grad!=None
        self.assertEqual(bw_cnt.frame_count, 2)

    def test_fake_distributed_inductor_resize(self):
        m1, inp1 = init_fake_distributed()
        out1 = steps(m1, inp1)

        m2, inp2 = init_fake_distributed()
        m2 = torch.compile(m2, fullgraph=True)
        # The forward runs successfully, but functionalizing the backward errors today.
        # See bullet (8) of the description at https://github.com/pytorch/pytorch/pull/120971 for more  details.
        # TLDR: we see the parameter as two separate inputs in the bw graph:
        # (a) one from a saved activation (potential the weight.t())
        # (b) one from the pre-backward hook closing over the parameter
        # The second is what actually sees the resize_() / copy in,
        # while the first is used in the actual compute.
        # The easiest way to handle this would be to figure out how to de-duplicate the parameter
        # before we functionalize the backward.
        # This can be done either as:
        # (1) a custom FX pass (Will F. has pass for it, although it is not guaranteed to be safe)
        # (2) Detecting when input aliases an safely be deduplicated and regenerated inside of the graph.
        #     This is likely safer but will be a reasonable amount of work
        with compiled_autograd.enable(torch.compile(fullgraph=True)):
            # out2 = steps(m2, inp2)  # Add back when backward works
            out = m2(inp2)
            with self.assertRaisesRegex(
                RuntimeError, "requiring a storage size of 800 are out of bounds"
            ):
                out.sum().backward()

                self._assert_same_grad(m1.weight, m2.weight)
                self._assert_same_grad(inp1, inp2)
                # self._assert_same_grad(out1, out2)

    # We can kill this test once we fix resizing in the backward, see previous test
    @patch(f"{__name__}.RESIZE", False)
    def test_fake_distributed_inductor_no_resize(self):
        m1, inp1 = init_fake_distributed()
        out1 = steps(m1, inp1)

        m2, inp2 = init_fake_distributed()
        m2 = torch.compile(m2, fullgraph=True)
        with compiled_autograd.enable(torch.compile(fullgraph=True)):
            out2 = steps(m2, inp2)

        self._assert_same_grad(m1.weight, m2.weight)
        self._assert_same_grad(inp1, inp2)
        self._assert_same_grad(out1, out2)


if __name__ == "__main__":
    if HAS_CPU and not IS_MACOS:
        run_tests(needs="filelock")
