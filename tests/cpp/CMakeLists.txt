add_executable(qte_bar_tests test_bar.cpp)
target_link_libraries(qte_bar_tests PRIVATE qte::core)
target_compile_features(qte_bar_tests PRIVATE cxx_std_20)

add_test(NAME qte.bar COMMAND qte_bar_tests)

add_executable(qte_domain_types_tests test_domain_types.cpp)
target_link_libraries(qte_domain_types_tests PRIVATE qte::core)
target_compile_features(qte_domain_types_tests PRIVATE cxx_std_20)

add_test(NAME qte.domain_types COMMAND qte_domain_types_tests)
