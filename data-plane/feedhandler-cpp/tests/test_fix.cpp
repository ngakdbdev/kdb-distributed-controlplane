#include "test_framework.hpp"
#include "../protocols/fix/FixDecoder.hpp"
#include "../protocols/fix/FixMessage.hpp"

using namespace fh;
using namespace fh::fix;

TEST(fix_build_then_parse_round_trips) {
    std::string msg = build("FIX.4.4", {
        {Tag::MsgType, "0"},
        {Tag::SenderCompID, "SIM"},
        {Tag::TargetCompID, "VENUE"},
        {Tag::MsgSeqNum, "42"},
    });

    FixMessage parsed;
    size_t consumed = parseOne(reinterpret_cast<const uint8_t*>(msg.data()), msg.size(), parsed);

    CHECK_EQ(consumed, msg.size());
    CHECK_EQ(parsed.get(Tag::MsgType), std::string("0"));
    CHECK_EQ(parsed.get(Tag::SenderCompID), std::string("SIM"));
    CHECK_EQ(parsed.get(Tag::MsgSeqNum), std::string("42"));
    CHECK(parsed.has(Tag::CheckSum));
}

TEST(fix_parse_incomplete_message_returns_zero) {
    std::string msg = "8=FIX.4.4\x01" "9=100\x01" "35=0\x01"; // BodyLength says 100 more bytes, but they're not here
    FixMessage parsed;
    CHECK_EQ(parseOne(reinterpret_cast<const uint8_t*>(msg.data()), msg.size(), parsed), 0u);
}

TEST(fix_decoder_buffers_message_split_across_two_calls) {
    std::string msg = build("FIX.4.4", {{Tag::MsgType, "1"}, {Tag::MsgSeqNum, "5"}});
    FixDecoder decoder;

    struct Sink : MessageSink {
        std::vector<DecodedMessage> messages;
        void onMessage(const DecodedMessage& m) override { messages.push_back(m); }
    } sink;

    size_t half = msg.size() / 2;
    decoder.decode(reinterpret_cast<const uint8_t*>(msg.data()), half, 0, sink);
    CHECK_EQ(sink.messages.size(), 0u);
    decoder.decode(reinterpret_cast<const uint8_t*>(msg.data()) + half, msg.size() - half, 0, sink);
    CHECK_EQ(sink.messages.size(), 1u);
    CHECK_EQ(sink.messages[0].msgType, '1');
    CHECK_EQ(sink.messages[0].sequence, 5u);
}

TEST(fix_decoder_handles_two_back_to_back_messages_in_one_call) {
    std::string m1 = build("FIX.4.4", {{Tag::MsgType, "0"}, {Tag::MsgSeqNum, "1"}});
    std::string m2 = build("FIX.4.4", {{Tag::MsgType, "0"}, {Tag::MsgSeqNum, "2"}});
    std::string both = m1 + m2;

    FixDecoder decoder;
    struct Sink : MessageSink {
        std::vector<DecodedMessage> messages;
        void onMessage(const DecodedMessage& m) override { messages.push_back(m); }
    } sink;

    decoder.decode(reinterpret_cast<const uint8_t*>(both.data()), both.size(), 0, sink);
    CHECK_EQ(sink.messages.size(), 2u);
    CHECK_EQ(sink.messages[0].sequence, 1u);
    CHECK_EQ(sink.messages[1].sequence, 2u);
}
