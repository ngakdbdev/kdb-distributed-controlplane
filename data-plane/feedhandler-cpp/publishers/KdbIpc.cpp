#include "KdbIpc.hpp"

#include <cstring>

namespace fh::kdb {

namespace {
// kdb+ IPC type codes used here (see code.kx.com/q/basics/datatypes/):
//   0   general list       -11  symbol atom
//   7   long vector         9  float vector
//   11  symbol vector      12  timestamp vector
//   98  table              99  dict
constexpr int8_t TYPE_GENERAL_LIST = 0;
constexpr int8_t TYPE_LONG_VECTOR = 7;
constexpr int8_t TYPE_FLOAT_VECTOR = 9;
constexpr int8_t TYPE_SYMBOL_VECTOR = 11;
constexpr int8_t TYPE_TIMESTAMP_VECTOR = 12;
constexpr int8_t TYPE_TABLE = 98;
constexpr int8_t TYPE_DICT = 99;
constexpr int8_t TYPE_SYMBOL_ATOM = -11;

void appendU32LE(std::vector<uint8_t>& out, uint32_t v) {
    out.push_back(static_cast<uint8_t>(v & 0xFF));
    out.push_back(static_cast<uint8_t>((v >> 8) & 0xFF));
    out.push_back(static_cast<uint8_t>((v >> 16) & 0xFF));
    out.push_back(static_cast<uint8_t>((v >> 24) & 0xFF));
}
void appendI64LE(std::vector<uint8_t>& out, int64_t v) {
    auto u = static_cast<uint64_t>(v);
    for (int i = 0; i < 8; ++i) out.push_back(static_cast<uint8_t>((u >> (i * 8)) & 0xFF));
}
void appendF64LE(std::vector<uint8_t>& out, double v) {
    uint64_t bits;
    std::memcpy(&bits, &v, sizeof(bits));
    for (int i = 0; i < 8; ++i) out.push_back(static_cast<uint8_t>((bits >> (i * 8)) & 0xFF));
}
} // namespace

void Writer::writeVectorHeader(std::vector<uint8_t>& out, int8_t typeCode, uint32_t count) {
    out.push_back(static_cast<uint8_t>(typeCode));
    out.push_back(0); // attribute byte (0 = no attribute, e.g. not marked sorted/unique)
    appendU32LE(out, count);
}

void Writer::writeSymbolAtom(std::vector<uint8_t>& out, const std::string& s) {
    out.push_back(static_cast<uint8_t>(TYPE_SYMBOL_ATOM));
    out.insert(out.end(), s.begin(), s.end());
    out.push_back(0); // symbols are C strings on the wire - null-terminated, no length prefix
}

std::vector<uint8_t> Writer::symbolVector(const std::vector<std::string>& values) {
    std::vector<uint8_t> out;
    writeVectorHeader(out, TYPE_SYMBOL_VECTOR, static_cast<uint32_t>(values.size()));
    for (const auto& s : values) { out.insert(out.end(), s.begin(), s.end()); out.push_back(0); }
    return out;
}

std::vector<uint8_t> Writer::timestampVector(const std::vector<int64_t>& kdbEpochNs) {
    std::vector<uint8_t> out;
    writeVectorHeader(out, TYPE_TIMESTAMP_VECTOR, static_cast<uint32_t>(kdbEpochNs.size()));
    for (auto v : kdbEpochNs) appendI64LE(out, v);
    return out;
}

std::vector<uint8_t> Writer::floatVector(const std::vector<double>& values) {
    std::vector<uint8_t> out;
    writeVectorHeader(out, TYPE_FLOAT_VECTOR, static_cast<uint32_t>(values.size()));
    for (auto v : values) appendF64LE(out, v);
    return out;
}

std::vector<uint8_t> Writer::longVector(const std::vector<int64_t>& values) {
    std::vector<uint8_t> out;
    writeVectorHeader(out, TYPE_LONG_VECTOR, static_cast<uint32_t>(values.size()));
    for (auto v : values) appendI64LE(out, v);
    return out;
}

std::vector<uint8_t> Writer::table(const std::vector<std::string>& columnNames,
                                   const std::vector<std::vector<uint8_t>>& columnVectors) {
    std::vector<uint8_t> out;
    out.push_back(static_cast<uint8_t>(TYPE_TABLE));
    out.push_back(0); // table attribute byte

    // the dict: symbol-vector keys, general-list-of-vectors values
    out.push_back(static_cast<uint8_t>(TYPE_DICT));
    auto keyBytes = symbolVector(columnNames);
    out.insert(out.end(), keyBytes.begin(), keyBytes.end());

    out.push_back(static_cast<uint8_t>(TYPE_GENERAL_LIST));
    out.push_back(0);
    appendU32LE(out, static_cast<uint32_t>(columnVectors.size()));
    for (const auto& col : columnVectors) out.insert(out.end(), col.begin(), col.end());

    return out;
}

std::vector<uint8_t> Writer::asyncUpdMessage(const std::string& tableName, const std::vector<uint8_t>& tableBytes) {
    std::vector<uint8_t> body;
    body.push_back(static_cast<uint8_t>(TYPE_GENERAL_LIST));
    body.push_back(0);
    appendU32LE(body, 3); // (`.u.upd; tableName; tableValue)

    writeSymbolAtom(body, ".u.upd");
    writeSymbolAtom(body, tableName);
    body.insert(body.end(), tableBytes.begin(), tableBytes.end());

    std::vector<uint8_t> msg;
    msg.push_back(1); // byte0: 1 = little-endian encoding of the length field below
    msg.push_back(0); // byte1: message type 0 = async
    msg.push_back(0); msg.push_back(0); // reserved
    appendU32LE(msg, static_cast<uint32_t>(8 + body.size())); // total length INCLUDING this 8-byte header
    msg.insert(msg.end(), body.begin(), body.end());
    return msg;
}

} // namespace fh::kdb
