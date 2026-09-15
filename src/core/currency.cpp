#include "qte/core/currency.hpp"

#include <stdexcept>
#include <utility>

namespace qte::core {

Currency Currency::from_code(std::string code) {
    if (code.size() != 3) {
        throw std::invalid_argument("currency code must contain exactly three letters");
    }
    for (const char character : code) {
        if (character < 'A' || character > 'Z') {
            throw std::invalid_argument("currency code must use uppercase ASCII letters");
        }
    }
    return Currency{std::move(code)};
}

Currency Currency::usd() {
    return Currency{"USD"};
}

}  // namespace qte::core
