// Json.hpp - a minimal, self-contained JSON value + parser. Used by the
// generic WebSocket+JSON protocol decoder (protocols/wsjson) to parse
// vendor market-data messages (Coinbase/Binance/Kraken-shaped feeds all
// send JSON). Deliberately hand-rolled rather than vendoring a third-party
// library: the subset of JSON needed here (objects, arrays, strings,
// numbers, bool, null - no comments/trailing commas/big-number precision
// concerns) is small and self-contained is one less external dependency
// in the build, consistent with Sha1Base64.hpp's same reasoning for the
// WebSocket handshake.
#pragma once

#include <map>
#include <memory>
#include <string>
#include <vector>

namespace fh::json {

enum class Type { Null, Bool, Number, String, Array, Object };

class Value {
public:
    Type type = Type::Null;
    bool boolValue = false;
    double numberValue = 0;
    std::string stringValue;
    std::vector<Value> arrayValue;
    std::map<std::string, Value> objectValue;

    bool isNull() const { return type == Type::Null; }
    bool isObject() const { return type == Type::Object; }
    bool isArray() const { return type == Type::Array; }

    const Value* find(const std::string& key) const {
        if (type != Type::Object) return nullptr;
        auto it = objectValue.find(key);
        return it != objectValue.end() ? &it->second : nullptr;
    }
    std::string asString(const std::string& fallback = "") const {
        if (type == Type::String) return stringValue;
        if (type == Type::Number) {
            // some vendors send numeric fields as JSON numbers, others as
            // strings (crypto feeds routinely send price/size as strings
            // to avoid float precision loss) - callers shouldn't have to
            // care which; asDouble() below has the same symmetry.
            char buf[64];
            snprintf(buf, sizeof(buf), "%g", numberValue);
            return buf;
        }
        return fallback;
    }
    double asDouble(double fallback = 0.0) const {
        if (type == Type::Number) return numberValue;
        if (type == Type::String) {
            try { return std::stod(stringValue); } catch (...) { return fallback; }
        }
        return fallback;
    }
};

// Returns true and fills `out` on success; false on any malformed input
// (never throws - a malformed message from a flaky feed must never crash
// the decode path, same principle as every other decoder in this
// codebase silently rejecting truncated/malformed bytes).
bool parse(const std::string& text, Value& out);

} // namespace fh::json
