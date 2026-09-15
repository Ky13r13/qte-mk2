#pragma once

#include <compare>
#include <string>
#include <utility>

namespace qte::core {

// Canonical three-letter currency code. This validates representation only;
// recognition of a particular ISO currency belongs to configuration policy.
class Currency final {
public:
    [[nodiscard]] static Currency from_code(std::string code);
    [[nodiscard]] static Currency usd();

    [[nodiscard]] const std::string& code() const noexcept { return code_; }

    friend auto operator<=>(const Currency&, const Currency&) = default;

private:
    explicit Currency(std::string code) : code_(std::move(code)) {}

    std::string code_;
};

}  // namespace qte::core
