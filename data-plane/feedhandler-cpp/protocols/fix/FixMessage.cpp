#include "FixMessage.hpp"
#include <cstdio>
#include <cstring>

namespace fh::fix {

namespace {
// Finds the next SOH-terminated "tag=value" field starting at pos. Returns
// the position just past the field's terminating SOH, or std::string::npos
// if none is complete yet.
bool nextField(const uint8_t* data, size_t length, size_t& pos, int& tag, std::string& value) {
    if (pos >= length) return false;
    size_t eq = pos;
    while (eq < length && data[eq] != '=') ++eq;
    if (eq >= length) return false;
    tag = std::atoi(std::string(reinterpret_cast<const char*>(data + pos), eq - pos).c_str());
    size_t sohPos = eq + 1;
    while (sohPos < length && data[sohPos] != static_cast<uint8_t>(SOH)) ++sohPos;
    if (sohPos >= length) return false; // field's value not terminated yet - incomplete
    value.assign(reinterpret_cast<const char*>(data + eq + 1), sohPos - (eq + 1));
    pos = sohPos + 1;
    return true;
}
} // namespace

size_t parseOne(const uint8_t* data, size_t length, FixMessage& out) {
    out.fields.clear();
    size_t pos = 0;
    int tag; std::string value;

    // Tag 8 (BeginString) must be first.
    if (!nextField(data, length, pos, tag, value) || tag != Tag::BeginString) return 0;
    out.set(Tag::BeginString, value);

    // Tag 9 (BodyLength) must be second - its value tells us exactly how
    // many bytes follow up to (but not including) the checksum field.
    if (!nextField(data, length, pos, tag, value) || tag != Tag::BodyLength) return 0;
    size_t bodyLength = static_cast<size_t>(std::atoi(value.c_str()));
    out.set(Tag::BodyLength, value);

    size_t bodyStart = pos;
    size_t bodyEnd = bodyStart + bodyLength; // position where the checksum field ("10=nnn|") should start
    if (bodyEnd > length) { out.fields.clear(); return 0; } // haven't received the full body yet

    pos = bodyStart;
    while (pos < bodyEnd) {
        if (!nextField(data, length, pos, tag, value)) { out.fields.clear(); return 0; }
        out.set(tag, value);
    }

    // Checksum field (tag 10) follows immediately after the body.
    if (pos >= length || !nextField(data, length, pos, tag, value) || tag != Tag::CheckSum) {
        out.fields.clear();
        return 0;
    }
    out.set(Tag::CheckSum, value);
    return pos;
}

std::string build(const std::string& beginString, const std::vector<std::pair<int, std::string>>& fields) {
    std::string body;
    for (const auto& [tag, value] : fields) {
        body += std::to_string(tag) + "=" + value + SOH;
    }

    std::string header = std::to_string(Tag::BeginString) + "=" + beginString + SOH;
    header += std::to_string(Tag::BodyLength) + "=" + std::to_string(body.size()) + SOH;

    std::string withoutChecksum = header + body;
    uint32_t sum = 0;
    for (unsigned char c : withoutChecksum) sum += c;
    char checksumBuf[8];
    std::snprintf(checksumBuf, sizeof(checksumBuf), "%03u", sum % 256);

    return withoutChecksum + std::to_string(Tag::CheckSum) + "=" + checksumBuf + SOH;
}

} // namespace fh::fix
