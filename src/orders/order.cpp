#include "qte/orders/order.hpp"

#include <sstream>
#include <utility>

namespace qte::orders {
namespace {

[[nodiscard]] std::string validation_message(
    const std::vector<OrderValidationError>& errors) {
    std::ostringstream message;
    message << "invalid order request";

    bool first = true;
    for (const auto& error : errors) {
        message << (first ? ": " : "; ") << error.message;
        first = false;
    }
    return message.str();
}

void add_price_presence_errors(
    std::vector<OrderValidationError>& errors,
    const OrderRequest& request,
    const bool requires_limit,
    const bool requires_stop) {
    if (requires_limit && !request.limit_price.has_value()) {
        errors.push_back({
            OrderValidationCode::missing_limit_price,
            "order type requires a limit price",
        });
    } else if (!requires_limit && request.limit_price.has_value()) {
        errors.push_back({
            OrderValidationCode::unexpected_limit_price,
            "order type does not permit a limit price",
        });
    }

    if (requires_stop && !request.stop_price.has_value()) {
        errors.push_back({
            OrderValidationCode::missing_stop_price,
            "order type requires a stop price",
        });
    } else if (!requires_stop && request.stop_price.has_value()) {
        errors.push_back({
            OrderValidationCode::unexpected_stop_price,
            "order type does not permit a stop price",
        });
    }
}

[[nodiscard]] bool valid_rejection_reason(const OrderRejectionReason reason) noexcept {
    switch (reason) {
        case OrderRejectionReason::invalid_request:
        case OrderRejectionReason::risk:
            return true;
    }
    return false;
}

[[nodiscard]] bool valid_cancellation_reason(const OrderCancellationReason reason) noexcept {
    switch (reason) {
        case OrderCancellationReason::user_requested:
        case OrderCancellationReason::end_of_data:
        case OrderCancellationReason::execution_risk:
            return true;
    }
    return false;
}

}  // namespace

InvalidOrderRequest::InvalidOrderRequest(std::vector<OrderValidationError> errors)
    : std::invalid_argument(validation_message(errors)), errors_(std::move(errors)) {}

std::vector<OrderValidationError> validate(const OrderRequest& request) {
    std::vector<OrderValidationError> errors;

    switch (request.side) {
        case OrderSide::buy:
        case OrderSide::sell:
            break;
        default:
            errors.push_back({
                OrderValidationCode::unsupported_side,
                "unsupported order side",
            });
            break;
    }

    switch (request.type) {
        case OrderType::market:
            add_price_presence_errors(errors, request, false, false);
            break;
        case OrderType::limit:
            add_price_presence_errors(errors, request, true, false);
            break;
        case OrderType::stop:
            add_price_presence_errors(errors, request, false, true);
            break;
        case OrderType::stop_limit:
            add_price_presence_errors(errors, request, true, true);
            break;
        default:
            errors.push_back({
                OrderValidationCode::unsupported_type,
                "unsupported order type",
            });
            break;
    }

    switch (request.time_in_force) {
        case TimeInForce::good_til_canceled:
            break;
        default:
            errors.push_back({
                OrderValidationCode::unsupported_time_in_force,
                "unsupported time in force",
            });
            break;
    }

    return errors;
}

std::vector<OrderValidationError> validate(
    const OrderRequest& request,
    const market_data::InstrumentSpec& instrument) {
    auto errors = validate(request);

    if (request.symbol != instrument.symbol()) {
        errors.push_back({
            OrderValidationCode::instrument_symbol_mismatch,
            "order symbol does not match instrument metadata",
        });
    }

    if (request.limit_price.has_value()) {
        try {
            static_cast<void>(
                instrument.price_grid().canonicalize(request.limit_price->value()));
        } catch (const std::invalid_argument&) {
            errors.push_back({
                OrderValidationCode::limit_price_not_on_instrument_grid,
                "limit price is not valid on the instrument tick grid",
            });
        } catch (const std::out_of_range&) {
            errors.push_back({
                OrderValidationCode::limit_price_not_on_instrument_grid,
                "limit price is not valid on the instrument tick grid",
            });
        }
    }
    if (request.stop_price.has_value()) {
        try {
            static_cast<void>(
                instrument.price_grid().canonicalize(request.stop_price->value()));
        } catch (const std::invalid_argument&) {
            errors.push_back({
                OrderValidationCode::stop_price_not_on_instrument_grid,
                "stop price is not valid on the instrument tick grid",
            });
        } catch (const std::out_of_range&) {
            errors.push_back({
                OrderValidationCode::stop_price_not_on_instrument_grid,
                "stop price is not valid on the instrument tick grid",
            });
        }
    }

    return errors;
}

void require_valid(const OrderRequest& request) {
    auto errors = validate(request);
    if (!errors.empty()) {
        throw InvalidOrderRequest(std::move(errors));
    }
}

void require_valid(
    const OrderRequest& request,
    const market_data::InstrumentSpec& instrument) {
    auto errors = validate(request, instrument);
    if (!errors.empty()) {
        throw InvalidOrderRequest(std::move(errors));
    }
}

OrderRecord::OrderRecord(
    const core::OrderId id,
    OrderRequest request,
    const market_data::Timestamp submitted_at,
    const core::EventSequence submission_sequence,
    const core::EventSequence eligible_after_sequence)
    : id_(id),
      request_(std::move(request)),
      submitted_at_(submitted_at),
      submission_sequence_(submission_sequence),
      eligible_after_sequence_(eligible_after_sequence),
      remaining_quantity_(request_.quantity.as_quantity()) {
    if (eligible_after_sequence_ < submission_sequence_) {
        throw std::invalid_argument(
            "eligible-after sequence cannot precede submission sequence");
    }
}

bool OrderRecord::is_terminal() const noexcept {
    return orders::is_terminal(status_);
}

void OrderRecord::open(const market_data::InstrumentSpec& instrument) {
    require_status(OrderStatus::new_order, "open");
    require_valid(request_, instrument);
    if (request_.limit_price.has_value()) {
        request_.limit_price =
            instrument.price_grid().canonicalize(request_.limit_price->value());
    }
    if (request_.stop_price.has_value()) {
        request_.stop_price =
            instrument.price_grid().canonicalize(request_.stop_price->value());
    }
    status_ = OrderStatus::open;
}

void OrderRecord::reject(const OrderRejectionReason reason) {
    require_status(OrderStatus::new_order, "reject");
    if (!valid_rejection_reason(reason)) {
        throw std::invalid_argument("unsupported order rejection reason");
    }
    rejection_reason_ = reason;
    status_ = OrderStatus::rejected;
}

CancelResult OrderRecord::cancel(const OrderCancellationReason reason) {
    if (!valid_cancellation_reason(reason)) {
        throw std::invalid_argument("unsupported order cancellation reason");
    }
    if (is_terminal()) {
        return CancelResult::already_terminal;
    }
    if (status_ == OrderStatus::new_order) {
        throw InvalidOrderTransition("cannot cancel an order before it is opened");
    }

    cancellation_reason_ = reason;
    status_ = OrderStatus::canceled;
    return CancelResult::canceled;
}

void OrderRecord::record_fill_quantity(const core::ShareAmount quantity) {
    if (status_ != OrderStatus::open && status_ != OrderStatus::partially_filled) {
        throw InvalidOrderTransition("fill quantity requires an open order");
    }
    if (quantity.value() > remaining_quantity_.value()) {
        throw std::invalid_argument("fill quantity exceeds remaining order quantity");
    }

    const auto fill_delta = quantity.as_quantity();
    const auto new_filled_quantity = filled_quantity_.checked_add(fill_delta);
    const auto new_remaining_quantity = remaining_quantity_.checked_subtract(fill_delta);

    filled_quantity_ = new_filled_quantity;
    remaining_quantity_ = new_remaining_quantity;
    status_ = new_remaining_quantity.is_zero() ? OrderStatus::filled
                                               : OrderStatus::partially_filled;
}

bool OrderRecord::mark_stop_triggered() {
    if (status_ != OrderStatus::open && status_ != OrderStatus::partially_filled) {
        throw InvalidOrderTransition("stop trigger requires an open order");
    }
    if (request_.type != OrderType::stop && request_.type != OrderType::stop_limit) {
        throw InvalidOrderTransition("only stop orders can be triggered");
    }
    if (stop_triggered_) {
        return false;
    }
    stop_triggered_ = true;
    return true;
}

void OrderRecord::require_status(
    const OrderStatus expected,
    const std::string_view operation) const {
    if (status_ != expected) {
        throw InvalidOrderTransition(
            std::string{operation} + " is invalid for order status " +
            std::string{to_string(status_)});
    }
}

bool is_terminal(const OrderStatus status) noexcept {
    switch (status) {
        case OrderStatus::filled:
        case OrderStatus::canceled:
        case OrderStatus::rejected:
            return true;
        case OrderStatus::new_order:
        case OrderStatus::open:
        case OrderStatus::partially_filled:
            return false;
    }
    return false;
}

std::string_view to_string(const OrderStatus status) noexcept {
    switch (status) {
        case OrderStatus::new_order:
            return "NEW";
        case OrderStatus::open:
            return "OPEN";
        case OrderStatus::partially_filled:
            return "PARTIALLY_FILLED";
        case OrderStatus::filled:
            return "FILLED";
        case OrderStatus::canceled:
            return "CANCELED";
        case OrderStatus::rejected:
            return "REJECTED";
    }
    return "UNKNOWN";
}

std::string_view to_string(const OrderValidationCode code) noexcept {
    switch (code) {
        case OrderValidationCode::unsupported_side:
            return "unsupported_side";
        case OrderValidationCode::unsupported_type:
            return "unsupported_type";
        case OrderValidationCode::unsupported_time_in_force:
            return "unsupported_time_in_force";
        case OrderValidationCode::instrument_symbol_mismatch:
            return "instrument_symbol_mismatch";
        case OrderValidationCode::missing_limit_price:
            return "missing_limit_price";
        case OrderValidationCode::unexpected_limit_price:
            return "unexpected_limit_price";
        case OrderValidationCode::limit_price_not_on_instrument_grid:
            return "limit_price_not_on_instrument_grid";
        case OrderValidationCode::missing_stop_price:
            return "missing_stop_price";
        case OrderValidationCode::unexpected_stop_price:
            return "unexpected_stop_price";
        case OrderValidationCode::stop_price_not_on_instrument_grid:
            return "stop_price_not_on_instrument_grid";
    }
    return "unknown";
}

}  // namespace qte::orders
