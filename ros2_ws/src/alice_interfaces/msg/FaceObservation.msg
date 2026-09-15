uint8 VALID=0
uint8 NO_FACE=1

StreamHeader header
string<=40 schema_version
builtin_interfaces/Time captured_at
bool has_observed_at
builtin_interfaces/Time observed_at
string<=128 camera_id
string<=128 detector
string<=64 detector_model_sha256
uint32 image_width
uint32 image_height
bool has_face_confidence
float64 face_confidence
uint8 validity
string<=256 invalid_reason
BlendshapeScore[<=64] scores
