//  Copyright © 2022 Apple Inc.

#pragma once

#include <ATen/Tensor.h>
#include <ATen/mps/MPSStream.h>
#include <ATen/mps/MPSAllocatorInterface.h>

#include <os/signpost.h>
#include <os/log.h>

#include <sstream>
#include <string>
#include <atomic>
#include <unordered_map>
#include <utility>
#include <ctime>

namespace at::mps {

namespace Profiler {

struct BaseInfo {
  // profiling info types
  enum class Type {
    GRAPH,
    KERNEL,
    COPY,
    CPU_FALLBACK,
  };

  BaseInfo(Type infoType, uint64_t Id, const uintptr_t Handle) :
      type(infoType), profileId(Id), handle(Handle) { }
  virtual ~BaseInfo() = default;

  // type of profiling info
  Type type;
  // unique profile ID for execution instances of operations or copies
  uint64_t profileId;
  // ID generated by os_signpost
  // since it's possible to use event and interval-based signposts at the
  // same time, we need separate IDs for each.
  os_signpost_id_t eventSignpostId = 0, intervalSignpostId = 0;
  // accumulated GPU time in ms (obtained from CompletionHandler's "GPUEndTime - GPUStartTime")
  std::atomic<double> totalGpuTime{0.0};
  // accumulated Scheduling time in ms (obtained from CompletionHandler's "KernelEndTime - KernelStartTime")
  std::atomic<double> totalSchedulingTime{0.0};
  // indicates if the operation or copy execution has completed
  std::atomic_bool completed{false};
  // handle used to identify the profile info's instance (usually the pointer)
  const uintptr_t handle;

  virtual const std::string toString(double gpuTime = 0, double schedulingTime = 0) const;
  // builds a string for a tensor (format: Device:ScalarType[tensor.sizes()])
  static std::string buildTensorString(const Tensor& tensor, bool includeBufferId = false) {
    if (tensor.defined()) {
      std::stringstream tensorStr;
      auto deviceType = tensor.device().type();
      tensorStr << c10::DeviceTypeName(deviceType);
      // see comments for INCLUDE_BUFFER_ID
      if (includeBufferId && deviceType == at::kMPS) {
        id<MTLBuffer> buffer = __builtin_bit_cast(id<MTLBuffer>, tensor.storage().data());
        tensorStr << "(buf#" << (getIMPSAllocator()->getBufferId(buffer))
                  << ":" << buffer.retainCount << ")";
      }
      tensorStr << ":"
                << tensor.scalar_type() << tensor.sizes();
      return tensorStr.str();
    } else {
      return "undefined";
    }
  }
  static uint64_t getTime() {
    return clock_gettime_nsec_np(CLOCK_MONOTONIC_RAW);
  }
};

struct OperationInfo : BaseInfo {
  OperationInfo(const void* Handle, bool IsGraph, uint64_t Id, const std::string& StrKey) :
      BaseInfo(IsGraph ? Type::GRAPH : Type::KERNEL, Id, uintptr_t(Handle)), strKey(StrKey) { }

  uint64_t runCount = 0;
  std::string strKey;

  const std::string toString(double gpuTime = 0, double schedulingTime = 0) const override;

  // builds a string for a kernel
  static std::string buildKernelString(const std::string& kernelName,
                                       const TensorList& tensors,
                                       bool includeBufferId = false) {
    std::stringstream kernelStr;
    kernelStr << kernelName;
    for (const Tensor& tensor: tensors) {
      kernelStr << ":" << BaseInfo::buildTensorString(tensor, includeBufferId);
    }
    return kernelStr.str();
  }
};

struct CpuFbInfo : BaseInfo {
  CpuFbInfo(uint64_t Id, const std::string& OpName) :
      BaseInfo(Type::CPU_FALLBACK, Id, 0), opName(OpName) { }

  uint64_t runCount = 0;
  // the current and total overhead of copies in bytes required to convert the Op's
  // input tensors from MPS to CPU and then output from CPU back to MPS
  size_t currentCopyOverhead = 0;
  size_t totalCopyOverhead = 0;
  std::string opName;
  std::string strKey;
  uint64_t startTime = 0;

  const std::string toString(double gpuTime = 0, double schedulingTime = 0) const override;

  void updateCopyOverhead(const TensorList& tensors) {
    currentCopyOverhead = 0;
    for (const Tensor& tensor: tensors) {
      if (tensor.defined()) {
        currentCopyOverhead += tensor.nbytes();
      }
    }
    totalCopyOverhead += currentCopyOverhead;
  }
};

struct CopyInfo : BaseInfo {
  enum class Kind {
    MPS_TO_MPS,
    MPS_TO_CPU,
    CPU_TO_MPS,
  };

