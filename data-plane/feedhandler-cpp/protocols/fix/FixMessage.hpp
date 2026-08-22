// FixMessage.hpp - FIX tag=value wire format: fields are `tag=value`
// separated by SOH (0x01). Kept as an ordered tag->value list (not a map)
// because FIX repeating groups and duplicate tags are order-sensitive and
// a std::map would silently collapse them - this codebase parses/builds
// well-formed non-repeating-group messages (Logon, Heartbeat, TestRequest,
// MarketDataSnapshotFullRefresh, MarketDataIncrementalRefresh, the core
// message types needed for a market-data session), which is the FIX
// subset needed here; a real production FIX engine's repeating-group and
// SBE-body ("FIX/FAST"/FIXT binary session) support is real, additional
// scope beyond what ships in this pass.
#pragma once

#include <string>
#include <vector>

namespace fh::fix {

constexpr char SOH = '\x01';

struct FixMessage {
    std::vector<std::pair<int, std::string>> fields;

    std::string get(int tag, const std::string& fallback = "") const {
        for (const auto& [t, v] : fields) if (t == tag) return v;
        return fallback;
    }
    bool has(int tag) const {
        for (const auto& [t, v] : fields) if (t == tag) return true;
        return false;
    }
    void set(int tag, const std::string& value) { fields.emplace_back(tag, value); }
};

// Common tags used by this codebase - not exhaustive.
namespace Tag {
constexpr int BeginString = 8, BodyLength = 9, MsgType = 35, SenderCompID = 49, TargetCompID = 56,
              MsgSeqNum = 34, SendingTime = 52, CheckSum = 10, EncryptMethod = 98, HeartBtInt = 108,
              Username = 553, Password = 554, Symbol = 55, Side = 54, LastPx = 31, LastQty = 32,
              TransactTime = 60, MDEntryType = 269, MDEntryPx = 270, MDEntrySize = 271;
}

namespace MsgType {
constexpr const char* Logon = "A";
constexpr const char* Heartbeat = "0";
constexpr const char* TestRequest = "1";
constexpr const char* Logout = "5";
constexpr const char* MarketDataSnapshotFullRefresh = "W";
constexpr const char* MarketDataIncrementalRefresh = "X";
}

// Parses one complete FIX message (including the leading "8=" and trailing
// checksum field) from `data`. Returns the byte length consumed, or 0 if
// `data` doesn't start with a well-formed "8=...|9=...|" header or the
// declared BodyLength runs past the available bytes (caller should wait
// for more bytes and retry, same contract as every other buffering
// decoder in this codebase).
size_t parseOne(const uint8_t* data, size_t length, FixMessage& out);

// Serializes a message, computing BodyLength (tag 9) and CheckSum (tag 10)
// correctly - the two fields a hand-built FIX message gets wrong most
// often if computed manually. `fields` should NOT include BeginString(8),
// BodyLength(9), or CheckSum(10) - those are added here.
std::string build(const std::string& beginString, const std::vector<std::pair<int, std::string>>& fields);

} // namespace fh::fix
