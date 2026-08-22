#include "Json.hpp"
#include <cctype>
#include <cstdlib>

namespace fh::json {

namespace {

class Parser {
public:
    explicit Parser(const std::string& s) : s_(s) {}

    bool parseValue(Value& out) {
        skipWs();
        if (pos_ >= s_.size()) return false;
        char c = s_[pos_];
        if (c == '{') return parseObject(out);
        if (c == '[') return parseArray(out);
        if (c == '"') return parseString(out);
        if (c == 't' || c == 'f') return parseBool(out);
        if (c == 'n') return parseNull(out);
        if (c == '-' || std::isdigit(static_cast<unsigned char>(c))) return parseNumber(out);
        return false;
    }

private:
    const std::string& s_;
    size_t pos_ = 0;

    void skipWs() { while (pos_ < s_.size() && std::isspace(static_cast<unsigned char>(s_[pos_]))) ++pos_; }

    bool parseObject(Value& out) {
        out.type = Type::Object;
        ++pos_; // '{'
        skipWs();
        if (pos_ < s_.size() && s_[pos_] == '}') { ++pos_; return true; }
        while (true) {
            skipWs();
            Value keyVal;
            if (pos_ >= s_.size() || s_[pos_] != '"' || !parseString(keyVal)) return false;
            skipWs();
            if (pos_ >= s_.size() || s_[pos_] != ':') return false;
            ++pos_;
            Value v;
            if (!parseValue(v)) return false;
            out.objectValue[keyVal.stringValue] = std::move(v);
            skipWs();
            if (pos_ >= s_.size()) return false;
            if (s_[pos_] == ',') { ++pos_; continue; }
            if (s_[pos_] == '}') { ++pos_; return true; }
            return false;
        }
    }

    bool parseArray(Value& out) {
        out.type = Type::Array;
        ++pos_; // '['
        skipWs();
        if (pos_ < s_.size() && s_[pos_] == ']') { ++pos_; return true; }
        while (true) {
            Value v;
            if (!parseValue(v)) return false;
            out.arrayValue.push_back(std::move(v));
            skipWs();
            if (pos_ >= s_.size()) return false;
            if (s_[pos_] == ',') { ++pos_; continue; }
            if (s_[pos_] == ']') { ++pos_; return true; }
            return false;
        }
    }

    bool parseString(Value& out) {
        out.type = Type::String;
        ++pos_; // opening quote
        std::string result;
        while (pos_ < s_.size() && s_[pos_] != '"') {
            char c = s_[pos_];
            if (c == '\\' && pos_ + 1 < s_.size()) {
                char n = s_[pos_ + 1];
                switch (n) {
                    case 'n': result += '\n'; break;
                    case 't': result += '\t'; break;
                    case 'r': result += '\r'; break;
                    case '"': result += '"'; break;
                    case '\\': result += '\\'; break;
                    case '/': result += '/'; break;
                    default: result += n; break; // \uXXXX and others: pass through unescaped (sufficient for market-data field values)
                }
                pos_ += 2;
            } else {
                result += c;
                ++pos_;
            }
        }
        if (pos_ >= s_.size()) return false; // unterminated string
        ++pos_; // closing quote
        out.stringValue = std::move(result);
        return true;
    }

    bool parseBool(Value& out) {
        if (s_.compare(pos_, 4, "true") == 0) { out.type = Type::Bool; out.boolValue = true; pos_ += 4; return true; }
        if (s_.compare(pos_, 5, "false") == 0) { out.type = Type::Bool; out.boolValue = false; pos_ += 5; return true; }
        return false;
    }

    bool parseNull(Value& out) {
        if (s_.compare(pos_, 4, "null") == 0) { out.type = Type::Null; pos_ += 4; return true; }
        return false;
    }

    bool parseNumber(Value& out) {
        size_t start = pos_;
        if (pos_ < s_.size() && s_[pos_] == '-') ++pos_;
        while (pos_ < s_.size() && (std::isdigit(static_cast<unsigned char>(s_[pos_])) || s_[pos_] == '.' ||
                                    s_[pos_] == 'e' || s_[pos_] == 'E' || s_[pos_] == '+' || s_[pos_] == '-'))
            ++pos_;
        if (pos_ == start) return false;
        out.type = Type::Number;
        out.numberValue = std::strtod(s_.substr(start, pos_ - start).c_str(), nullptr);
        return true;
    }
};

} // namespace

bool parse(const std::string& text, Value& out) {
    Parser p(text);
    return p.parseValue(out);
}

} // namespace fh::json
