cmake_minimum_required(VERSION 3.10)
project(alice_interfaces)

find_package(ament_cmake REQUIRED)
find_package(builtin_interfaces REQUIRED)
find_package(rosidl_default_generators REQUIRED)

rosidl_generate_interfaces(${PROJECT_NAME}
  "msg/RunIdentity.msg"
  "msg/StreamHeader.msg"
  "msg/SpeechClause.msg"
  "msg/PcmChunk.msg"
  "msg/PcmCredit.msg"
  "msg/SpeechState.msg"
  "msg/ActuatorTarget.msg"
  "msg/ExpressionFrame.msg"
  "msg/FaceTarget.msg"
  "msg/RunHealth.msg"
  "msg/PlaybackStatus.msg"
  "msg/PeerIdentity.msg"
  "msg/ServoChannelReceipt.msg"
  "msg/ServoReceipt.msg"
  "msg/BlendshapeScore.msg"
  "msg/FaceObservation.msg"
  "srv/BeginRun.srv"
  "srv/EndRun.srv"
  "action/RunSpeech.action"
  DEPENDENCIES builtin_interfaces
)

ament_export_dependencies(rosidl_default_runtime)
ament_package()
