#pragma once

#include <chrono>
#include <cstdint>
#include <stdexcept>
#include <string>
#include <string_view>
#include <vector>

namespace qte::market_data {

using Timestamp = std::chrono::sys_time<std::chrono::nanoseconds>;

inline constexpr std::uint32_t kBarSchemaVersion = 1;

class Symbol final {
public:
    explicit Symbol(std::string value);

    [[nodiscard]] const std::string& value() const noexcept { return value_; }

    friend bool operator==(const Symbol&, const Symbol&) = default;

private:
    std::string value_;
};

struct Bar final {
    Symbol symbol;
    Timestamp start_time;
    Timestamp end_time;
    double open;
    double high;
    double low;
    double close;
    double volume;
};

enum class BarValidationCode {
    invalid_interval,
    non_finite_price,
    negative_price,
    inconsistent_ohlc,
    non_finite_volume,
    negative_volume,
};

struct BarValidationError final {
    BarValidationCode code;
    std::string message;

    friend bool operator==(const BarValidationError&, const BarValidationError&) = default;
};

class InvalidBar final : public std::invalid_argument {
public:
    explicit InvalidBar(std::vector<BarValidationError> errors);

    [[nodiscard]] const std::vector<BarValidationError>& errors() const noexcept {
        return errors_;
    }

private:
    std::vector<BarValidationError> errors_;
};

[[nodiscard]] std::vector<BarValidationError> validate(const Bar& bar);
void require_valid(const Bar& bar);
[[nodiscard]] std::string_view to_string(BarValidationCode code) noexcept;

}  // namespace qte::market_data
