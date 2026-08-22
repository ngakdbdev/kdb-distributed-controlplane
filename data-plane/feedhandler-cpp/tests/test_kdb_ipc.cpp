#include "test_framework.hpp"
#include "../publishers/KdbIpc.hpp"

using namespace fh::kdb;

TEST(kdb_symbol_vector_header_and_null_termination) {
    auto v = Writer::symbolVector({"AAPL", "MSFT"});
    CHECK_EQ(v[0], static_cast<uint8_t>(11)); // symbol vector type
    CHECK_EQ(v[1], static_cast<uint8_t>(0));  // attribute
    // count (LE u32) = 2
    CHECK_EQ(v[2], static_cast<uint8_t>(2)); CHECK_EQ(v[3], static_cast<uint8_t>(0));
    CHECK_EQ(v[4], static_cast<uint8_t>(0)); CHECK_EQ(v[5], static_cast<uint8_t>(0));
    // "AAPL\0MSFT\0"
    std::string rest(v.begin() + 6, v.end());
    CHECK_EQ(rest, std::string("AAPL\0MSFT\0", 10));
}

TEST(kdb_timestamp_vector_encodes_little_endian_int64) {
    auto v = Writer::timestampVector({1, -1});
    CHECK_EQ(v[0], static_cast<uint8_t>(12)); // timestamp vector type
    CHECK_EQ(v.size(), static_cast<size_t>(6 + 16)); // header + 2*8 bytes
    // first value: 1 (LE) -> byte0=1, rest 0
    CHECK_EQ(v[6], static_cast<uint8_t>(1));
    for (int i = 7; i < 14; ++i) CHECK_EQ(v[i], static_cast<uint8_t>(0));
    // second value: -1 -> all 0xFF
    for (int i = 14; i < 22; ++i) CHECK_EQ(v[i], static_cast<uint8_t>(0xFF));
}

TEST(kdb_table_wraps_dict_with_correct_type_bytes) {
    auto priceCol = Writer::floatVector({1.5});
    auto table = Writer::table({"price"}, {priceCol});
    CHECK_EQ(table[0], static_cast<uint8_t>(98)); // table type
    CHECK_EQ(table[1], static_cast<uint8_t>(0));  // table attribute
    CHECK_EQ(table[2], static_cast<uint8_t>(99)); // dict type follows immediately
}

TEST(kdb_async_upd_message_header_length_matches_body) {
    auto tableBytes = Writer::table({"price"}, {Writer::floatVector({1.0})});
    auto msg = Writer::asyncUpdMessage("trade", tableBytes);

    CHECK_EQ(msg[0], static_cast<uint8_t>(1)); // little-endian marker
    CHECK_EQ(msg[1], static_cast<uint8_t>(0)); // async message type

    uint32_t declaredLen = static_cast<uint32_t>(msg[4]) | (static_cast<uint32_t>(msg[5]) << 8) |
                           (static_cast<uint32_t>(msg[6]) << 16) | (static_cast<uint32_t>(msg[7]) << 24);
    CHECK_EQ(declaredLen, static_cast<uint32_t>(msg.size()));
}

TEST(kdb_epoch_conversion_matches_known_offset) {
    // 2000-01-01T00:00:00Z as Unix-epoch nanoseconds -> kdb+ timestamp 0
    CHECK_EQ(toKdbTimestampNs(946684800000000000ULL), 0LL);
}

TEST(kdb_handshake_request_ends_with_capability_and_nul) {
    std::string hs = handshakeRequest("user:pass");
    CHECK_EQ(hs.size(), static_cast<size_t>(11)); // "user:pass" (9) + capability byte + NUL
    CHECK_EQ(static_cast<uint8_t>(hs[9]), static_cast<uint8_t>(3));
    CHECK_EQ(hs[10], '\0');
}
