// KdbIpc.hpp - a from-scratch kdb+ IPC wire-format writer: enough of the
// publicly documented kdb+ IPC protocol (code.kx.com/q/kb/ipc/) to build
// an async `.u.upd[table;data]` message and the connection handshake, and
// nothing else. Deliberately independent of KX's own C API (k.h/c.o) -
// this repo never links or bundles KX code (see data-plane/docker/
// Dockerfile.kdb's own licensing note) - the IPC WIRE FORMAT itself isn't
// proprietary (qpython, PyQ, node-q and others all implement it
// independently the same way); this is this codebase's own from-scratch
// implementation of that public format, matching exactly what
// data-plane/feeds/feed_common.py's qpython-based publisher already sends
// (a real q TABLE, type 98h - see that file's own comment on why binary,
// not text).
//
// kdb+ timestamps are nanoseconds since 2000.01.01, NOT the Unix epoch -
// KDB_EPOCH_OFFSET_NS below is the (fixed, well-known) gap between the two.
#pragma once

#include <cstdint>
#include <string>
#include <vector>

namespace fh::kdb {

constexpr int64_t KDB_EPOCH_OFFSET_NS = 946684800000000000LL; // 2000-01-01 00:00:00 UTC, as Unix-epoch nanoseconds

inline int64_t toKdbTimestampNs(uint64_t unixEpochNs) {
    return static_cast<int64_t>(unixEpochNs) - KDB_EPOCH_OFFSET_NS;
}

// A minimal typed-column table builder covering exactly the two tables
// this platform's schema.q defines (trade, risk) - see feed_common.py's
// _TABLE_SPECS, which this mirrors column-for-column so the SAME
// tickerplant/.u.upd path handles both a Python feed's rows and this
// engine's without either side needing to know which produced a given
// batch.
class Writer {
public:
    // Appends bytes for one column vector of the given q type onto `out`;
    // callers assemble a full table by calling these in schema-column order
    // (see buildTradeTable below for the concrete shape).
    static std::vector<uint8_t> symbolVector(const std::vector<std::string>& values);
    static std::vector<uint8_t> timestampVector(const std::vector<int64_t>& kdbEpochNs);
    static std::vector<uint8_t> floatVector(const std::vector<double>& values);
    static std::vector<uint8_t> longVector(const std::vector<int64_t>& values);

    // Wraps column name/data pairs into a proper q table (type 98h: a
    // dict of symbol-vector column names -> general-list of column
    // vectors, each already serialized by the *Vector() helpers above).
    static std::vector<uint8_t> table(const std::vector<std::string>& columnNames,
                                      const std::vector<std::vector<uint8_t>>& columnVectors);

    // Builds the full IPC message body for `.u.upd[tableNameSymbol;
    // tableValue]` - a 3-element general list (function symbol; table
    // name symbol; table value) - and wraps it with the 8-byte async
    // message header. This is the ONLY entry point publishers actually
    // need; the rest of this class exists to build `tableBytes`.
    static std::vector<uint8_t> asyncUpdMessage(const std::string& tableName, const std::vector<uint8_t>& tableBytes);

private:
    static void writeSymbolAtom(std::vector<uint8_t>& out, const std::string& s);
    static void writeVectorHeader(std::vector<uint8_t>& out, int8_t typeCode, uint32_t count);
};

// The IPC connection handshake request bytes: "user:password\3\0" (3 =
// requested protocol capability - timestamp/timespan/uuid/compression-
// aware, what every modern kdb+/KDB-X build understands). The server
// replies with exactly 1 byte (its negotiated capability level) - callers
// using an async transport (see KdbPublisher) send this via the
// transport's own send() and treat the first byte received back as that
// reply, rather than a blocking recv() a raw-socket caller would use.
inline std::string handshakeRequest(const std::string& credentials) {
    std::string hs = credentials + "\x03";
    hs.push_back('\0');
    return hs;
}

} // namespace fh::kdb
