#include "test_framework.hpp"
#include "../protocols/soupbintcp/SoupBinTcpDecoder.hpp"

using namespace fh;

namespace {
struct CapturingMessageSink : MessageSink {
    std::vector<DecodedMessage> messages;
    void onMessage(const DecodedMessage& m) override { messages.push_back(m); }
};

std::vector<uint8_t> buildSoupPacket(char type, const std::vector<uint8_t>& payload) {
    std::vector<uint8_t> pkt;
    uint16_t len = static_cast<uint16_t>(1 + payload.size());
    pkt.push_back(static_cast<uint8_t>(len >> 8));
    pkt.push_back(static_cast<uint8_t>(len));
    pkt.push_back(static_cast<uint8_t>(type));
    pkt.insert(pkt.end(), payload.begin(), payload.end());
    return pkt;
}
} // namespace

TEST(soupbintcp_sequenced_data_assigns_incrementing_local_sequence) {
    SoupBinTcpDecoder decoder(1);
    CapturingMessageSink sink;
    auto p1 = buildSoupPacket('S', {'A', '1'});
    auto p2 = buildSoupPacket('S', {'A', '2'});

    decoder.decode(p1.data(), p1.size(), 0, sink);
    decoder.decode(p2.data(), p2.size(), 0, sink);

    CHECK_EQ(sink.messages.size(), 2u);
    CHECK_EQ(sink.messages[0].sequence, 1u);
    CHECK_EQ(sink.messages[1].sequence, 2u);
    CHECK_EQ(sink.messages[0].msgType, 'A');
}

TEST(soupbintcp_packet_split_across_two_decode_calls_still_parses) {
    SoupBinTcpDecoder decoder;
    CapturingMessageSink sink;
    auto full = buildSoupPacket('S', {'X', 'Y', 'Z'});

    // simulate a TCP recv() boundary landing mid-packet
    decoder.decode(full.data(), 2, 0, sink);      // just the length prefix
    CHECK_EQ(sink.messages.size(), 0u);
    decoder.decode(full.data() + 2, full.size() - 2, 0, sink);
    CHECK_EQ(sink.messages.size(), 1u);
    CHECK_EQ(sink.messages[0].payload.size(), 3u);
}

TEST(soupbintcp_login_accepted_sets_flag) {
    SoupBinTcpDecoder decoder;
    CapturingMessageSink sink;
    auto p = buildSoupPacket('A', {});
    decoder.decode(p.data(), p.size(), 0, sink);
    CHECK(decoder.loginAccepted());
}

TEST(soupbintcp_heartbeat_emitted_but_not_sequenced) {
    SoupBinTcpDecoder decoder(1);
    CapturingMessageSink sink;
    auto hb = buildSoupPacket('H', {});
    decoder.decode(hb.data(), hb.size(), 0, sink);
    CHECK_EQ(sink.messages.size(), 1u);
    CHECK_EQ(sink.messages[0].msgType, 'H');
    CHECK_EQ(sink.messages[0].sequence, 0u);
}