  CopyInfo(const void* Handle, size_t Length, uint64_t Id, bool IsNonBlocking, bool UsesBlitter) :
           BaseInfo(Type::COPY, Id, uintptr_t(Handle)), kind(Kind::MPS_TO_MPS),
           length(Length), isNonBlocking(IsNonBlocking), usesBlitter(UsesBlitter) { }

  Kind kind;
  size_t length;
  bool isNonBlocking;
  bool usesBlitter;
  std::string srcStrKey;
  std::string dstStrKey;
  // for copies that don't use blitters, we measure CPU time
  uint64_t startTime = 0;

  const std::string toString(double gpuTime = 0, double schedulingTime = 0) const override;

  static std::string buildTensorString(const void* buffer, const OptionalTensorRef tensor, bool includeBufferId = false);

  static bool isStorageOnMPS(const void* buffer, const OptionalTensorRef tensor) {
    if (tensor.has_value()) {
      return tensor->device().type() == at::kMPS;
    }
    TORCH_INTERNAL_ASSERT_DEBUG_ONLY(buffer);
    // getUnalignedBufferSize() returns -1 if input buffer is not on MPS device
    return getIMPSAllocator()->getUnalignedBufferSize(buffer) >= 0;
  }

  static Kind getCopyKind(const void* srcBuffer, const void* dstBuffer,
                          const OptionalTensorRef srcTensor, const OptionalTensorRef dstTensor) {
    const bool isSrcOnMPS = isStorageOnMPS(srcBuffer, srcTensor);
    const bool isDstOnMPS = isStorageOnMPS(dstBuffer, dstTensor);
    TORCH_INTERNAL_ASSERT_DEBUG_ONLY(isSrcOnMPS || isDstOnMPS);
    if (isSrcOnMPS && !isDstOnMPS) {
      return Kind::MPS_TO_CPU;
    } else if (!isSrcOnMPS && isDstOnMPS) {
      return Kind::CPU_TO_MPS;
    }
    return Kind::MPS_TO_MPS;
  }
};

struct CopyStat : CopyInfo {
  explicit CopyStat(std::string CopyKindStr) :
          CopyInfo(nullptr, 0, 0, false, false), kindStr(std::move(CopyKindStr)) {}
  // total number of copies
  size_t totalCount = 0;
  // number of Scalar copies (i.e., less than sizeof(int64))
  size_t scalarsCount = 0;
  // number of blocking copies (i.e., require syncing to GPU)
  size_t blockingCount = 0;
  // number of copies that used memcpy(), instead of Metal Blit Encoder
  size_t memcpyCount = 0;
  // accumulated GPU time in ms for the scalar copies
  std::atomic<double> scalarsGpuTime{0.0};
  // copy kind in string type
  std::string kindStr;
};

class MPSProfiler {
public:
  // lower 16 bits used for profiler options
  enum ProfileOptions : uint32_t {
    OPTIONS_NONE = 0,
    // ALL_* means, all signpost types (RUN_OPERATION|BLIT_COPY|CPU_FALLBACK, etc.)
    // (used for convenience to not compute bit flags by OR-ing manually)
    // trace all signpost types using events
    ALL_SIGNPOST_EVENTS    = (1 << 0),
    // trace all signpost types using intervals
    ALL_SIGNPOST_INTERVALS = (1 << 1),
    // always wait for command buffer to finish executing after each commit
    WAIT_UNTIL_COMPLETED   = (1 << 2),
    // for interval-based signposts, include the scheduling portion of
    // Graph/Kernel/Copy executions as well.
    // if flag is disable, only "GPU run time" is included in interval,
    // and not schedule time.
    INCLUDE_SCHEDULE_INTERVAL = (1 << 3),

    // use these if you need to trace signposts types individually (rarely required)
    // trace signpost using intervals
    USE_INTERVALS = (1 << 4),
    // trace signpost by emitting events
    USE_EVENTS    = (1 << 5),
    // used for sanity check (Change this when new option added)
    OPTIONS_COUNT = (USE_EVENTS << 1) - 1,
  };

