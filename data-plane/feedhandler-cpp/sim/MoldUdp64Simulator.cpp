#include "MoldUdp64Simulator.hpp"
#include "../core/Transport.hpp"
#include <cstring>

namespace fh::sim {

namespace {
void putU16(std::vector<uint8_t>& b, uint16_t v) { b.push_back(static_cast<uint8_t>(v >> 8)); b.push_back(static_cast<uint8_t>(v)); }
void putU32(std::vector<uint8_t>& b, uint32_t v) {
    for (int i = 3; i >= 0; --i) b.push_back(static_cast<uint8_t>((v >> (i * 8)) & 0xFF));
}
void putU48(std::vector<uint8_t>& b, uint64_t v) {
    for (int i = 5; i >= 0; --i) b.push_back(static_cast<uint8_t>((v >> (i * 8)) & 0xFF));
}
void putU64(std::vector<uint8_t>& b, uint64_t v) {
    for (int i = 7; i >= 0; --i) b.push_back(static_cast<uint8_t>((v >> (i * 8)) & 0xFF));
}
void putStock(std::vector<uint8_t>& b, const std::string& symbol) {
    for (size_t i = 0; i < 8; ++i) b.push_back(i < symbol.size() ? static_cast<uint8_t>(symbol[i]) : ' ');
}

std::vector<uint8_t> buildSystemEvent(uint16_t locate, uint64_t ts, char eventCode) {
    std::vector<uint8_t> m;
    m.push_back('S'); putU16(m, locate); putU16(m, 0); putU48(m, ts); m.push_back(static_cast<uint8_t>(eventCode));
    return m;
}
std::vector<uint8_t> buildStockDirectory(uint16_t locate, uint64_t ts, const std::string& symbol) {
    std::vector<uint8_t> m;
    m.push_back('R'); putU16(m, locate); putU16(m, 0); putU48(m, ts);
    putStock(m, symbol);
    m.push_back('Q');  // market category
    m.push_back('N');  // financial status indicator
    putU32(m, 100);    // round lot size
    m.resize(39, 0);   // real ITCH 5.0 Stock Directory is 39 bytes - see ItchMessages.hpp's parseStockDirectory length guard
    return m;
}
std::vector<uint8_t> buildAddOrder(uint16_t locate, uint64_t ts, uint64_t orderRef, char side,
                                   uint32_t shares, const std::string& symbol, uint32_t priceTicks) {
    std::vector<uint8_t> m;
    m.push_back('A'); putU16(m, locate); putU16(m, 0); putU48(m, ts); putU64(m, orderRef);
    m.push_back(static_cast<uint8_t>(side)); putU32(m, shares); putStock(m, symbol); putU32(m, priceTicks);
    return m;
}
std::vector<uint8_t> buildTrade(uint16_t locate, uint64_t ts, uint64_t orderRef, char side, uint32_t shares,
                                const std::string& symbol, uint32_t priceTicks, uint64_t matchNumber) {
    std::vector<uint8_t> m;
    m.push_back('P'); putU16(m, locate); putU16(m, 0); putU48(m, ts); putU64(m, orderRef);
    m.push_back(static_cast<uint8_t>(side)); putU32(m, shares); putStock(m, symbol); putU32(m, priceTicks);
    putU64(m, matchNumber);
    return m;
}
std::vector<uint8_t> buildOrderCancel(uint16_t locate, uint64_t ts, uint64_t orderRef, uint32_t cancelled) {
    std::vector<uint8_t> m;
    m.push_back('X'); putU16(m, locate); putU16(m, 0); putU48(m, ts); putU64(m, orderRef); putU32(m, cancelled);
    return m;
}
std::vector<uint8_t> buildOrderDelete(uint16_t locate, uint64_t ts, uint64_t orderRef) {
    std::vector<uint8_t> m;
    m.push_back('D'); putU16(m, locate); putU16(m, 0); putU48(m, ts); putU64(m, orderRef);
    return m;
}
} // namespace

std::vector<uint8_t> MoldUdp64Simulator::buildPacket(const std::vector<std::vector<uint8_t>>& innerMessages) {
    std::vector<uint8_t> pkt;
    for (size_t i = 0; i < 10 && i < cfg_.session.size(); ++i) pkt.push_back(static_cast<uint8_t>(cfg_.session[i]));
    while (pkt.size() < 10) pkt.push_back(' ');
    // sequence (8 bytes) + message count (2 bytes)
    for (int i = 7; i >= 0; --i) pkt.push_back(static_cast<uint8_t>((sequence_ >> (i * 8)) & 0xFF));
    putU16(pkt, static_cast<uint16_t>(innerMessages.size()));
    for (const auto& m : innerMessages) {
        putU16(pkt, static_cast<uint16_t>(m.size()));
        pkt.insert(pkt.end(), m.begin(), m.end());
    }
    sequence_ += innerMessages.size();
    return pkt;
}

bool MoldUdp64Simulator::sendPacket(InProcessTransport& transport, const std::vector<uint8_t>& innerMessage,
                                    uint32_t& packetCount) {
    ++packetCount;
    auto pkt = buildPacket({innerMessage});
    if (cfg_.dropEveryNth != 0 && packetCount % cfg_.dropEveryNth == 0) {
        return false; // simulated packet loss - see file header
    }
    transport.feed(pkt);
    return true;
}

size_t MoldUdp64Simulator::run(InProcessTransport& transport) {
    // Real current time-of-day, not a fixed "market open" constant - a
    // real venue's ITCH timestamps are always close to wall-clock now.
    // Confirmed live: a fixed 09:30:00 start meant every synthetic trade
    // fell further behind real time with each run, and rdb.q's own
    // watermark (.rdb.upd, fed by wdb's rolling retention broadcast - see
    // rdb.q's header comment) silently drops any row older than that
    // watermark. A fixed-in-the-past clock meant NASDAQ rows were
    // accepted by the tickerplant (zero .u.errs) but discarded by rdb
    // before ever becoming queryable - not a publish failure, a staleness
    // filter doing exactly what it's designed to do.
    uint64_t ts = nowNs() % 86400000000000ULL;
    uint32_t packetCount = 0;
    size_t sent = 0;

    transport.feed(buildPacket({buildSystemEvent(0, ts, 'O')})); // Start of Messages - locate 0, not symbol-scoped
    ++sent;

    uint16_t locate = 1;
    uint64_t orderRef = 1000;
    uint64_t matchNumber = 5000;
    for (const auto& symbol : cfg_.symbols) {
        transport.feed(buildPacket({buildStockDirectory(locate, ts, symbol)}));
        ++sent;
        double price = cfg_.basePrice;
        for (uint32_t i = 0; i < cfg_.tradesPerSymbol; ++i) {
            char side = (i % 2 == 0) ? 'B' : 'S';
            uint32_t priceTicks = static_cast<uint32_t>(price * 10000);
            uint32_t shares = 100 + (i % 5) * 100;

            if (sendPacket(transport, buildAddOrder(locate, ts, orderRef, side, shares, symbol, priceTicks), packetCount)) ++sent;
            if (sendPacket(transport, buildTrade(locate, ts, orderRef, side, shares, symbol, priceTicks, matchNumber), packetCount)) ++sent;
            if (i % 3 == 0) {
                if (sendPacket(transport, buildOrderCancel(locate, ts, orderRef, shares / 2), packetCount)) ++sent;
            } else {
                if (sendPacket(transport, buildOrderDelete(locate, ts, orderRef), packetCount)) ++sent;
            }

            ++orderRef; ++matchNumber; ts += 1000000; // +1ms per synthetic event
            price += (side == 'B' ? 1 : -1) * 0.01;
        }
        ++locate;
    }

    transport.feed(buildPacket({buildSystemEvent(0, ts, 'C')})); // End of Messages
    ++sent;
    return sent;
}

} // namespace fh::sim
