// SbeSchema.hpp - a runtime (not codegen'd) description of an SBE message
// schema: which templateId maps to which fields, at which fixed byte
// offsets, of which primitive type. Real SBE toolchains generate a C++
// struct per message type from an XML schema at BUILD time; this codebase
// deliberately decodes at RUN time against a schema object instead, so a
// new venue's SBE schema is data (loaded from config/providers/<venue>.yaml
// - see admin/SchemaLoader), not a recompile. The tradeoff is some
// per-field dispatch overhead versus codegen'd structs - a reasonable one
// for a platform whose whole point is "add a venue without touching code."
//
// IMPORTANT: the example schema shipped in this codebase (see
// venues/CmeMdp3VenueAdapter's default schema) is illustrative of SBE's
// SHAPE - the same header layout and field-offset mechanics real SBE uses -
// NOT a byte-for-byte reproduction of CME's actual current MDP3 schema,
// which is CME's own published spec and needs to be sourced from CME
// directly (their Market Data Platform documentation) and dropped in here
// as schema data before this adapter is used against a real CME feed.
#pragma once

#include <cstdint>
#include <map>
#include <string>
#include <vector>

namespace fh::sbe {

enum class FieldType { UInt8, UInt16, UInt32, UInt64, Int8, Int16, Int32, Int64, Char, FixedString };

struct Field {
    std::string name;
    FieldType type;
    uint16_t offset;   // byte offset from the start of the message BODY (after the 8-byte SBE message header)
    uint16_t size;      // in bytes - required for FixedString, ignored (derived from type) otherwise
};

struct MessageTemplate {
    uint16_t templateId;
    std::string name;   // maps to EventType via the venue adapter's own templateId -> EventType table
    uint16_t blockLength;
    std::vector<Field> fields;
};

// SBE message header, per the FIX SBE encoding spec (little-endian):
//   blockLength  2 bytes  - length of the root block (fixed fields)
//   templateId   2 bytes  - which MessageTemplate this is
//   schemaId     2 bytes
//   version      2 bytes
constexpr size_t MESSAGE_HEADER_LEN = 8;

class Schema {
public:
    void addTemplate(MessageTemplate t) { templates_[t.templateId] = std::move(t); }
    const MessageTemplate* find(uint16_t templateId) const {
        auto it = templates_.find(templateId);
        return it != templates_.end() ? &it->second : nullptr;
    }

private:
    std::map<uint16_t, MessageTemplate> templates_;
};

} // namespace fh::sbe