  // when adding new types, #define the type string in MPSProfiler.mm as well.
  // upper 16 bits used for event types
  enum SignpostTypes : uint32_t {
    SIGNPOST_NONE = 0,
    // trace signposts for PyTorch operation executions
    RUN_OPERATION = (1 << 16),
    // trace signposts for blitter copies
    BLIT_COPY     = (1 << 17),
    // trace signposts for ops that fall back on CPU
    CPU_FALLBACK  = (1 << 18),
    // used for sanity check (Change this when new type added)
    SIGNPOST_COUNT = (CPU_FALLBACK << 1) - 1,
  };

  enum LogOptions : uint32_t {
    LOG_NONE = 0,

    // Info logging options during execution
    // -------------------------------------
    // prints operation info (id/key/run_count) during execution
    OPERATION_INFO      = (1 << 0),
    // prints copy info (src/dst tensors/buffers, size, etc.) during execution
    COPY_INFO           = (1 << 1),
    // prints CPU Fallback info (id/runCount/opName/copyOverhead) during execution
    CPU_FALLBACK_INFO   = (1 << 2),

    // Profiling Statistics logging options when process terminates
    // ------------------------------------------------------------
    // prints all stats (OPERATION_STATS, COPY_STATS, CPU_FALLBACK_STATS) before process terminates
    // this is convenient to not combine following stats bit flags manually
    ALL_STATS           = (1 << 3),
    // prints operation stats (GPU times, run count, etc.) before process terminates
    OPERATION_STATS     = (1 << 4),
    // prints copies stats (GPU times, copy kinds, sizes, etc.) before process terminates
    COPY_STATS          = (1 << 5),
    // prints CPU Fallback stats (CPU times, run times, size of MPS<->CPU copies
    // for tensors, etc.) before process terminates
    CPU_FALLBACK_STATS  = (1 << 6),

    // Metadata format options when logging the info
    // ---------------------------------------------
    // if enabled, includes GPU run time in metadata (i.e., GPUEndTime-GPUStartTime
    // from Metal Command Buffers) (e.g., [GPU=0.324 ms])
    INCLUDE_GPU_TIME    = (1 << 7),
    // if enabled, includes GPU scheduling time in metadata separately
    // (i.e., KernelEndTime-KernelStartTime from Metal Command Buffers)
    // e.g., [GPU=0.324 ms, KRNL=0.036 ms]
    INCLUDE_KERNEL_TIME = (1 << 8),
    // if enabled, includes the unique buffer ID in metadata for the storage
    // of a tensor that was allocated on MPSAllocator. This is useful (along with
    // the EV "PYTORCH_DEBUG_MPS_ALLOCATOR") to identify buffers that are involved
    // with various operations.
    INCLUDE_BUFFER_ID   = (1 << 9),

    // used for sanity check (Change this when new option added)
    LOG_COUNT = (INCLUDE_BUFFER_ID << 1) - 1,
  };

  explicit MPSProfiler();
  ~MPSProfiler();

  // the handle is either "MPSGraph*" or "id<MTLComputePipelineState>" for Metal Kernels
  // the beginProfile*() functions return a profileId which is unique per graph/kernel/copy
  uint64_t beginProfileKernel(const void* handle, const std::string& strKey, bool isGraph);
  uint64_t beginProfileKernel(const void* handle, const std::string& kernelName, const TensorList& tensors);
  uint64_t beginProfileCopy(const void* srcBuffer, const void* dstBuffer,
                            const OptionalTensorRef srcTensor,
                            const OptionalTensorRef dstTensor,
                            size_t length, bool isNonBlocking, bool usesBlitter = true);
  uint64_t beginProfileCPUFallback(const std::string& opName, const TensorList& tensors);
  void beginProfileGPUInterval(const void* handle);

  void endProfileCopy(uint64_t profileId, SyncType syncType);
  void endProfileKernel(const void* handle, SyncType syncType = SyncType::NONE);
  void endProfileCPUFallback(const std::string& opName);

  // these are used to hook into Python bindings for torch.mps.profiler module.
  // this enables generating OS Signpost traces from MPSProfiler on-demand
  // during runtime (instead of environment variables).
  // The "mode" could be either "interval", "event", or both "interval,event"
  // for interval-based and/or event-based signpost tracing.
  void StartTrace(const string& mode, bool waitUntilCompleted);
  void StopTrace();

