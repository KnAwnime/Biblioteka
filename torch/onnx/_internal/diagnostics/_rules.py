"""
GENERATED CODE - DO NOT EDIT DIRECTLY
This file is generated by gen_diagnostics.py.
See tools/onnx/gen_diagnostics.py for more information.

Diagnostic rules for PyTorch ONNX export.
"""

import dataclasses
from typing import Tuple

# flake8: noqa
from torch.onnx._internal.diagnostics import infra

"""
GENERATED CODE - DO NOT EDIT DIRECTLY
The purpose of generating a class for each rule is to override the `format_message`
method to provide more details in the signature about the format arguments.
"""


class _NodeMissingOnnxShapeInference(infra.Rule):
    """Node is missing ONNX shape inference."""

    def format_message(self, op_name) -> str:  # type: ignore[override]
        """Returns the formatted default message of this Rule.

        Message template: 'The shape inference of {op_name} type is missing, so it may result in wrong shape inference for the exported graph. Please consider adding it in symbolic function.'
        """
        return self.message_default_template.format(op_name=op_name)

    def format(  # type: ignore[override]
        self, level: infra.Level, op_name
    ) -> Tuple[infra.Rule, infra.Level, str]:
        """Returns a tuple of (Rule, Level, message) for this Rule.

        Message template: 'The shape inference of {op_name} type is missing, so it may result in wrong shape inference for the exported graph. Please consider adding it in symbolic function.'
        """
        return self, level, self.format_message(op_name=op_name)


class _MissingCustomSymbolicFunction(infra.Rule):
    """Missing symbolic function for custom PyTorch operator, cannot translate node to ONNX."""

    def format_message(self, op_name) -> str:  # type: ignore[override]
        """Returns the formatted default message of this Rule.

        Message template: 'ONNX export failed on an operator with unrecognized namespace {op_name}. If you are trying to export a custom operator, make sure you registered it with the right domain and version.'
        """
        return self.message_default_template.format(op_name=op_name)

    def format(  # type: ignore[override]
        self, level: infra.Level, op_name
    ) -> Tuple[infra.Rule, infra.Level, str]:
        """Returns a tuple of (Rule, Level, message) for this Rule.

        Message template: 'ONNX export failed on an operator with unrecognized namespace {op_name}. If you are trying to export a custom operator, make sure you registered it with the right domain and version.'
        """
        return self, level, self.format_message(op_name=op_name)


class _MissingStandardSymbolicFunction(infra.Rule):
    """Missing symbolic function for standard PyTorch operator, cannot translate node to ONNX."""

    def format_message(  # type: ignore[override]
        self, op_name, opset_version, issue_url
    ) -> str:
        """Returns the formatted default message of this Rule.

        Message template: "Exporting the operator '{op_name}' to ONNX opset version {opset_version} is not supported. Please feel free to request support or submit a pull request on PyTorch GitHub: {issue_url}."
        """
        return self.message_default_template.format(
            op_name=op_name, opset_version=opset_version, issue_url=issue_url
        )

    def format(  # type: ignore[override]
        self, level: infra.Level, op_name, opset_version, issue_url
    ) -> Tuple[infra.Rule, infra.Level, str]:
        """Returns a tuple of (Rule, Level, message) for this Rule.

        Message template: "Exporting the operator '{op_name}' to ONNX opset version {opset_version} is not supported. Please feel free to request support or submit a pull request on PyTorch GitHub: {issue_url}."
        """
        return (
            self,
            level,
            self.format_message(
                op_name=op_name, opset_version=opset_version, issue_url=issue_url
            ),
        )


class _OperatorSupportedInNewerOpsetVersion(infra.Rule):
    """Operator is supported in newer opset version."""

    def format_message(  # type: ignore[override]
        self, op_name, opset_version, supported_opset_version
    ) -> str:
        """Returns the formatted default message of this Rule.

        Message template: "Exporting the operator '{op_name}' to ONNX opset version {opset_version} is not supported. Support for this operator was added in version {supported_opset_version}, try exporting with this version."
        """
        return self.message_default_template.format(
            op_name=op_name,
            opset_version=opset_version,
            supported_opset_version=supported_opset_version,
        )

    def format(  # type: ignore[override]
        self, level: infra.Level, op_name, opset_version, supported_opset_version
    ) -> Tuple[infra.Rule, infra.Level, str]:
        """Returns a tuple of (Rule, Level, message) for this Rule.

        Message template: "Exporting the operator '{op_name}' to ONNX opset version {opset_version} is not supported. Support for this operator was added in version {supported_opset_version}, try exporting with this version."
        """
        return (
            self,
            level,
            self.format_message(
                op_name=op_name,
                opset_version=opset_version,
                supported_opset_version=supported_opset_version,
            ),
        )


