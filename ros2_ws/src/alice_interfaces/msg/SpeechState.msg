uint8 BUFFERING=0
uint8 PLAYING=1
uint8 DRAINED=2
uint8 FAULT=3

StreamHeader header
string<=32 schema_version
uint64 source_monotonic_ns
uint64 played_sample
uint32 sample_rate
float64 envelope_aperture
float64 speech_weight
float64[3] audible_vector
float64 audible_intensity
uint8 phase
string<=128 owner_incarnation
