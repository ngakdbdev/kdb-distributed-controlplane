#include "SbeDecoder.hpp"
#include <cstring>

namespace fh::sbe {

namespace {
uint16_t readU16LE(const uint8_t* p) { return static_cast<uint16_t>(p[0] | (p[1] << 8)); }

FieldValue readField(const uint8_t* base, const Field& f) {
    const uint8_t* p = base + f.offset;
    switch (f.type) {
        case FieldType::UInt8: return static_cast<uint64_t>(p[0]);
        case FieldType::UInt16: return static_cast<uint64_t>(p[0] | (p[1] << 8));
        case FieldType::UInt32: {
            uint32_t v = static_cast<uint32_t>(p[0]) | (static_cast<uint32_t>(p[1]) << 8) |
                        (static_cast<uint32_t>(p[2]) << 16) | (static_cast<uint32_t>(p[3]) << 24);
            return static_cast<uint64_t>(v);
        }
        case FieldType::UInt64: {
            uint64_t v = 0;
            for (int i = 7; i >= 0; --i) v = (v << 8) | p[i];
            return v;
        }
        case FieldType::Int8: return static_cast<int64_t>(static_cast<int8_t>(p[0]));
        case FieldType::Int16: return static_cast<int64_t>(static_cast<int16_t>(p[0] | (p[1] << 8)));
        case FieldType::Int32: {
            int32_t v = static_cast<int32_t>(static_cast<uint32_t>(p[0]) | (static_cast<uint32_t>(p[1]) << 8) |
                                             (static_cast<uint32_t>(p[2]) << 16) | (static_cast<uint32_t>(p[3]) << 24));
            return static_cast<int64_t>(v);
        }
        case FieldType::Int64: {
            uint64_t v = 0;
            for (int i = 7; i >= 0; --i) v = (v << 8) | p[i];
            return static_cast<int64_t>(v);
        }
        case FieldType::Char: return std::string(1, static_cast<char>(p[0]));
        case FieldType::FixedString: {
            size_t n = f.size;
            while (n > 0 && (p[n - 1] == '\0' || p[n - 1] == ' ')) --n;
            return std::string(reinterpret_cast<const char*>(p), n);
        }
    }
    return static_cast<uint64_t>(0);
}
} // namespace

size_t SbeDecoder::decodeOne(const uint8_t* data, size_t length, SbeMessage& out) const {
    if (length < MESSAGE_HEADER_LEN) return 0;
    uint16_t blockLength = readU16LE(data);
    uint16_t templateId = readU16LE(data + 2);
    // schemaId (data+4) / version (data+6) are available but not required to decode a known template

    const MessageTemplate* tmpl = schema_.find(templateId);
    if (tmpl == nullptr) return 0;

    size_t total = MESSAGE_HEADER_LEN + blockLength;
    if (length < total) return 0;

    out.templateId = templateId;
    out.templateName = tmpl->name;
    out.fields.clear();
    const uint8_t* body = data + MESSAGE_HEADER_LEN;
    for (const auto& f : tmpl->fields) {
        out.fields[f.name] = readField(body, f);
    }
    return total;
}

} // namespace fh::sbe
