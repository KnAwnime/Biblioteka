#!/usr/bin/env python3

from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Set

import jinja2
from typing_extensions import Literal

YamlShellBool = Literal["''", 1]
Arch = Literal["windows", "linux"]

DOCKER_REGISTRY = "308535385114.dkr.ecr.us-east-1.amazonaws.com"
GITHUB_DIR = Path(__file__).resolve().parent.parent

WINDOWS_CPU_TEST_RUNNER = "windows.4xlarge"
WINDOWS_CUDA_TEST_RUNNER = "windows.8xlarge.nvidia.gpu"
WINDOWS_RUNNERS = {
    WINDOWS_CPU_TEST_RUNNER,
    WINDOWS_CUDA_TEST_RUNNER,
}

LINUX_CPU_TEST_RUNNER = "linux.2xlarge"
LINUX_CUDA_TEST_RUNNER = "linux.8xlarge.nvidia.gpu"
LINUX_RUNNERS = {
    LINUX_CPU_TEST_RUNNER,
    LINUX_CUDA_TEST_RUNNER,
}


# TODO: ------------- Remove the comment once fully rollout -------------------
#       Rollout Strategy:
#       1. Manual Phase
#          step 1. Add 'ciflow/default' label to the PR
#          step 2. Once there's an [unassigned] event from PR, it should rerun
#          step 3. Remove 'ciflow/default' label
#          step 4. Trigger the [unassigned] event again, it should not rerun
#       2. Probot Phase 1 (manual on 1 workflow)
#          step 1. Probot automatically add labels based on the context
#          step 2. Manually let probot trigger [unassigned] event
#       3. Probot Phase 2 (auto on 1 workflows)
#          step 1. Modify the workflows so that they only listen on [unassigned] events
#          step 2. Probot automatically adds labels automatically based on the context
#          step 3. Probot automatically triggers [unassigned] event
#       4. Probot Phase 3 (auto on many workflows)
#          step 1. Enable it for all workflows
#       -----------------------------------------------------------------------
@dataclass
class CIFlowConfig:
    enabled: bool = False
    labels: Set[str] = field(default_factory=set)
    trigger_action: str = 'unassigned'
    trigger_actor: str = 'pytorchbot'
    root_job_name: str = 'ciflow_should_run'
    root_job_condition: str = ''

    # trigger_action_only controls if we listen only on the trigger_action of a pull_request.
    # If it's False, we listen on all default pull_request actions, this is useful when
    # ciflow (via probot) is not automated yet.
    trigger_action_only: bool = False

    def gen_root_job_condition(self) -> None:
        # TODO: Make conditions strict
        # At the beginning of the rollout of ciflow, we keep everything the same as what we have
        # Once fully rollout, we can have strict constraints
        # e.g. ADD      env.GITHUB_ACTOR == '{self.trigger_actor}
        #      REMOVE   github.event.action !='{self.trigger_action}'
        label_conditions = [f"github.event.action == '{self.trigger_action}'"] + \
            [f"contains(github.event.pull_request.labels.*.name, '{label}')" for label in self.labels]
        self.root_job_condition = f"(github.event_name != 'pull_request') || " \
            f"(github.event.action !='{self.trigger_action}') || " \
            f"({' && '.join(label_conditions)})"

    def reset_root_job(self) -> None:
        self.root_job_name = ''
        self.root_job_condition = ''

    def __post_init__(self) -> None:
        if not self.enabled:
            self.reset_root_job()
            return
        self.gen_root_job_condition()


