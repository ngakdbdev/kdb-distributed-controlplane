#include "ConfigLoader.hpp"
#include "Json.hpp"

namespace fh {

namespace {
void mergeStringMap(const json::Value& obj, std::map<std::string, std::string>& out) {
    if (!obj.isObject()) return;
    for (const auto& [k, v] : obj.objectValue) out[k] = v.asString();
}
} // namespace

bool loadFeedConfig(const std::string& configJson, const std::string& secretsJson, FeedConfig& out) {
    json::Value root;
    if (!json::parse(configJson, root) || !root.isObject()) return false;

    out.provider = root.find("provider") ? root.find("provider")->asString() : "";
    out.feed = root.find("feed") ? root.find("feed")->asString() : "";
    out.environment = root.find("environment") ? root.find("environment")->asString("production") : "production";
    if (const auto* v = root.find("enabled")) out.enabled = v->type == json::Type::Bool ? v->boolValue : out.enabled;

    if (const auto* t = root.find("transport")) {
        if (const auto* tt = t->find("type")) out.transportType = tt->asString();
        mergeStringMap(*t, out.params); // interface/group/port/host/path/... alongside "type"
    }
    if (const auto* p = root.find("protocol")) {
        if (const auto* pt = p->find("type")) out.protocolType = pt->asString();
    }
    if (const auto* v = root.find("venue_adapter")) out.venueAdapter = v->asString();

    if (const auto* r = root.find("recovery")) {
        if (const auto* e = r->find("enabled")) out.recoveryEnabled = e->boolValue;
    }
    if (const auto* ab = root.find("ab_feed_enabled")) out.abFeedEnabled = ab->boolValue;

    if (const auto* mapping = root.find("field_mapping")) mergeStringMap(*mapping, out.params);

    if (!secretsJson.empty()) {
        json::Value secretsRoot;
        if (json::parse(secretsJson, secretsRoot) && secretsRoot.isObject()) {
            mergeStringMap(secretsRoot, out.secrets);
        }
    }
    return true;
}

} // namespace fh
