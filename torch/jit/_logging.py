import torch

add_stat_value = torch._C._logging_add_stat_value
get_counter_val = torch._C._logging_get_counter_val

set_logger = torch._C._logging_set_logger
LockingLogger = torch._C.LockingLogger
AggregationType = torch._C.AggregationType
NoopLogger = torch._C.NoopLogger

time_point = torch._C._logging_time_point
record_duration_since = torch._C._logging_record_duration_since