class _FxTracerSuccess(infra.Rule):
    """FX Tracer succeeded."""

    def format_message(self, fn_name, tracer_name) -> str:  # type: ignore[override]
        """Returns the formatted default message of this Rule.

        Message template: "The callable '{fn_name}' is successfully traced as a 'torch.fx.GraphModule' by '{tracer_name}'."
        """
        return self.message_default_template.format(
            fn_name=fn_name, tracer_name=tracer_name
        )

    def format(  # type: ignore[override]
        self, level: infra.Level, fn_name, tracer_name
    ) -> Tuple[infra.Rule, infra.Level, str]:
        """Returns a tuple of (Rule, Level, message) for this Rule.

        Message template: "The callable '{fn_name}' is successfully traced as a 'torch.fx.GraphModule' by '{tracer_name}'."
        """
        return (
            self,
            level,
            self.format_message(fn_name=fn_name, tracer_name=tracer_name),
        )


class _FxTracerFailure(infra.Rule):
    """FX Tracer failed."""

    def format_message(  # type: ignore[override]
        self, fn_name, tracer_name, explanation
    ) -> str:
        """Returns the formatted default message of this Rule.

        Message template: "The callable '{fn_name}' is not successfully traced as a 'torch.fx.GraphModule' by '{tracer_name}'.\n{explanation}"
        """
        return self.message_default_template.format(
            fn_name=fn_name, tracer_name=tracer_name, explanation=explanation
        )

    def format(  # type: ignore[override]
        self, level: infra.Level, fn_name, tracer_name, explanation
    ) -> Tuple[infra.Rule, infra.Level, str]:
        """Returns a tuple of (Rule, Level, message) for this Rule.

        Message template: "The callable '{fn_name}' is not successfully traced as a 'torch.fx.GraphModule' by '{tracer_name}'.\n{explanation}"
        """
        return (
            self,
            level,
            self.format_message(
                fn_name=fn_name, tracer_name=tracer_name, explanation=explanation
            ),
        )


class _FxFrontendAotautograd(infra.Rule):
    """FX Tracer succeeded."""

    def format_message(self, fn_name, tracer_name) -> str:  # type: ignore[override]
        """Returns the formatted default message of this Rule.

        Message template: "The callable '{fn_name}' is successfully traced as a 'torch.fx.GraphModule' by '{tracer_name}'."
        """
        return self.message_default_template.format(
            fn_name=fn_name, tracer_name=tracer_name
        )

    def format(  # type: ignore[override]
        self, level: infra.Level, fn_name, tracer_name
    ) -> Tuple[infra.Rule, infra.Level, str]:
        """Returns a tuple of (Rule, Level, message) for this Rule.

        Message template: "The callable '{fn_name}' is successfully traced as a 'torch.fx.GraphModule' by '{tracer_name}'."
        """
        return (
            self,
            level,
            self.format_message(fn_name=fn_name, tracer_name=tracer_name),
        )


class _FxPassConvertNegToSigmoid(infra.Rule):
    """FX pass converting torch.neg to torch.sigmoid."""

    def format_message(  # type: ignore[override]
        self,
    ) -> str:
        """Returns the formatted default message of this Rule.

        Message template: "Running 'convert-neg-to-sigmoid' pass on 'torch.fx.GraphModule'."
        """
        return self.message_default_template.format()

    def format(  # type: ignore[override]
        self,
        level: infra.Level,
    ) -> Tuple[infra.Rule, infra.Level, str]:
        """Returns a tuple of (Rule, Level, message) for this Rule.

        Message template: "Running 'convert-neg-to-sigmoid' pass on 'torch.fx.GraphModule'."
        """
        return self, level, self.format_message()


