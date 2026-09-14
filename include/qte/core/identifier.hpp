#pragma once

#include <compare>
#include <cstdint>
#include <limits>
#include <stdexcept>

namespace qte::core {

template <typename Tag>
class Identifier final {
public:
    explicit Identifier(const std::uint64_t value) : value_(value) {
        if (value == 0) {
            throw std::invalid_argument("identifier zero is reserved");
        }
    }

    [[nodiscard]] std::uint64_t value() const noexcept { return value_; }

    friend auto operator<=>(const Identifier&, const Identifier&) = default;

private:
    std::uint64_t value_;
};

struct OrderIdTag final {};
struct FillIdTag final {};
struct EventSequenceTag final {};

using OrderId = Identifier<OrderIdTag>;
using FillId = Identifier<FillIdTag>;
using EventSequence = Identifier<EventSequenceTag>;

template <typename Id, std::uint64_t Maximum = std::numeric_limits<std::uint64_t>::max()>
class SequentialIdGenerator final {
    static_assert(Maximum > 0, "an ID generator must be able to issue at least one ID");

public:
    [[nodiscard]] Id next() {
        if (exhausted_) {
            throw std::overflow_error("identifier sequence exhausted");
        }

        const Id result{next_};
        if (next_ == Maximum) {
            exhausted_ = true;
        } else {
            ++next_;
        }
        return result;
    }

    [[nodiscard]] bool exhausted() const noexcept { return exhausted_; }

private:
    std::uint64_t next_{1};
    bool exhausted_{false};
};

using OrderIdGenerator = SequentialIdGenerator<OrderId>;
using FillIdGenerator = SequentialIdGenerator<FillId>;
using EventSequenceGenerator = SequentialIdGenerator<EventSequence>;

}  // namespace qte::core
