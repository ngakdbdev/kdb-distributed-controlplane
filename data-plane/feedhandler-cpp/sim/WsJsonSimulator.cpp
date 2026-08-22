#include "WsJsonSimulator.hpp"
#include <cstdio>

namespace fh::sim {

size_t WsJsonSimulator::run(InProcessTransport& transport) {
    size_t sent = 0;
    uint64_t tradeId = 100000;
    for (const auto& symbol : cfg_.symbols) {
        double price = cfg_.basePrice;
        for (uint32_t i = 0; i < cfg_.tradesPerSymbol; ++i) {
            const char* side = (i % 2 == 0) ? "buy" : "sell";
            price += (i % 2 == 0 ? 1 : -1) * (0.5 + (i % 7));

            // Spans both sides of the whole-unit rounding threshold
            // (0.2, 0.6, 1.0, 1.4, 1.8, repeating) so a live run visibly
            // demonstrates GenericWsJsonVenueAdapter's round-to-nearest
            // handling of fractional crypto sizes, not just the unit test.
            double size = 0.2 + (i % 5) * 0.4;
            char buf[512];
            std::snprintf(buf, sizeof(buf),
                R"({"type":"match","trade_id":%llu,"product_id":"%s","price":"%.2f","size":"%.4f","side":"%s","time":"2026-08-15T00:00:00.000000Z"})",
                static_cast<unsigned long long>(tradeId), symbol.c_str(), price, size, side);

            transport.feed(reinterpret_cast<const uint8_t*>(buf), std::string(buf).size());
            ++tradeId;
            ++sent;
        }
    }
    return sent;
}

} // namespace fh::sim
