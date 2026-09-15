#pragma once

#include "qte/core/identifier.hpp"
#include "qte/core/price.hpp"
#include "qte/core/quantity.hpp"
#include "qte/market_data/instrument.hpp"

#include <optional>
#include <stdexcept>
#include <string>
#include <string_view>
#include <vector>

namespace qte::orders {

enum class OrderSide {
    buy,
    sell,
};

enum class OrderType {
    market,
    limit,
    stop,
    stop_limit,
};

enum class TimeInForce {
    good_til_canceled,
};

struct OrderRequest final {
    market_data::Symbol symbol;
    OrderSide side;
    core::ShareAmount quantity;
    OrderType type;
    std::optional<core::TickPrice> limit_price;
    std::optional<core::TickPrice> stop_price;
    TimeInForce time_in_force{TimeInForce::good_til_canceled};
};

enum class OrderValidationCode {
    unsupported_side,
    unsupported_type,
    unsupported_time_in_force,
    instrument_symbol_mismatch,
    missing_limit_price,
    unexpected_limit_price,
    limit_price_not_on_instrument_grid,
    missing_stop_price,
    unexpected_stop_price,
    stop_price_not_on_instrument_grid,
};

struct OrderValidationError final {
    OrderValidationCode code;
    std::string message;

    friend bool operator==(const OrderValidationError&, const OrderValidationError&) = default;
};

class InvalidOrderRequest final : public std::invalid_argument {
public:
    explicit InvalidOrderRequest(std::vector<OrderValidationError> errors);

    [[nodiscard]] const std::vector<OrderValidationError>& errors() const noexcept {
        return errors_;
    }

private:
    std::vector<OrderValidationError> errors_;
};

[[nodiscard]] std::vector<OrderValidationError> validate(const OrderRequest& request);
[[nodiscard]] std::vector<OrderValidationError> validate(
    const OrderRequest& request,
    const market_data::InstrumentSpec& instrument);
void require_valid(const OrderRequest& request);
void require_valid(
    const OrderRequest& request,
    const market_data::InstrumentSpec& instrument);

enum class OrderStatus {
    new_order,
    open,
    partially_filled,
    filled,
    canceled,
    rejected,
};

enum class OrderRejectionReason {
    invalid_request,
    risk,
};

enum class OrderCancellationReason {
    user_requested,
    end_of_data,
    execution_risk,
};

enum class CancelResult {
    canceled,
    already_terminal,
};

class InvalidOrderTransition final : public std::logic_error {
public:
    using std::logic_error::logic_error;
};

class OrderRecord final {
public:
    OrderRecord(
        core::OrderId id,
        OrderRequest request,
        market_data::Timestamp submitted_at,
        core::EventSequence submission_sequence,
        core::EventSequence eligible_after_sequence);

    OrderRecord(const OrderRecord&) = delete;
    OrderRecord& operator=(const OrderRecord&) = delete;
    OrderRecord(OrderRecord&&) noexcept = default;
    OrderRecord& operator=(OrderRecord&&) noexcept = default;

    [[nodiscard]] core::OrderId id() const noexcept { return id_; }
    [[nodiscard]] const OrderRequest& request() const noexcept { return request_; }
    [[nodiscard]] market_data::Timestamp submitted_at() const noexcept { return submitted_at_; }
    [[nodiscard]] core::EventSequence submission_sequence() const noexcept {
        return submission_sequence_;
    }
    [[nodiscard]] core::EventSequence eligible_after_sequence() const noexcept {
        return eligible_after_sequence_;
    }
    [[nodiscard]] OrderStatus status() const noexcept { return status_; }
    [[nodiscard]] core::ShareQuantity filled_quantity() const noexcept {
        return filled_quantity_;
    }
    [[nodiscard]] core::ShareQuantity remaining_quantity() const noexcept {
        return remaining_quantity_;
    }
    [[nodiscard]] bool stop_triggered() const noexcept { return stop_triggered_; }
    [[nodiscard]] std::optional<OrderRejectionReason> rejection_reason() const noexcept {
        return rejection_reason_;
    }
    [[nodiscard]] std::optional<OrderCancellationReason> cancellation_reason() const noexcept {
        return cancellation_reason_;
    }
    [[nodiscard]] bool is_terminal() const noexcept;

    void open(const market_data::InstrumentSpec& instrument);
    void reject(OrderRejectionReason reason);
    [[nodiscard]] CancelResult cancel(OrderCancellationReason reason);
    void record_fill_quantity(core::ShareAmount quantity);
    [[nodiscard]] bool mark_stop_triggered();

private:
    void require_status(OrderStatus expected, std::string_view operation) const;

    core::OrderId id_;
    OrderRequest request_;
    market_data::Timestamp submitted_at_;
    core::EventSequence submission_sequence_;
    core::EventSequence eligible_after_sequence_;
    OrderStatus status_{OrderStatus::new_order};
    core::ShareQuantity filled_quantity_{core::ShareQuantity::zero()};
    core::ShareQuantity remaining_quantity_;
    bool stop_triggered_{false};
    std::optional<OrderRejectionReason> rejection_reason_;
    std::optional<OrderCancellationReason> cancellation_reason_;
};

[[nodiscard]] bool is_terminal(OrderStatus status) noexcept;
[[nodiscard]] std::string_view to_string(OrderStatus status) noexcept;
[[nodiscard]] std::string_view to_string(OrderValidationCode code) noexcept;

}  // namespace qte::orders