class _FxIrAddNode(infra.Rule):
    """ToDo, experimenting diagnostics, placeholder text."""

    def format_message(  # type: ignore[override]
        self,
    ) -> str:
        """Returns the formatted default message of this Rule.

        Message template: 'ToDo, experimenting diagnostics, placeholder text.'
        """
        return self.message_default_template.format()

    def format(  # type: ignore[override]
        self,
        level: infra.Level,
    ) -> Tuple[infra.Rule, infra.Level, str]:
        """Returns a tuple of (Rule, Level, message) for this Rule.

        Message template: 'ToDo, experimenting diagnostics, placeholder text.'
        """
        return self, level, self.format_message()


class _AtenlibSymbolicFunction(infra.Rule):
    """Op level tracking. ToDo, experimenting diagnostics, placeholder text."""

    def format_message(  # type: ignore[override]
        self,
    ) -> str:
        """Returns the formatted default message of this Rule.

        Message template: 'ToDo, experimenting diagnostics, placeholder text.'
        """
        return self.message_default_template.format()

    def format(  # type: ignore[override]
        self,
        level: infra.Level,
    ) -> Tuple[infra.Rule, infra.Level, str]:
        """Returns a tuple of (Rule, Level, message) for this Rule.

        Message template: 'ToDo, experimenting diagnostics, placeholder text.'
        """
        return self, level, self.format_message()


class _AtenlibFxToOnnx(infra.Rule):
    """Graph level tracking. Each op is a step. ToDo, experimenting diagnostics, placeholder text."""

    def format_message(  # type: ignore[override]
        self,
    ) -> str:
        """Returns the formatted default message of this Rule.

        Message template: 'ToDo, experimenting diagnostics, placeholder text.'
        """
        return self.message_default_template.format()

    def format(  # type: ignore[override]
        self,
        level: infra.Level,
    ) -> Tuple[infra.Rule, infra.Level, str]:
        """Returns a tuple of (Rule, Level, message) for this Rule.

        Message template: 'ToDo, experimenting diagnostics, placeholder text.'
        """
        return self, level, self.format_message()


class _FxNodeToOnnx(infra.Rule):
    """Node level tracking. ToDo, experimenting diagnostics, placeholder text."""

    def format_message(  # type: ignore[override]
        self,
    ) -> str:
        """Returns the formatted default message of this Rule.

        Message template: 'ToDo, experimenting diagnostics, placeholder text.'
        """
        return self.message_default_template.format()

    def format(  # type: ignore[override]
        self,
        level: infra.Level,
    ) -> Tuple[infra.Rule, infra.Level, str]:
        """Returns a tuple of (Rule, Level, message) for this Rule.

        Message template: 'ToDo, experimenting diagnostics, placeholder text.'
        """
        return self, level, self.format_message()


class _FxFrontendDynamoMakeFx(infra.Rule):
    """The make_fx + decomposition pass on fx graph produced from Dynamo, before ONNX export."""

    def format_message(  # type: ignore[override]
        self,
    ) -> str:
        """Returns the formatted default message of this Rule.

        Message template: 'ToDo, experimenting diagnostics, placeholder text.'
        """
        return self.message_default_template.format()

    def format(  # type: ignore[override]
        self,
        level: infra.Level,
    ) -> Tuple[infra.Rule, infra.Level, str]:
        """Returns a tuple of (Rule, Level, message) for this Rule.

        Message template: 'ToDo, experimenting diagnostics, placeholder text.'
        """
        return self, level, self.format_message()


class _FxPass(infra.Rule):
    """FX graph transformation before ONNX export."""

    def format_message(  # type: ignore[override]
        self,
    ) -> str:
        """Returns the formatted default message of this Rule.

        Message template: 'ToDo, experimenting diagnostics, placeholder text.'
        """
        return self.message_default_template.format()

    def format(  # type: ignore[override]
        self,
        level: infra.Level,
    ) -> Tuple[infra.Rule, infra.Level, str]:
        """Returns a tuple of (Rule, Level, message) for this Rule.

        Message template: 'ToDo, experimenting diagnostics, placeholder text.'
        """
        return self, level, self.format_message()


class _NoSymbolicFunctionForCallFunction(infra.Rule):
    """Cannot find symbolic function to convert the "call_function" FX node to ONNX."""

    def format_message(self, target) -> str:  # type: ignore[override]
        """Returns the formatted default message of this Rule.

        Message template: 'No symbolic function to convert the "call_function" node {target} to ONNX. '
        """
        return self.message_default_template.format(target=target)

    def format(  # type: ignore[override]
        self, level: infra.Level, target
    ) -> Tuple[infra.Rule, infra.Level, str]:
        """Returns a tuple of (Rule, Level, message) for this Rule.

        Message template: 'No symbolic function to convert the "call_function" node {target} to ONNX. '
        """
        return self, level, self.format_message(target=target)


