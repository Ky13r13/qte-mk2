cmake_minimum_required(VERSION 3.20)

project(qte VERSION 0.1.0 LANGUAGES CXX)

option(QTE_BUILD_TESTS "Build qte unit tests" ON)
option(QTE_BUILD_PYTHON "Build the qte Python extension" OFF)

add_library(qte_core
    src/analytics/performance.cpp
    src/config/research_profile.cpp
    src/core/currency.cpp
    src/core/price.cpp
    src/core/quantity.cpp
    src/execution/open_only.cpp
    src/engine/backtest.cpp
    src/market_data/bar.cpp
    src/market_data/dataset.cpp
    src/market_data/schedule.cpp
    src/orders/fill.cpp
    src/orders/order.cpp
    src/portfolio/portfolio.cpp
    src/portfolio/position.cpp
    src/portfolio/trade.cpp
    src/risk/risk.cpp
    src/strategy/strategy.cpp
)

add_library(qte::core ALIAS qte_core)

target_compile_features(qte_core PUBLIC cxx_std_20)
target_include_directories(qte_core
    PUBLIC
        $<BUILD_INTERFACE:${CMAKE_CURRENT_SOURCE_DIR}/include>
        $<INSTALL_INTERFACE:include>
)
set_target_properties(qte_core PROPERTIES POSITION_INDEPENDENT_CODE ON)

if(MSVC)
    target_compile_options(qte_core PRIVATE /W4 /permissive-)
else()
    target_compile_options(qte_core PRIVATE -Wall -Wextra -Wpedantic)
endif()

if(QTE_BUILD_TESTS)
    enable_testing()
    add_subdirectory(tests/cpp)
endif()

if(QTE_BUILD_PYTHON)
    find_package(Python REQUIRED COMPONENTS Interpreter Development.Module)
    find_package(pybind11 CONFIG REQUIRED)
    pybind11_add_module(_core MODULE bindings/module.cpp)
    target_link_libraries(_core PRIVATE qte::core)
    target_compile_features(_core PRIVATE cxx_std_20)
    set_target_properties(_core PROPERTIES
        LIBRARY_OUTPUT_DIRECTORY "${CMAKE_BINARY_DIR}/python/qte")
    file(MAKE_DIRECTORY "${CMAKE_BINARY_DIR}/python/qte")
    file(COPY "${CMAKE_CURRENT_SOURCE_DIR}/python/qte"
         DESTINATION "${CMAKE_BINARY_DIR}/python")
    install(TARGETS _core LIBRARY DESTINATION qte)
endif()
