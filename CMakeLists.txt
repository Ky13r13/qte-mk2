cmake_minimum_required(VERSION 3.20)

project(qte VERSION 0.1.0 LANGUAGES CXX)

option(QTE_BUILD_TESTS "Build qte unit tests" ON)

add_library(qte_core
    src/config/research_profile.cpp
    src/core/currency.cpp
    src/core/price.cpp
    src/core/quantity.cpp
    src/execution/open_only.cpp
    src/market_data/bar.cpp
    src/orders/fill.cpp
    src/orders/order.cpp
    src/portfolio/portfolio.cpp
    src/portfolio/position.cpp
    src/portfolio/trade.cpp
)

add_library(qte::core ALIAS qte_core)

target_compile_features(qte_core PUBLIC cxx_std_20)
target_include_directories(qte_core
    PUBLIC
        $<BUILD_INTERFACE:${CMAKE_CURRENT_SOURCE_DIR}/include>
        $<INSTALL_INTERFACE:include>
)

if(MSVC)
    target_compile_options(qte_core PRIVATE /W4 /permissive-)
else()
    target_compile_options(qte_core PRIVATE -Wall -Wextra -Wpedantic)
endif()

if(QTE_BUILD_TESTS)
    enable_testing()
    add_subdirectory(tests/cpp)
endif()