class _UnsupportedFxNodeAnalysis(infra.Rule):
    """Result from FX graph analysis to reveal unsupported FX nodes."""

    def format_message(  # type: ignore[override]
        self, node_op_to_target_mapping
    ) -> str:
        """Returns the formatted default message of this Rule.

        Message template: 'Unsupported FX nodes: {node_op_to_target_mapping}. '
        """
        return self.message_default_template.format(
            node_op_to_target_mapping=node_op_to_target_mapping
        )

    def format(  # type: ignore[override]
        self, level: infra.Level, node_op_to_target_mapping
    ) -> Tuple[infra.Rule, infra.Level, str]:
        """Returns a tuple of (Rule, Level, message) for this Rule.

        Message template: 'Unsupported FX nodes: {node_op_to_target_mapping}. '
        """
        return (
            self,
            level,
            self.format_message(node_op_to_target_mapping=node_op_to_target_mapping),
        )


class _OpLevelDebugging(infra.Rule):
    """Report any op level validation failure in warnings."""

    def format_message(self, node, symbolic_fn) -> str:  # type: ignore[override]
        """Returns the formatted default message of this Rule.

        Message template: 'FX node: {node} and its onnx function: {symbolic_fn} fails on op level validation.'
        """
        return self.message_default_template.format(node=node, symbolic_fn=symbolic_fn)

    def format(  # type: ignore[override]
        self, level: infra.Level, node, symbolic_fn
    ) -> Tuple[infra.Rule, infra.Level, str]:
        """Returns a tuple of (Rule, Level, message) for this Rule.

        Message template: 'FX node: {node} and its onnx function: {symbolic_fn} fails on op level validation.'
        """
        return self, level, self.format_message(node=node, symbolic_fn=symbolic_fn)


class _ArgFormatTooVerbose(infra.Rule):
    """The formatted str for argument to display is too verbose."""

    def format_message(  # type: ignore[override]
        self, length, length_limit, argument_type, formatter_type
    ) -> str:
        """Returns the formatted default message of this Rule.

        Message template: 'Too verbose ({length} > {length_limit}). Argument type {argument_type} for formatter {formatter_type}.'
        """
        return self.message_default_template.format(
            length=length,
            length_limit=length_limit,
            argument_type=argument_type,
            formatter_type=formatter_type,
        )

    def format(  # type: ignore[override]
        self, level: infra.Level, length, length_limit, argument_type, formatter_type
    ) -> Tuple[infra.Rule, infra.Level, str]:
        """Returns a tuple of (Rule, Level, message) for this Rule.

        Message template: 'Too verbose ({length} > {length_limit}). Argument type {argument_type} for formatter {formatter_type}.'
        """
        return (
            self,
            level,
            self.format_message(
                length=length,
                length_limit=length_limit,
                argument_type=argument_type,
                formatter_type=formatter_type,
            ),
        )


