#include "test_framework.hpp"
#include "../protocols/sbe/SbeDecoder.hpp"

using namespace fh::sbe;

namespace {
void putU16LE(std::vector<uint8_t>& b, uint16_t v) { b.push_back(static_cast<uint8_t>(v)); b.push_back(static_cast<uint8_t>(v >> 8)); }
void putU32LE(std::vector<uint8_t>& b, uint32_t v) { for (int i = 0; i < 4; ++i) b.push_back(static_cast<uint8_t>((v >> (i*8)) & 0xFF)); }
void putI64LE(std::vector<uint8_t>& b, int64_t v) { auto u = static_cast<uint64_t>(v); for (int i = 0; i < 8; ++i) b.push_back(static_cast<uint8_t>((u >> (i*8)) & 0xFF)); }

Schema buildTestSchema() {
    Schema schema;
    MessageTemplate tmpl;
    tmpl.templateId = 7;
    tmpl.name = "TestTrade";
    tmpl.blockLength = 13; // instrumentId(4) + price(8) + side(1)
    tmpl.fields = {
        {"instrumentId", FieldType::UInt32, 0, 4},
        {"price", FieldType::Int64, 4, 8},
        {"side", FieldType::Char, 12, 1},
    };
    schema.addTemplate(tmpl);
    return schema;
}
} // namespace

TEST(sbe_decodes_known_template_fields) {
    Schema schema = buildTestSchema();
    SbeDecoder decoder(schema);

    std::vector<uint8_t> msg;
    putU16LE(msg, 13);  // blockLength
    putU16LE(msg, 7);   // templateId
    putU16LE(msg, 1);   // schemaId
    putU16LE(msg, 0);   // version
    putU32LE(msg, 555); // instrumentId
    putI64LE(msg, 182500000000LL); // price (fixed point)
    msg.push_back('B'); // side

    SbeMessage out;
    size_t consumed = decoder.decodeOne(msg.data(), msg.size(), out);

    CHECK_EQ(consumed, msg.size());
    CHECK_EQ(out.templateId, static_cast<uint16_t>(7));
    CHECK_EQ(out.getUInt("instrumentId"), 555ULL);
    CHECK_EQ(out.getInt("price"), 182500000000LL);
    CHECK_EQ(out.getString("side"), std::string("B"));
}

TEST(sbe_unknown_template_id_returns_zero) {
    Schema schema = buildTestSchema();
    SbeDecoder decoder(schema);
    std::vector<uint8_t> msg;
    putU16LE(msg, 13); putU16LE(msg, 999); putU16LE(msg, 1); putU16LE(msg, 0);
    msg.resize(msg.size() + 13);

    SbeMessage out;
    CHECK_EQ(decoder.decodeOne(msg.data(), msg.size(), out), 0u);
}

TEST(sbe_truncated_message_returns_zero) {
    Schema schema = buildTestSchema();
    SbeDecoder decoder(schema);
    std::vector<uint8_t> msg;
    putU16LE(msg, 13); putU16LE(msg, 7); putU16LE(msg, 1); putU16LE(msg, 0);
    // missing the 13-byte body entirely

    SbeMessage out;
    CHECK_EQ(decoder.decodeOne(msg.data(), msg.size(), out), 0u);
}
