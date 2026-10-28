cmake_minimum_required(VERSION 3.10)
project(mrs_msgs)

# Interface-only package: no C++ is written here, CMake just generates the
# Python (and C++) bindings for the .msg files.
find_package(ament_cmake REQUIRED)
find_package(rosidl_default_generators REQUIRED)
find_package(builtin_interfaces REQUIRED)
find_package(geometry_msgs REQUIRED)

rosidl_generate_interfaces(${PROJECT_NAME}
  "msg/PerceivedEntity.msg"
  "msg/WorldState.msg"
  "msg/TeamState.msg"
  "msg/DialogueTurn.msg"
  "msg/ProposedPlan.msg"
  "msg/EthicalVerdict.msg"
  "msg/ValidatedPlan.msg"
  "msg/TaskStatus.msg"
  "msg/SafetyOverride.msg"
  "msg/EpisodeEvent.msg"
  DEPENDENCIES builtin_interfaces geometry_msgs
)

ament_export_dependencies(rosidl_default_runtime)
ament_package()