@dataclasses.dataclass
class _POERules(infra.RuleCollection):
    node_missing_onnx_shape_inference: _NodeMissingOnnxShapeInference = dataclasses.field(
        default=_NodeMissingOnnxShapeInference.from_sarif(
            **{
                "id": "POE0001",
                "name": "node-missing-onnx-shape-inference",
                "short_description": {"text": "Node is missing ONNX shape inference."},
                "full_description": {
                    "text": "Node is missing ONNX shape inference. This usually happens when the node is not valid under standard ONNX operator spec.",
                    "markdown": "Node is missing ONNX shape inference.\nThis usually happens when the node is not valid under standard ONNX operator spec.\n",
                },
                "message_strings": {
                    "default": {
                        "text": "The shape inference of {op_name} type is missing, so it may result in wrong shape inference for the exported graph. Please consider adding it in symbolic function."
                    }
                },
                "help_uri": None,
                "properties": {"deprecated": False, "tags": []},
            }
        ),
        init=False,
    )
    """Node is missing ONNX shape inference."""

    missing_custom_symbolic_function: _MissingCustomSymbolicFunction = dataclasses.field(
        default=_MissingCustomSymbolicFunction.from_sarif(
            **{
                "id": "POE0002",
                "name": "missing-custom-symbolic-function",
                "short_description": {
                    "text": "Missing symbolic function for custom PyTorch operator, cannot translate node to ONNX."
                },
                "full_description": {
                    "text": "Missing symbolic function for custom PyTorch operator, cannot translate node to ONNX.",
                    "markdown": "Missing symbolic function for custom PyTorch operator, cannot translate node to ONNX.\n",
                },
                "message_strings": {
                    "default": {
                        "text": "ONNX export failed on an operator with unrecognized namespace {op_name}. If you are trying to export a custom operator, make sure you registered it with the right domain and version."
                    }
                },
                "help_uri": None,
                "properties": {"deprecated": False, "tags": []},
            }
        ),
        init=False,
    )
    """Missing symbolic function for custom PyTorch operator, cannot translate node to ONNX."""

    missing_standard_symbolic_function: _MissingStandardSymbolicFunction = dataclasses.field(
        default=_MissingStandardSymbolicFunction.from_sarif(
            **{
                "id": "POE0003",
                "name": "missing-standard-symbolic-function",
                "short_description": {
                    "text": "Missing symbolic function for standard PyTorch operator, cannot translate node to ONNX."
                },
                "full_description": {
                    "text": "Missing symbolic function for standard PyTorch operator, cannot translate node to ONNX.",
                    "markdown": "Missing symbolic function for standard PyTorch operator, cannot translate node to ONNX.\n",
                },
                "message_strings": {
                    "default": {
                        "text": "Exporting the operator '{op_name}' to ONNX opset version {opset_version} is not supported. Please feel free to request support or submit a pull request on PyTorch GitHub: {issue_url}."
                    }
                },
                "help_uri": None,
                "properties": {"deprecated": False, "tags": []},
            }
        ),
        init=False,
    )
    """Missing symbolic function for standard PyTorch operator, cannot translate node to ONNX."""

    operator_supported_in_newer_opset_version: _OperatorSupportedInNewerOpsetVersion = dataclasses.field(
        default=_OperatorSupportedInNewerOpsetVersion.from_sarif(
            **{
                "id": "POE0004",
                "name": "operator-supported-in-newer-opset-version",
                "short_description": {
                    "text": "Operator is supported in newer opset version."
                },
                "full_description": {
                    "text": "Operator is supported in newer opset version.",
                    "markdown": "Operator is supported in newer opset version.\n\nExample:\n```python\ntorch.onnx.export(model, args, ..., opset_version=9)\n```\n",
                },
                "message_strings": {
                    "default": {
                        "text": "Exporting the operator '{op_name}' to ONNX opset version {opset_version} is not supported. Support for this operator was added in version {supported_opset_version}, try exporting with this version."
                    }
                },
                "help_uri": None,
                "properties": {"deprecated": False, "tags": []},
            }
        ),
        init=False,
    )
    """Operator is supported in newer opset version."""

    fx_tracer_success: _FxTracerSuccess = dataclasses.field(
        default=_FxTracerSuccess.from_sarif(
            **{
                "id": "FXE0001",
                "name": "fx-tracer-success",
                "short_description": {"text": "FX Tracer succeeded."},
                "full_description": {
                    "text": "FX Tracer succeeded. The callable is successfully traced as a 'torch.fx.GraphModule' by one of the fx tracers.",
                    "markdown": "FX Tracer succeeded.\nThe callable is successfully traced as a 'torch.fx.GraphModule' by one of the fx tracers.\n",
                },
                "message_strings": {
                    "default": {
                        "text": "The callable '{fn_name}' is successfully traced as a 'torch.fx.GraphModule' by '{tracer_name}'."
                    }
                },
                "help_uri": None,
                "properties": {"deprecated": False, "tags": []},
            }
        ),
        init=False,
    )
    """FX Tracer succeeded."""

    fx_tracer_failure: _FxTracerFailure = dataclasses.field(
        default=_FxTracerFailure.from_sarif(
            **{
                "id": "FXE0002",
                "name": "fx-tracer-failure",
                "short_description": {"text": "FX Tracer failed."},
                "full_description": {
                    "text": "FX Tracer failed. The callable is not successfully traced as a 'torch.fx.GraphModule'.",
                    "markdown": "FX Tracer failed.\nThe callable is not successfully traced as a 'torch.fx.GraphModule'.\n",
                },
                "message_strings": {
                    "default": {
                        "text": "The callable '{fn_name}' is not successfully traced as a 'torch.fx.GraphModule' by '{tracer_name}'.\n{explanation}"
                    }
                },
                "help_uri": None,
                "properties": {"deprecated": False, "tags": []},
            }
        ),
        init=False,
    )
    """FX Tracer failed."""

    fx_frontend_aotautograd: _FxFrontendAotautograd = dataclasses.field(
        default=_FxFrontendAotautograd.from_sarif(
            **{
                "id": "FXE0003",
                "name": "fx-frontend-aotautograd",
                "short_description": {"text": "FX Tracer succeeded."},
                "full_description": {
                    "text": "FX Tracer succeeded. The callable is successfully traced as a 'torch.fx.GraphModule' by one of the fx tracers.",
                    "markdown": "FX Tracer succeeded.\nThe callable is successfully traced as a 'torch.fx.GraphModule' by one of the fx tracers.\n",
                },
                "message_strings": {
                    "default": {
                        "text": "The callable '{fn_name}' is successfully traced as a 'torch.fx.GraphModule' by '{tracer_name}'."
                    }
                },
                "help_uri": None,
                "properties": {"deprecated": False, "tags": []},
            }
        ),
        init=False,
    )
    """FX Tracer succeeded."""

    fx_pass_convert_neg_to_sigmoid: _FxPassConvertNegToSigmoid = dataclasses.field(
        default=_FxPassConvertNegToSigmoid.from_sarif(
            **{
                "id": "FXE0004",
                "name": "fx-pass-convert-neg-to-sigmoid",
                "short_description": {
                    "text": "FX pass converting torch.neg to torch.sigmoid."
                },
                "full_description": {
                    "text": "A 'fx.Interpreter' based pass to convert all 'torch.neg' calls to 'torch.sigmoid' for a given 'torch.fx.GraphModule' object.",
                    "markdown": "A 'fx.Interpreter' based pass to convert all 'torch.neg' calls to 'torch.sigmoid' for\na given 'torch.fx.GraphModule' object.\n",
                },
                "message_strings": {
                    "default": {
                        "text": "Running 'convert-neg-to-sigmoid' pass on 'torch.fx.GraphModule'."
                    }
                },
                "help_uri": None,
                "properties": {"deprecated": False, "tags": []},
            }
        ),
        init=False,
    )
    """FX pass converting torch.neg to torch.sigmoid."""

    fx_ir_add_node: _FxIrAddNode = dataclasses.field(
        default=_FxIrAddNode.from_sarif(
            **{
                "id": "FXE0005",
                "name": "fx-ir-add-node",
                "short_description": {
                    "text": "ToDo, experimenting diagnostics, placeholder text."
                },
                "full_description": {
                    "text": "ToDo, experimenting diagnostics, placeholder text.",
                    "markdown": "ToDo, experimenting diagnostics, placeholder text.\n",
                },
                "message_strings": {
                    "default": {
                        "text": "ToDo, experimenting diagnostics, placeholder text."
                    }
                },
                "help_uri": None,
                "properties": {"deprecated": False, "tags": []},
            }
        ),
        init=False,
    )
    """ToDo, experimenting diagnostics, placeholder text."""

    atenlib_symbolic_function: _AtenlibSymbolicFunction = dataclasses.field(
        default=_AtenlibSymbolicFunction.from_sarif(
            **{
                "id": "FXE0006",
                "name": "atenlib-symbolic-function",
                "short_description": {
                    "text": "Op level tracking. ToDo, experimenting diagnostics, placeholder text."
                },
                "full_description": {
                    "text": "ToDo, experimenting diagnostics, placeholder text.",
                    "markdown": "ToDo, experimenting diagnostics, placeholder text.\n",
                },
                "message_strings": {
                    "default": {
                        "text": "ToDo, experimenting diagnostics, placeholder text."
                    }
                },
                "help_uri": None,
                "properties": {"deprecated": False, "tags": []},
            }
        ),
        init=False,
    )
    """Op level tracking. ToDo, experimenting diagnostics, placeholder text."""

    atenlib_fx_to_onnx: _AtenlibFxToOnnx = dataclasses.field(
        default=_AtenlibFxToOnnx.from_sarif(
            **{
                "id": "FXE0007",
                "name": "atenlib-fx-to-onnx",
                "short_description": {
                    "text": "Graph level tracking. Each op is a step. ToDo, experimenting diagnostics, placeholder text."
                },
                "full_description": {
                    "text": "ToDo, experimenting diagnostics, placeholder text.",
                    "markdown": "ToDo, experimenting diagnostics, placeholder text.\n",
                },
                "message_strings": {
                    "default": {
                        "text": "ToDo, experimenting diagnostics, placeholder text."
                    }
                },
                "help_uri": None,
                "properties": {"deprecated": False, "tags": []},
            }
        ),
        init=False,
    )
    """Graph level tracking. Each op is a step. ToDo, experimenting diagnostics, placeholder text."""

    fx_node_to_onnx: _FxNodeToOnnx = dataclasses.field(
        default=_FxNodeToOnnx.from_sarif(
            **{
                "id": "FXE0008",
                "name": "fx-node-to-onnx",
                "short_description": {
                    "text": "Node level tracking. ToDo, experimenting diagnostics, placeholder text."
                },
                "full_description": {
                    "text": "ToDo, experimenting diagnostics, placeholder text.",
                    "markdown": "ToDo, experimenting diagnostics, placeholder text.\n",
                },
                "message_strings": {
                    "default": {
                        "text": "ToDo, experimenting diagnostics, placeholder text."
                    }
                },
                "help_uri": None,
                "properties": {"deprecated": False, "tags": []},
            }
        ),
        init=False,
    )
    """Node level tracking. ToDo, experimenting diagnostics, placeholder text."""

    fx_frontend_dynamo_make_fx: _FxFrontendDynamoMakeFx = dataclasses.field(
        default=_FxFrontendDynamoMakeFx.from_sarif(
            **{
                "id": "FXE0009",
                "name": "fx-frontend-dynamo-make-fx",
                "short_description": {
                    "text": "The make_fx + decomposition pass on fx graph produced from Dynamo, before ONNX export."
                },
                "full_description": {
                    "text": "ToDo, experimenting diagnostics, placeholder text.",
                    "markdown": "ToDo, experimenting diagnostics, placeholder text.\n",
                },
                "message_strings": {
                    "default": {
                        "text": "ToDo, experimenting diagnostics, placeholder text."
                    }
                },
                "help_uri": None,
                "properties": {"deprecated": False, "tags": []},
            }
        ),
        init=False,
    )
    """The make_fx + decomposition pass on fx graph produced from Dynamo, before ONNX export."""

    fx_pass: _FxPass = dataclasses.field(
        default=_FxPass.from_sarif(
            **{
                "id": "FXE0010",
                "name": "fx-pass",
                "short_description": {
                    "text": "FX graph transformation before ONNX export."
                },
                "full_description": {
                    "text": "ToDo, experimenting diagnostics, placeholder text.",
                    "markdown": "ToDo, experimenting diagnostics, placeholder text.\n",
                },
                "message_strings": {
                    "default": {
                        "text": "ToDo, experimenting diagnostics, placeholder text."
                    }
                },
                "help_uri": None,
                "properties": {"deprecated": False, "tags": []},
            }
        ),
        init=False,
    )
    """FX graph transformation before ONNX export."""

    no_symbolic_function_for_call_function: _NoSymbolicFunctionForCallFunction = dataclasses.field(
        default=_NoSymbolicFunctionForCallFunction.from_sarif(
            **{
                "id": "FXE0011",
                "name": "no-symbolic-function-for-call-function",
                "short_description": {
                    "text": 'Cannot find symbolic function to convert the "call_function" FX node to ONNX.'
                },
                "full_description": {
                    "text": 'Cannot find symbolic function to convert the "call_function" FX node to ONNX. ',
                    "markdown": 'This error occurs when the ONNX converter is unable to find a corresponding symbolic function\nto convert a "call_function" node in the input graph to its equivalence in ONNX. The "call_function"\nnode represents a normalized function call in PyTorch, such as "torch.aten.ops.add".\n\nTo resolve this error, you can try one of the following:\n\n- If exists, apply the auto-fix suggested by the diagnostic. TODO: this part is not available yet.\n- Rewrite the model using only supported PyTorch operators or functions.\n- Follow this [guide](https://pytorch.org/docs/stable/onnx.html#onnx-script-functions) to write and\n  register a custom symbolic function for the unsupported call_function FX node.\n\nTODO: Replace above link once docs for `dynamo_export` custom op registration are available.\n',
                },
                "message_strings": {
                    "default": {
                        "text": 'No symbolic function to convert the "call_function" node {target} to ONNX. '
                    }
                },
                "help_uri": None,
                "properties": {"deprecated": False, "tags": []},
            }
        ),
        init=False,
    )
    """Cannot find symbolic function to convert the "call_function" FX node to ONNX."""

    unsupported_fx_node_analysis: _UnsupportedFxNodeAnalysis = dataclasses.field(
        default=_UnsupportedFxNodeAnalysis.from_sarif(
            **{
                "id": "FXE0012",
                "name": "unsupported-fx-node-analysis",
                "short_description": {
                    "text": "Result from FX graph analysis to reveal unsupported FX nodes."
                },
                "full_description": {
                    "text": "Result from FX graph analysis to reveal unsupported FX nodes.",
                    "markdown": "This error indicates that an FX graph contains one or more unsupported nodes. The error message\nis typically accompanied by a list of the unsupported nodes found during analysis.\n\nTo resolve this error, you can try resolving each individual unsupported node error by following\nthe suggestions by its diagnostic. Typically, options include:\n\n- If exists, apply the auto-fix suggested by the diagnostic. TODO: this part is not available yet.\n- Rewrite the model using only supported PyTorch operators or functions.\n- Follow this [guide](https://pytorch.org/docs/stable/onnx.html#onnx-script-functions) to write and\n  register a custom symbolic function for the unsupported call_function FX node.\n",
                },
                "message_strings": {
                    "default": {
                        "text": "Unsupported FX nodes: {node_op_to_target_mapping}. "
                    }
                },
                "help_uri": None,
                "properties": {"deprecated": False, "tags": []},
            }
        ),
        init=False,
    )
    """Result from FX graph analysis to reveal unsupported FX nodes."""

    op_level_debugging: _OpLevelDebugging = dataclasses.field(
        default=_OpLevelDebugging.from_sarif(
            **{
                "id": "FXE0013",
                "name": "op-level-debugging",
                "short_description": {
                    "text": "Report any op level validation failure in warnings."
                },
                "full_description": {
                    "text": "Report any op level validation failure in warnings..",
                    "markdown": "This warning indicates that in op level debugging, the selected symbolic functions are failed\nto match torch ops results when we use generated real tensors from fake tensors. It is worth\nnoting that the symbolic functions are not necessarily wrong, as the validation is non-deteministic,\nand it's for reference.\n\nThey could be caused by the following reasons:\n\nPyTorch operators:\n- IndexError: Unsupported input args of randomnized dim/indices(INT64).\n- RuntimeError: Unsupported input args for torch op are generated.\n\ntorchlib operators:\n- ValueError: args/kwargs do not match the signature of the symbolic function.\n- RuntimeError: Unsupported input args for torchlib op are generated.\n- AssertionError: The symbolic function is potentially wrong.\n",
                },
                "message_strings": {
                    "default": {
                        "text": "FX node: {node} and its onnx function: {symbolic_fn} fails on op level validation."
                    }
                },
                "help_uri": None,
                "properties": {"deprecated": False, "tags": []},
            }
        ),
        init=False,
    )
    """Report any op level validation failure in warnings."""

    arg_format_too_verbose: _ArgFormatTooVerbose = dataclasses.field(
        default=_ArgFormatTooVerbose.from_sarif(
            **{
                "id": "DIAGSYS0001",
                "name": "arg-format-too-verbose",
                "short_description": {
                    "text": "The formatted str for argument to display is too verbose."
                },
                "full_description": {
                    "text": "ToDo, experimenting diagnostics, placeholder text.",
                    "markdown": "ToDo, experimenting diagnostics, placeholder text.\n",
                },
                "message_strings": {
                    "default": {
                        "text": "Too verbose ({length} > {length_limit}). Argument type {argument_type} for formatter {formatter_type}."
                    }
                },
                "help_uri": None,
                "properties": {"deprecated": False, "tags": []},
            }
        ),
        init=False,
    )
    """The formatted str for argument to display is too verbose."""


rules = _POERules()
