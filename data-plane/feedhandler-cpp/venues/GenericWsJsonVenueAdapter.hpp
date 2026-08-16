// GenericWsJsonVenueAdapter.hpp - the ONE adapter behind every JSON-over-
// WebSocket provider (Coinbase, Binance, Kraken today; any future JSON
// vendor feed without a code change - see design section 15). What
// differs per provider is config, not code:
//
//   filter_field / filter_value   - only emit when this top-level field
//                                    matches (Coinbase: type=="match",
//                                    Binance: e=="trade" (after descending
//                                    into data_path), Kraken: channel=="trade")
//   data_path                     - optional single-level key to descend
//                                    into before reading fields (Binance
//                                    wraps the real payload under "data")
//   field_symbol/field_price/
//   field_qty/field_side          - which JSON key holds each value
//   side_buy_value                - which raw side value means Buy
//
// If the (possibly data_path-descended) record is itself a JSON array
// (Kraken's "data":[...]), each element is processed as its own trade -
// this is what makes Kraken's shape work with the SAME adapter as
// Coinbase's flat single-object messages, purely through config.
#pragma once

#include "../core/VenueAdapter.hpp"

namespace fh {

class GenericWsJsonVenueAdapter : public IVenueAdapter {
public:
    void configure(const FeedConfig& config, EventSink& sink) override;
    void onMessage(const DecodedMessage& msg) override;
    std::string name() const override { return "generic_wsjson"; }

private:
    EventSink* sink_ = nullptr;
    FeedConfig config_;
};

} // namespace fh
