# -*- coding: utf-8 -*-

import logging

import torch
from torch import nn
from torch.ao.sparsity import BaseSparsifier

from torch.testing._internal.common_utils import TestCase

logging.basicConfig(format='%(asctime)s - %(name)s - %(levelname)s - %(message)s', level=logging.INFO)

class Model(nn.Module):
    def __init__(self):
        super().__init__()
        self.seq = nn.Sequential(
            nn.Linear(3, 4)
        )
        self.linear = nn.Linear(4, 3)


class ImplementedSparsifier(BaseSparsifier):
    def __init__(self, **kwargs):
        super().__init__(defaults=kwargs)

    def step(self):
        linear_state = self.state['linear']
        linear_state['step_count'] = linear_state.get('step_count', 0) + 1


class TestBaseSparsifier(TestCase):
    def test_constructor(self):
        # Cannot instantiate the base
        self.assertRaisesRegex(TypeError, 'with abstract methods step',
                               BaseSparsifier)
        # Can instantiate the model with no configs
        model = Model()
        sparsifier = ImplementedSparsifier(test=3)
        sparsifier.prepare(model, config=None)
        assert len(sparsifier.module_groups) == 2
        sparsifier.step()
        # Can instantiate the model with configs
        sparsifier = ImplementedSparsifier(test=3)
        sparsifier.prepare(model, [model.linear])
        assert len(sparsifier.module_groups) == 1
        assert sparsifier.module_groups[0]['fqn'] == 'linear'
        assert 'test' in sparsifier.module_groups[0]
        assert sparsifier.module_groups[0]['test'] == 3

    def test_state_dict(self):
        step_count = 3
        model0 = Model()
        sparsifier0 = ImplementedSparsifier(test=3)
        sparsifier0.prepare(model0, [model0.linear])
        mask = model0.linear.parametrizations['weight'][0].mask
        mask.data = torch.arange(mask.shape[0] * mask.shape[1]).reshape(mask.shape)
        for step in range(step_count):
            sparsifier0.step()
        state_dict = sparsifier0.state_dict()

        # Check the expected keys in the state_dict
        assert 'state' in state_dict
        assert 'linear' in state_dict['state']
        assert 'mask' in state_dict['state']['linear']
        assert 'step_count' in state_dict['state']['linear']
        assert state_dict['state']['linear']['step_count'] == 3

        assert 'module_groups' in state_dict
        assert 'test' in state_dict['module_groups'][0]
        assert 'fqn' in state_dict['module_groups'][0]
        assert state_dict['module_groups'][0]['fqn'] == 'linear'

        # Check loading static_dict creates an equivalent model
        model1 = Model()
        sparsifier1 = ImplementedSparsifier()
        sparsifier1.prepare(model1, None)

        assert sparsifier0.state != sparsifier1.state

        # Make sure the masks are different in the beginning
        for mg in sparsifier0.module_groups:
            if mg['fqn'] == 'linear':
                mask0 = mg['module'].parametrizations.weight[0].mask
        for mg in sparsifier1.module_groups:
            if mg['fqn'] == 'linear':
                mask1 = mg['module'].parametrizations.weight[0].mask
        self.assertNotEqual(mask0, mask1)

        sparsifier1.load_state_dict(state_dict)
        # Make sure the states are loaded, and are correct
        assert sparsifier0.state == sparsifier1.state

        # Make sure the masks (and all dicts) are the same after loading
        assert len(sparsifier0.module_groups) == len(sparsifier1.module_groups)
        for idx in range(len(sparsifier0.module_groups)):
            mg0 = sparsifier0.module_groups[idx]
            mg1 = sparsifier1.module_groups[idx]
            for key in mg0.keys():
                assert key in mg1
                if key == 'module':
                    # We cannot compare modules as they are different
                    param0 = mg0[key].parametrizations.weight[0]
                    param1 = mg1[key].parametrizations.weight[0]
                    assert hasattr(param0, 'mask')
                    assert hasattr(param1, 'mask')
                    self.assertEqual(param0.__dict__, param1.__dict__)
                else:
                    assert mg0[key] == mg1[key]
