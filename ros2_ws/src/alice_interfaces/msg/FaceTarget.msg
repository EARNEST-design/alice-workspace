StreamHeader header
string<=32 schema_version
string<=64 config_sha256
string<=64 calibration_sha256
uint64 speech_sequence
uint64 played_sample
uint64 speech_source_monotonic_ns
ActuatorTarget[<=11] targets
