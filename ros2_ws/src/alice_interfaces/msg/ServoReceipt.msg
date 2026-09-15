uint8 APPLIED=0
uint8 REJECTED=1
uint8 FAULT=2

StreamHeader header
string<=32 schema_version
string<=128 request_id
string<=64 hardware_id
string<=64 calibration_sha256
uint8 state
ServoChannelReceipt[<=6] channels
string<=128 fault_code
string<=256 detail
