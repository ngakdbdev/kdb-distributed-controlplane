// SbeDecoder.hpp - decodes a schema-driven SBE message body into a
// name->value field map (SbeMessage), given the message bytes AFTER the
// venue's own packet-level framing has already stripped its sequence/
// session header (see venues/CmeMdp3VenueAdapter for that outer layer -
// CME MDP3's real packet header is venue-specific, not part of SBE itself,
// which is why it isn't handled here). This class is pure decode logic
// with zero I/O, unit-testable against hand-built byte sequences matching
// a known schema.
#pragma once

#include <cstdint>
#include <map>
#include <string>
#include <variant>

#include "SbeSchema.hpp"

namespace fh::sbe {

using FieldValue = std::variant<uint64_t, int64_t, std::string>;

struct SbeMessage {
    uint16_t templateId = 0;
    std::string templateName;
    std::map<std::string, FieldValue> fields;

    uint64_t getUInt(const std::string& name, uint64_t fallback = 0) const {
        auto it = fields.find(name);
        if (it == fields.end()) return fallback;
        if (auto* v = std::get_if<uint64_t>(&it->second)) return *v;
        return fallback;
    }
    int64_t getInt(const std::string& name, int64_t fallback = 0) const {
        auto it = fields.find(name);
        if (it == fields.end()) return fallback;
        if (auto* v = std::get_if<int64_t>(&it->second)) return *v;
        return fallback;
    }
    std::string getString(const std::string& name) const {
        auto it = fields.find(name);
        if (it == fields.end()) return "";
        if (auto* v = std::get_if<std::string>(&it->second)) return *v;
        return "";
    }
};

class SbeDecoder {
public:
    explicit SbeDecoder(const Schema& schema) : schema_(schema) {}

    // Decodes exactly one SBE message starting at `data`. Returns the
    // number of bytes consumed (header + blockLength), or 0 if the bytes
    // don't form a complete, recognized message (unknown templateId, or
    // fewer bytes than the header+block requires) - callers loop while
    // this returns non-zero to walk multiple back-to-back messages in one
    // packet, the same shape as MoldUdp64Decoder's message loop.
    size_t decodeOne(const uint8_t* data, size_t length, SbeMessage& out) const;

private:
    const Schema& schema_;
};

} // namespace fh::sbe
