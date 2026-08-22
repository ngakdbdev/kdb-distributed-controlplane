cmake_minimum_required(VERSION 3.16)
project(feedhandler-cpp CXX)

set(CMAKE_CXX_STANDARD 17)
set(CMAKE_CXX_STANDARD_REQUIRED ON)
if(NOT CMAKE_BUILD_TYPE)
  set(CMAKE_BUILD_TYPE Release)
endif()
add_compile_options(-Wall -Wextra -Wno-unused-parameter)

find_package(Threads REQUIRED)

set(CORE_SOURCES
  core/Event.cpp
  core/Transport.cpp
  core/Json.cpp
  core/FeedRegistry.cpp
  core/ConfigLoader.cpp
  core/StatusServer.cpp
  recovery/RecoveryManager.cpp
)

set(TRANSPORT_SOURCES
  transport/UdpTransport.cpp
  transport/TcpTransport.cpp
  transport/WebSocketTransport.cpp
)

set(PROTOCOL_SOURCES
  protocols/moldudp64/MoldUdp64Decoder.cpp
  protocols/soupbintcp/SoupBinTcpDecoder.cpp
  protocols/sbe/SbeDecoder.cpp
  protocols/sbe/SequencedSbePacketDecoder.cpp
  protocols/fix/FixMessage.cpp
  protocols/fix/FixDecoder.cpp
  protocols/wsjson/WsJsonDecoder.cpp
)

set(VENUE_SOURCES
  venues/NasdaqItchVenueAdapter.cpp
  venues/CmeMdp3VenueAdapter.cpp
  venues/GenericFixVenueAdapter.cpp
  venues/GenericWsJsonVenueAdapter.cpp
)

set(PUBLISHER_SOURCES
  publishers/KdbIpc.cpp
  publishers/KdbPublisher.cpp
)

set(ENGINE_SOURCES
  ${CORE_SOURCES}
  ${TRANSPORT_SOURCES}
  ${PROTOCOL_SOURCES}
  ${VENUE_SOURCES}
  ${PUBLISHER_SOURCES}
)

add_library(feedhandler_core STATIC ${ENGINE_SOURCES})
target_link_libraries(feedhandler_core PUBLIC Threads::Threads)
target_include_directories(feedhandler_core PUBLIC ${CMAKE_CURRENT_SOURCE_DIR})

# ---------------------------------------------------------------- unit tests
enable_testing()
file(GLOB TEST_SOURCES tests/*.cpp)
if(TEST_SOURCES)
  add_executable(feedhandler_tests ${TEST_SOURCES})
  target_link_libraries(feedhandler_tests PRIVATE feedhandler_core)
  add_test(NAME feedhandler_tests COMMAND feedhandler_tests)
endif()

# ---------------------------------------------------------------- simulators
file(GLOB SIM_SOURCES sim/*.cpp)
if(SIM_SOURCES)
  add_library(feedhandler_sim STATIC ${SIM_SOURCES})
  target_link_libraries(feedhandler_sim PUBLIC feedhandler_core)
endif()

# ---------------------------------------------------------------- main engine binary
if(EXISTS ${CMAKE_CURRENT_SOURCE_DIR}/main.cpp)
  add_executable(feedhandler main.cpp)
  target_link_libraries(feedhandler PRIVATE feedhandler_core feedhandler_sim)
endif()
