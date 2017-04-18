from numbers import Integral

from .module import Module
from .. import functional as F
from .utils import _pair


class UpsamplingNearest2d(Module):
    """
    Applies a 2D nearest neighbor upsampling to an input signal composed of several input
    channels.

    To specify the scale, it takes either the :attr:`size` or the :attr:`scale_factor`
    as it's constructor argument.

    When `size` is given, it is the output size of the image (h, w).

    Args:
        size (tuple, optional): a tuple of ints (H_out, W_out) output sizes
        scale_factor (int, optional): the multiplier for the image height / width

    Shape:
        - Input: :math:`(N, C, H_{in}, W_{in})`
        - Output: :math:`(N, C, H_{out}, W_{out})` where
          :math:`H_{out} = floor(H_{in} * scale\_factor)`
          :math:`W_{out} = floor(W_{in}  * scale\_factor)`

    Examples::

        >>> inp
        Variable containing:
        (0 ,0 ,.,.) =
          1  2
          3  4
        [torch.FloatTensor of size 1x1x2x2]

        >>> m = nn.UpsamplingNearest2d(scale_factor=2)
        >>> m(inp)
        Variable containing:
        (0 ,0 ,.,.) =
          1  1  2  2
          1  1  2  2
          3  3  4  4
          3  3  4  4
        [torch.FloatTensor of size 1x1x4x4]

    """
    def __init__(self, size=None, scale_factor=None):
        super(UpsamplingNearest2d, self).__init__()
        if size is None and scale_factor is None:
            raise ValueError('either size or scale_factor should be defined')
        if scale_factor is not None and not isinstance(scale_factor, Integral):
            raise ValueError('scale_factor must be of integer type')
        self.size = _pair(size)
        self.scale_factor = scale_factor

    def __repr__(self):
        if self.scale_factor is not None:
            info = 'scale_factor=' + str(self.scale_factor)
        else:
            info = 'size=' + str(self.size)
        return self.__class__.__name__ + '(' + info + ')'

    def forward(self, input):
        return F.upsample_nearest(input, self.size, self.scale_factor)


class UpsamplingBilinear2d(Module):
    """Applies a 2D bilinear upsampling to an input signal composed of several input
    channels.

    To specify the scale, it takes either the :attr:`size` or the :attr:`scale_factor`
    as it's constructor argument.

    When `size` is given, it is the output size of the image (h, w).

    When `scale_factor` is given, it must be either an integer (in
    which case the image will be scaled with a constant aspect
    ratio), or a tuple of integers (height scalar, width scalar).

    Args:
        size (tuple, optional): a tuple of ints (H_out, W_out) output sizes
        scale_factor (int or Tuple[int, int], optional): the multiplier for the image height / width

    Shape:
        - Input: :math:`(N, C, H_{in}, W_{in})`
        - Output: :math:`(N, C, H_{out}, W_{out})` where
          :math:`H_{out} = floor(H_{in} * scale\_factor\_h)`
          :math:`W_{out} = floor(W_{in}  * scale\_factor\_w)`

    Examples::

        >>> inp
        Variable containing:
        (0 ,0 ,.,.) =
          1  2
          3  4
        [torch.FloatTensor of size 1x1x2x2]

        >>> m = nn.UpsamplingBilinear2d(scale_factor=2)
        >>> m(inp)
        Variable containing:
        (0 ,0 ,.,.) =
          1.0000  1.3333  1.6667  2.0000
          1.6667  2.0000  2.3333  2.6667
          2.3333  2.6667  3.0000  3.3333
          3.0000  3.3333  3.6667  4.0000
        [torch.FloatTensor of size 1x1x4x4]

        >>> m = nn.UpsamplingBilinear2d(scale_factor=(2,1))
        >>> m(inp)
        Variable containing:
        (0 ,0 ,.,.) =
          1.0000  2.0000
          1.6667  2.6667
          2.3333  3.3333
          3.0000  4.0000
        [torch.FloatTensor of size 1x1x4x2]
    """
    def __init__(self, size=None, scale_factor=None):
        super(UpsamplingBilinear2d, self).__init__()
        if size is None and scale_factor is None:
            raise ValueError('either size or scale_factor should be defined')
        if scale_factor is not None:
            if not isinstance(scale_factor, (Integral, tuple)):
                raise ValueError('scale_factor must be a non-negative integer, or a tuple of non-negative integers')
            if isinstance(scale_factor, tuple):
                try:
                    assert len(scale_factor) == 2
                    for i in scale_factor:
                        assert isinstance(i, Integral)
                        assert i >= 1
                except AssertionError as e:
                    raise ValueError('scale_factor must be a non-negative integer, or a tuple of non-negative integers')
        self.size = _pair(size)
        if scale_factor is not None:
            self.scale_factor = _pair(scale_factor)
        else:
            self.scale_factor = scale_factor

    def __repr__(self):
        if self.scale_factor is not None:
            info = 'scale_factor=' + str(self.scale_factor)
        else:
            info = 'size=' + str(self.size)
        return self.__class__.__name__ + '(' + info + ')'

    def forward(self, input):
        return F.upsample_bilinear(input, self.size, self.scale_factor)
