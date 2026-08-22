#include "test_framework.hpp"
#include "../protocols/moldudp64/MoldUdp64Decoder.hpp"
#include "../core/EventSink.hpp"

using namespace fh;

namespace {
struct CapturingMessageSink : MessageSink {
    std::vector<DecodedMessage> messages;
    void onMessage(const DecodedMessage& m) override { messages.push_back(m); }
};

std::vector<uint8_t> buildPacket(const std::string& session, uint64_t seq, uint16_t msgCount,
                                 const std::vector<std::vector<uint8_t>>& msgs) {
    std::vector<uint8_t> pkt;
    for (size_t i = 0; i < 10; ++i) pkt.push_back(i < session.size() ? static_cast<uint8_t>(session[i]) : ' ');
    for (int i = 7; i >= 0; --i) pkt.push_back(static_cast<uint8_t>((seq >> (i * 8)) & 0xFF));
    pkt.push_back(static_cast<uint8_t>(msgCount >> 8));
    pkt.push_back(static_cast<uint8_t>(msgCount));
    for (const auto& m : msgs) {
        pkt.push_back(static_cast<uint8_t>(m.size() >> 8));
        pkt.push_back(static_cast<uint8_t>(m.size()));
        pkt.insert(pkt.end(), m.begin(), m.end());
    }
    return pkt;
}
} // namespace

TEST(moldudp64_single_message_sequence_and_payload) {
    MoldUdp64Decoder decoder;
    CapturingMessageSink sink;
    std::vector<uint8_t> inner = {'A', 'B', 'C'};
    auto pkt = buildPacket("SESSION001", 100, 1, {inner});

    decoder.decode(pkt.data(), pkt.size(), 12345, sink);

    CHECK_EQ(sink.messages.size(), 1u);
    CHECK_EQ(sink.messages[0].sequence, 100u);
    CHECK_EQ(sink.messages[0].payload.size(), 3u);
    CHECK_EQ(sink.messages[0].payload[0], 'A');
    CHECK_EQ(sink.messages[0].receiveTimestampNs, 12345u);
}

TEST(moldudp64_multiple_messages_get_sequential_sequence_numbers) {
    MoldUdp64Decoder decoder;
    CapturingMessageSink sink;
    std::vector<uint8_t> m1 = {'X'}, m2 = {'Y'}, m3 = {'Z'};
    auto pkt = buildPacket("S", 500, 3, {m1, m2, m3});

    decoder.decode(pkt.data(), pkt.size(), 0, sink);

    CHECK_EQ(sink.messages.size(), 3u);
    CHECK_EQ(sink.messages[0].sequence, 500u);
    CHECK_EQ(sink.messages[1].sequence, 501u);
    CHECK_EQ(sink.messages[2].sequence, 502u);
}

TEST(moldudp64_heartbeat_marker_produces_no_messages) {
    MoldUdp64Decoder decoder;
    CapturingMessageSink sink;
    auto pkt = buildPacket("S", 999, 0xFFFF, {});

    decoder.decode(pkt.data(), pkt.size(), 0, sink);

    CHECK_EQ(sink.messages.size(), 0u);
    CHECK(decoder.lastWasHeartbeat());
}

TEST(moldudp64_end_of_session_marker_produces_no_messages) {
    MoldUdp64Decoder decoder;
    CapturingMessageSink sink;
    auto pkt = buildPacket("S", 1, 0x0000, {});

    decoder.decode(pkt.data(), pkt.size(), 0, sink);

    CHECK_EQ(sink.messages.size(), 0u);
    CHECK(decoder.lastWasEndOfSession());
}

TEST(moldudp64_truncated_packet_is_dropped_not_crashed) {
    MoldUdp64Decoder decoder;
    CapturingMessageSink sink;
    std::vector<uint8_t> tooShort(10, 0); // less than the 20-byte header

    decoder.decode(tooShort.data(), tooShort.size(), 0, sink);

    CHECK_EQ(sink.messages.size(), 0u);
}