@dataclass
class CIWorkflow:
    # Required fields
    arch: Arch
    build_environment: str
    test_runner_type: str

    # Generated fields
    # generated fields will be populated in __post_init__
    ci_workflow_name: str = ''

    # Optional fields
    ciflow_config: CIFlowConfig = field(default_factory=CIFlowConfig)
    cuda_version: str = ''
    docker_image_base: str = ''
    enable_doc_jobs: bool = False
    exclude_test: bool = False
    is_libtorch: bool = False
    is_scheduled: str = ''
    num_test_shards: int = 1
    on_pull_request: bool = False
    only_build_on_pull_request: bool = False
    only_run_smoke_tests_on_pull_request: bool = False
    num_test_shards_on_pull_request: int = -1

    # The following variables will be set as environment variables,
    # so it's easier for both shell and Python scripts to consume it if false is represented as the empty string.
    enable_jit_legacy_test: YamlShellBool = "''"
    enable_multigpu_test: YamlShellBool = "''"
    enable_nogpu_no_avx_test: YamlShellBool = "''"
    enable_nogpu_no_avx2_test: YamlShellBool = "''"
    enable_slow_test: YamlShellBool = "''"

    def __post_init__(self) -> None:
        if self.is_libtorch:
            self.exclude_test = True

        # The following code allows for scheduled jobs to be debuggable by
        # adding the label 'ciflow/scheduled' and assigning + unassigning pytorchbot,
        # without overwriting the workflow's own ciflow_config, if already enabled.
        if self.is_scheduled:
            if self.ciflow_config.enabled:
                self.ciflow_config.labels.add('ciflow/scheduled')
            else:
                self.ciflow_config = CIFlowConfig(
                    enabled=True,
                    labels={'ciflow/scheduled'}
                )

        # CIFlow requires on_pull_request to be set in order to be enabled.
        # If on_pull_request wasn't previously specified, we set trigger_action_only
        # to True as we want to avoid unintentional pull_request event triggers
        if self.ciflow_config.enabled:
            self.ciflow_config.trigger_action_only = self.ciflow_config.trigger_action_only or not self.on_pull_request
            self.on_pull_request = True

        if not self.on_pull_request:
            self.only_build_on_pull_request = False

        # If num_test_shards_on_pull_request is not user-defined, default to num_test_shards unless we are
        # only running smoke tests on the pull request.
        if self.num_test_shards_on_pull_request == -1:
            # Don't waste resources on runner spinup and cooldown for another shard if we are only running a few tests
            if self.only_run_smoke_tests_on_pull_request:
                self.num_test_shards_on_pull_request = 1
            else:
                self.num_test_shards_on_pull_request = self.num_test_shards

        self.assert_valid()

    def assert_valid(self) -> None:
        err_message = f"invalid test_runner_type for {self.arch}: {self.test_runner_type}"
        if self.arch == 'linux':
            assert self.test_runner_type in LINUX_RUNNERS, err_message
        if self.arch == 'windows':
            assert self.test_runner_type in WINDOWS_RUNNERS, err_message
    
    def generate_workflow_name(self, workflow) -> str:
        workflow_name = workflow.build_environment
        if not workflow.on_pull_request:
            workflow_name = "master-only-" + workflow_name
        if workflow.is_scheduled:
            workflow_name = "periodic-" + workflow_name
        return workflow_name

    def generate_workflow_file(self, workflow_template: jinja2.Template) -> None:
        workflow.ci_workflow_name = self.generate_workflow_name(workflow)
        output_file_path = GITHUB_DIR / f"workflows/generated-{workflow.ci_workflow_name}.yml"
        with open(output_file_path, "w") as output_file:
            GENERATED = "generated"
            output_file.writelines([f"# @{GENERATED} DO NOT EDIT MANUALLY\n"])
            output_file.write(workflow_template.render(asdict(self)))
            output_file.write("\n")
        print(output_file_path)


WINDOWS_WORKFLOWS = [
    CIWorkflow(
        arch="windows",
        build_environment="win-vs2019-cpu-py3",
        cuda_version="cpu",
        test_runner_type=WINDOWS_CPU_TEST_RUNNER,
        on_pull_request=True,
        num_test_shards=2,
    ),
    CIWorkflow(
        arch="windows",
        build_environment="win-vs2019-cuda10-cudnn7-py3",
        cuda_version="10.1",
        test_runner_type=WINDOWS_CUDA_TEST_RUNNER,
        on_pull_request=True,
        only_run_smoke_tests_on_pull_request=True,
        num_test_shards=2,
    ),
    CIWorkflow(
        arch="windows",
        build_environment="win-vs2019-cuda11-cudnn8-py3",
        cuda_version="11.1",
        test_runner_type=WINDOWS_CUDA_TEST_RUNNER,
        num_test_shards=2,
    ),
    CIWorkflow(
        arch="windows",
        build_environment="win-vs2019-cuda11-cudnn8-py3",
        cuda_version="11.3",
        test_runner_type=WINDOWS_CUDA_TEST_RUNNER,
        num_test_shards=2,
        is_scheduled="45 0,4,8,12,16,20 * * *",
    ),
]