  // convenience functions to indicate whether signpost tracing or
  // logging are enabled for the SignpostTypes
  bool isOperationProfilingEnabled() const {
    return (m_signpost_types & SignpostTypes::RUN_OPERATION) ||
           (m_log_options & (LogOptions::OPERATION_INFO | LogOptions::OPERATION_STATS));
  }
  bool isCopyProfilingEnabled() const {
    return (m_signpost_types & SignpostTypes::BLIT_COPY) ||
           (m_log_options & (LogOptions::COPY_INFO | LogOptions::COPY_STATS));
  }
  bool isCPUFallbackProfilingEnabled() const {
    return (m_signpost_types & SignpostTypes::CPU_FALLBACK) ||
           (m_log_options & (LogOptions::CPU_FALLBACK_INFO | LogOptions::CPU_FALLBACK_STATS));
  }
  bool isSignpostTracingEnabled() const {
    return (m_signpost_types != SignpostTypes::SIGNPOST_NONE);
  }

 private:
  // indicates what type of signpost types are enabled and traced by MPS profiler.
  uint32_t m_signpost_types = 0;
  uint32_t m_profile_options = 0;
  uint32_t m_log_options = 0;
  uint64_t m_kernel_counter = 0;
  uint64_t m_graph_counter = 0;
  uint64_t m_cpu_fb_counter = 0;
  uint64_t m_copy_counter = 0;
  // technically, it's possible to trace both events and intervals at the same time
  // so we use separate os_log categories for them
  os_log_t m_os_log_events;
  os_log_t m_os_log_intervals;
  // stats logging could run either from destructor or signal handler
  // so this is used to check if logging has already started.
  std::atomic_bool hasLoggedStats{false};
  // indicates there are pending completionHandler callbacks that haven't been called yet.
  std::atomic_bool hasPendingCompletionHandlers{false};
  // used to capture sigint signal to log profiling stats
  static struct sigaction currentSigint, previousSigint;

  // We use the following lists for two reasons:
  // 1- for interval-based signposts the "begin" point won't be in same function
  // as the "end" point where we need to be able to retrieve signpost's info
  // 2- if Operations info need to be logged when process ends using LogOptions::OPERATION_INFO.

  // the pointer key for this map is either "MPSGraph*" or "id<MTLComputePipelineState>" for Metal Kernels
  // this list is retained and could be logged along with aggregate profiling numbers when the process ends.
  std::unordered_map<uintptr_t, std::unique_ptr<OperationInfo>> m_op_info_list{};
  // the string key for this map is the op name that we fall back to execute on CPU
  // this list is retained and could be logged along with aggregate profiling numbers when the process ends.
  std::unordered_map<std::string, std::unique_ptr<CpuFbInfo>> m_cpu_fb_info_list{};
  // this list contains the info for copies, and its key is the unique profileId
  // which is generated from m_copy_counter
  // The copyInfo list is not retained.
  std::unordered_map<uint64_t, std::unique_ptr<CopyInfo>> m_copy_info_list{};
  // a short list that contains copy stats
  std::unordered_map<CopyInfo::Kind, std::unique_ptr<CopyStat>> m_copy_stat_list{};

  void initialize();
  void beginProfileExecution(BaseInfo& info, bool cpuExecution = false);
  void endProfileExecution(BaseInfo& info, os_signpost_id_t event_signpost_id,
                           os_signpost_id_t interval_signpost_id,
                           double gpuTime, double schedulingTime);
  void addProfilerScheduledHandler(BaseInfo& info);
  void addProfilerCompletedHandler(BaseInfo& info, SyncType syncType);
  void emitSignpostEvent(SignpostTypes signpost_type, os_signpost_id_t signpost_id,
                         const std::string& msg) const;
  void beginSignpostInterval(SignpostTypes signpost_type, os_signpost_id_t signpost_id,
                             const std::string& msg) const;
  void endSignpostInterval(SignpostTypes signpost_type, os_signpost_id_t signpost_id) const;

  void updateCopyStats(const CopyInfo& copyInfo, double gpuTime, double schedulingTime);
  // returns true if logging the profiling info "during the execution" is enabled
  bool isProfileInfoLoggingEnabled(BaseInfo::Type infoType, bool isExecutionEnded);
  // logs all the profiling stats that are enabled
  void logProfilingStats();
  // logs kernel profiling stats when the process ends.
  void logOperationsProfilingStats(std::FILE* f) const;
  // logs CPU Fallback profiling stats when the process ends.
  void logCPUFallbackProfilingStats(std::FILE* f) const;
  // logs copy profiling stats when the process ends.
  void logCopyProfilingStats(std::FILE* f) const;

  os_signpost_id_t generateSignpostId(os_signpost_type_t signpostType, const void* ptr = nullptr);
  static SignpostTypes getSignpostType(BaseInfo::Type infoType);
  static void handleIntSignal(int signal);
};

} // namespace Profiler

Profiler::MPSProfiler& getMPSProfiler();

} // namespace at::mps
