uint8 BUFFERING=0
uint8 PLAYING=1
uint8 DRAINED=2
uint8 FAULT=3
uint8 CANCELLED=4

StreamHeader header
string<=32 schema_version
uint8 state
uint64 submitted_samples
uint64 played_samples
bool response_final_seen
bool drained
string<=256 error