LINUX_WORKFLOWS = [
    CIWorkflow(
        arch="linux",
        build_environment="linux-xenial-py3.6-gcc5.4",
        docker_image_base=f"{DOCKER_REGISTRY}/pytorch/pytorch-linux-xenial-py3.6-gcc5.4",
        test_runner_type=LINUX_CPU_TEST_RUNNER,
        on_pull_request=True,
        enable_doc_jobs=True,
        num_test_shards=2,
    ),
    # CIWorkflow(
    #     arch="linux",
    #     build_environment="paralleltbb-linux-xenial-py3.6-gcc5.4",
    #     docker_image_base=f"{DOCKER_REGISTRY}/pytorch/pytorch-linux-xenial-py3.6-gcc5.4",
    #     test_runner_type=LINUX_CPU_TEST_RUNNER,
    # ),
    # CIWorkflow(
    #     arch="linux",
    #     build_environment="parallelnative-linux-xenial-py3.6-gcc5.4",
    #     docker_image_base=f"{DOCKER_REGISTRY}/pytorch/pytorch-linux-xenial-py3.6-gcc5.4",
    #     test_runner_type=LINUX_CPU_TEST_RUNNER,
    # ),
    # CIWorkflow(
    #     arch="linux",
    #     build_environment="pure_torch-linux-xenial-py3.6-gcc5.4",
    #     docker_image_base=f"{DOCKER_REGISTRY}/pytorch/pytorch-linux-xenial-py3.6-gcc5.4",
    #     test_runner_type=LINUX_CPU_TEST_RUNNER,
    # ),
    # CIWorkflow(
    #     arch="linux",
    #     build_environment="linux-xenial-py3.6-gcc7",
    #     docker_image_base=f"{DOCKER_REGISTRY}/pytorch/pytorch-linux-xenial-py3.6-gcc7",
    #     test_runner_type=LINUX_CPU_TEST_RUNNER,
    # ),
    # CIWorkflow(
    #     arch="linux",
    #     build_environment="linux-xenial-py3.6-clang5-asan",
    #     docker_image_base=f"{DOCKER_REGISTRY}/pytorch/pytorch-linux-xenial-py3-clang5-asan",
    #     test_runner_type=LINUX_CPU_TEST_RUNNER,
    # ),
    # CIWorkflow(
    #     arch="linux",
    #     build_environment="linux-xenial-py3.6-clang7-onnx",
    #     docker_image_base=f"{DOCKER_REGISTRY}/pytorch/pytorch-linux-xenial-py3-clang7-onnx",
    #     test_runner_type=LINUX_CPU_TEST_RUNNER,
    # ),
    CIWorkflow(
        arch="linux",
        build_environment="linux-bionic-cuda10.2-cudnn7-py3.9-gcc7",
        docker_image_base=f"{DOCKER_REGISTRY}/pytorch/pytorch-linux-bionic-cuda10.2-cudnn7-py3.9-gcc7",
        test_runner_type=LINUX_CUDA_TEST_RUNNER,
        num_test_shards=2,
    ),
    CIWorkflow(
        arch="linux",
        build_environment="linux-xenial-cuda10.2-cudnn7-py3.6-gcc7",
        docker_image_base=f"{DOCKER_REGISTRY}/pytorch/pytorch-linux-xenial-cuda10.2-cudnn7-py3-gcc7",
        test_runner_type=LINUX_CUDA_TEST_RUNNER,
        enable_jit_legacy_test=1,
        enable_multigpu_test=1,
        enable_nogpu_no_avx_test=1,
        enable_nogpu_no_avx2_test=1,
        enable_slow_test=1,
        num_test_shards=2,
        on_pull_request=True,
        ciflow_config=CIFlowConfig(
            enabled=True,
            trigger_action_only=True,
            labels=set(['ciflow/slow']),
        ),
    ),
    CIWorkflow(
        arch="linux",
        build_environment="libtorch-linux-xenial-cuda10.2-cudnn7-py3.6-gcc7",
        docker_image_base=f"{DOCKER_REGISTRY}/pytorch/pytorch-linux-xenial-cuda10.2-cudnn7-py3-gcc7",
        test_runner_type=LINUX_CUDA_TEST_RUNNER,
        is_libtorch=True,
    ),
    CIWorkflow(
        arch="linux",
        build_environment="linux-xenial-cuda11.1-cudnn8-py3.6-gcc7",
        docker_image_base=f"{DOCKER_REGISTRY}/pytorch/pytorch-linux-xenial-cuda11.1-cudnn8-py3-gcc7",
        test_runner_type=LINUX_CUDA_TEST_RUNNER,
        num_test_shards=2,
    ),
    CIWorkflow(
        arch="linux",
        build_environment="libtorch-linux-xenial-cuda11.1-cudnn8-py3.6-gcc7",
        docker_image_base=f"{DOCKER_REGISTRY}/pytorch/pytorch-linux-xenial-cuda11.1-cudnn8-py3-gcc7",
        test_runner_type=LINUX_CUDA_TEST_RUNNER,
        is_libtorch=True,
    ),
    CIWorkflow(
        arch="linux",
        build_environment="linux-xenial-cuda11.3-cudnn8-py3.6-gcc7",
        docker_image_base=f"{DOCKER_REGISTRY}/pytorch/pytorch-linux-xenial-cuda11.3-cudnn8-py3-gcc7",
        test_runner_type=LINUX_CUDA_TEST_RUNNER,
        num_test_shards=2,
        is_scheduled="45 0,4,8,12,16,20 * * *",
    ),
    CIWorkflow(
        arch="linux",
        build_environment="libtorch-linux-xenial-cuda11.3-cudnn8-py3.6-gcc7",
        docker_image_base=f"{DOCKER_REGISTRY}/pytorch/pytorch-linux-xenial-cuda11.3-cudnn8-py3-gcc7",
        test_runner_type=LINUX_CUDA_TEST_RUNNER,
        is_libtorch=True,
        is_scheduled="45 0,4,8,12,16,20 * * *",
    ),
    # CIWorkflow(
    #     arch="linux",
    #     build_environment="linux-bionic-py3.6-clang9-noarch",
    #     docker_image_base=f"{DOCKER_REGISTRY}/pytorch/pytorch-linux-bionic-py3.6-clang9",
    #     test_runner_type=LINUX_CPU_TEST_RUNNER,
    # ),
    # CIWorkflow(
    #     arch="linux",
    #     build_environment="xla-linux-bionic-py3.6-clang9",
    #     docker_image_base=f"{DOCKER_REGISTRY}/pytorch/pytorch-linux-bionic-py3.6-clang9",
    #     test_runner_type=LINUX_CPU_TEST_RUNNER,
    # ),
    # CIWorkflow(
    #     arch="linux",
    #     build_environment="vulkan-linux-bionic-py3.6-clang9",
    #     docker_image_base=f"{DOCKER_REGISTRY}/pytorch/pytorch-linux-bionic-py3.6-clang9",
    #     test_runner_type=LINUX_CPU_TEST_RUNNER,
    # ),
    CIWorkflow(
        arch="linux",
        build_environment="linux-bionic-py3.8-gcc9-coverage",
        docker_image_base=f"{DOCKER_REGISTRY}/pytorch/pytorch-linux-bionic-py3.8-gcc9",
        test_runner_type=LINUX_CPU_TEST_RUNNER,
        on_pull_request=True,
        num_test_shards=2,
        ciflow_config=CIFlowConfig(
            enabled=True,
            labels=set(['ciflow/default']),
        ),
    ),
    # CIWorkflow(
    #     arch="linux",
    #     build_environment="linux-bionic-rocm3.9-py3.6",
    #     docker_image_base=f"{DOCKER_REGISTRY}/pytorch/pytorch-linux-bionic-rocm3.9-py3.6",
    #     test_runner_type=LINUX_CPU_TEST_RUNNER,
    # ),
    # CIWorkflow(
    #     arch="linux",
    #     build_environment="linux-xenial-py3.6-clang5-android-ndk-r19c-x86_32",
    #     docker_image_base=f"{DOCKER_REGISTRY}/pytorch/pytorch-linux-xenial-py3-clang5-android-ndk-r19c",
    #     test_runner_type=LINUX_CPU_TEST_RUNNER,
    # ),
    # CIWorkflow(
    #     arch="linux",
    #     build_environment="linux-xenial-py3.6-clang5-android-ndk-r19c-x86_64",
    #     docker_image_base=f"{DOCKER_REGISTRY}/pytorch/pytorch-linux-xenial-py3-clang5-android-ndk-r19c",
    #     test_runner_type=LINUX_CPU_TEST_RUNNER,
    # ),
    # CIWorkflow(
    #     arch="linux",
    #     build_environment="linux-xenial-py3.6-clang5-android-ndk-r19c-arm-v7a",
    #     docker_image_base=f"{DOCKER_REGISTRY}/pytorch/pytorch-linux-xenial-py3-clang5-android-ndk-r19c",
    #     test_runner_type=LINUX_CPU_TEST_RUNNER,
    # ),
    # CIWorkflow(
    #     arch="linux",
    #     build_environment="linux-xenial-py3.6-clang5-android-ndk-r19c-arm-v8a",
    #     docker_image_base=f"{DOCKER_REGISTRY}/pytorch/pytorch-linux-xenial-py3-clang5-android-ndk-r19c",
    #     test_runner_type=LINUX_CPU_TEST_RUNNER,
    # ),
    # CIWorkflow(
    #     arch="linux",
    #     build_environment="linux-xenial-py3.6-clang5-mobile",
    #     docker_image_base=f"{DOCKER_REGISTRY}/pytorch/pytorch-linux-xenial-py3-clang5-asan",
    #     test_runner_type=LINUX_CPU_TEST_RUNNER,
    # ),
    # CIWorkflow(
    #     arch="linux",
    #     build_environment="linux-xenial-py3.6-clang5-mobile-custom-dynamic",
    #     docker_image_base=f"{DOCKER_REGISTRY}/pytorch/pytorch-linux-xenial-py3-clang5-android-ndk-r19c",
    #     test_runner_type=LINUX_CPU_TEST_RUNNER,
    # ),
    # CIWorkflow(
    #     arch="linux",
    #     build_environment="linux-xenial-py3.6-clang5-mobile-custom-static",
    #     docker_image_base=f"{DOCKER_REGISTRY}/pytorch/pytorch-linux-xenial-py3-clang5-android-ndk-r19c",
    #     test_runner_type=LINUX_CPU_TEST_RUNNER,
    # ),
    # CIWorkflow(
    #     arch="linux",
    #     build_environment="linux-xenial-py3.6-clang5-mobile-code-analysis",
    #     docker_image_base=f"{DOCKER_REGISTRY}/pytorch/pytorch-linux-xenial-py3-clang5-android-ndk-r19c",
    #     test_runner_type=LINUX_CPU_TEST_RUNNER,
    # ),
]


BAZEL_WORKFLOWS = [
    CIWorkflow(
        arch="linux",
        build_environment="linux-xenial-py3.6-gcc7-bazel-test",
        docker_image_base=f"{DOCKER_REGISTRY}/pytorch/pytorch-linux-xenial-py3.6-gcc7",
        test_runner_type=LINUX_CPU_TEST_RUNNER,
        on_pull_request=True,
        ciflow_config=CIFlowConfig(
            enabled=True,
            trigger_action_only=True,
            labels=set(['ciflow/default']),
        ),
    ),
]

if __name__ == "__main__":
    jinja_env = jinja2.Environment(
        variable_start_string="!{{",
        loader=jinja2.FileSystemLoader(str(GITHUB_DIR.joinpath("templates"))),
    )
    template_and_workflows = [
        (jinja_env.get_template("linux_ci_workflow.yml.j2"), LINUX_WORKFLOWS),
        (jinja_env.get_template("windows_ci_workflow.yml.j2"), WINDOWS_WORKFLOWS),
        (jinja_env.get_template("bazel_ci_workflow.yml.j2"), BAZEL_WORKFLOWS),
    ]
    for template, workflows in template_and_workflows:
        for workflow in workflows:
            workflow.generate_workflow_file(workflow_template=template)
