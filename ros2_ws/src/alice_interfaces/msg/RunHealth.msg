uint8 PREPARING=0
uint8 READY=1
uint8 ACTIVE=2
uint8 FAULT=3
uint8 CANCELLED=4
uint8 FINALIZING=5
uint8 COMPLETED=6

StreamHeader header
string<=32 schema_version
uint8 state
uint64 progress
string<=256 detail
