add_executable(qte_bar_tests test_bar.cpp)
target_link_libraries(qte_bar_tests PRIVATE qte::core)
target_compile_features(qte_bar_tests PRIVATE cxx_std_20)

add_test(NAME qte.bar COMMAND qte_bar_tests)

add_executable(qte_domain_types_tests test_domain_types.cpp)
target_link_libraries(qte_domain_types_tests PRIVATE qte::core)
target_compile_features(qte_domain_types_tests PRIVATE cxx_std_20)

add_test(NAME qte.domain_types COMMAND qte_domain_types_tests)

add_executable(qte_price_instrument_tests test_price_instrument.cpp)
target_link_libraries(qte_price_instrument_tests PRIVATE qte::core)
target_compile_features(qte_price_instrument_tests PRIVATE cxx_std_20)

add_test(NAME qte.price_instrument COMMAND qte_price_instrument_tests)

add_executable(qte_order_tests test_order.cpp)
target_link_libraries(qte_order_tests PRIVATE qte::core)
target_compile_features(qte_order_tests PRIVATE cxx_std_20)

add_test(NAME qte.order COMMAND qte_order_tests)

add_executable(qte_fill_tests test_fill.cpp)
target_link_libraries(qte_fill_tests PRIVATE qte::core)
target_compile_features(qte_fill_tests PRIVATE cxx_std_20)

add_test(NAME qte.fill COMMAND qte_fill_tests)

add_executable(qte_open_only_execution_tests test_open_only_execution.cpp)
target_link_libraries(qte_open_only_execution_tests PRIVATE qte::core)
target_compile_features(qte_open_only_execution_tests PRIVATE cxx_std_20)

add_test(NAME qte.open_only_execution COMMAND qte_open_only_execution_tests)

add_executable(qte_position_tests test_position.cpp)
target_link_libraries(qte_position_tests PRIVATE qte::core)
target_compile_features(qte_position_tests PRIVATE cxx_std_20)

add_test(NAME qte.position COMMAND qte_position_tests)

add_executable(qte_portfolio_tests test_portfolio.cpp)
target_link_libraries(qte_portfolio_tests PRIVATE qte::core)
target_compile_features(qte_portfolio_tests PRIVATE cxx_std_20)

add_test(NAME qte.portfolio COMMAND qte_portfolio_tests)

add_executable(qte_trade_tests test_trade.cpp)
target_link_libraries(qte_trade_tests PRIVATE qte::core)
target_compile_features(qte_trade_tests PRIVATE cxx_std_20)

add_test(NAME qte.trade COMMAND qte_trade_tests)

add_executable(qte_risk_tests test_risk.cpp)
target_link_libraries(qte_risk_tests PRIVATE qte::core)
target_compile_features(qte_risk_tests PRIVATE cxx_std_20)

add_test(NAME qte.risk COMMAND qte_risk_tests)

add_executable(qte_strategy_tests test_strategy.cpp)
target_link_libraries(qte_strategy_tests PRIVATE qte::core)
target_compile_features(qte_strategy_tests PRIVATE cxx_std_20)

add_test(NAME qte.strategy COMMAND qte_strategy_tests)

add_executable(qte_dataset_tests test_dataset.cpp)
target_link_libraries(qte_dataset_tests PRIVATE qte::core)
target_compile_features(qte_dataset_tests PRIVATE cxx_std_20)

add_test(NAME qte.dataset COMMAND qte_dataset_tests)

add_executable(qte_schedule_tests test_schedule.cpp)
target_link_libraries(qte_schedule_tests PRIVATE qte::core)
target_compile_features(qte_schedule_tests PRIVATE cxx_std_20)

add_test(NAME qte.schedule COMMAND qte_schedule_tests)

add_executable(qte_backtest_tests test_backtest.cpp)
target_link_libraries(qte_backtest_tests PRIVATE qte::core)
target_compile_features(qte_backtest_tests PRIVATE cxx_std_20)

add_test(NAME qte.backtest COMMAND qte_backtest_tests)

add_executable(qte_analytics_tests test_analytics.cpp)
target_link_libraries(qte_analytics_tests PRIVATE qte::core)
target_compile_features(qte_analytics_tests PRIVATE cxx_std_20)

add_test(NAME qte.analytics COMMAND qte_analytics_tests)
