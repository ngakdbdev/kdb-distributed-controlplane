// FeedConfig.hpp - everything needed to stand up one feed instance:
// provider/feed identity, transport coordinates, protocol/decoder choice,
// and a free-form string map for whatever the specific transport/decoder/
// venue combination needs beyond the common fields (multicast group,
// FIX SenderCompID, WS URL, ...). This is the in-process twin of the JSON
// the admin portal saves per activated feed (control-api's
// FeedHandlerInstance.config_json) - see admin/ConfigLoader for the JSON
// <-> FeedConfig mapping.
#pragma once

#include <map>
#include <string>

namespace fh {

struct FeedConfig {
    std::string provider;      // "NASDAQ", "CME", "COINBASE", ...
    std::string feed;          // "TOTALVIEW_ITCH", "MDP3", "TRADES_WS", ...
    std::string environment = "production";
    bool enabled = false;

    std::string transportType;  // "udp_multicast" | "udp_unicast" | "tcp" | "websocket"
    std::string protocolType;   // "moldudp64" | "soupbintcp" | "sbe" | "fix" | "wsjson"
    std::string venueAdapter;   // registry key, e.g. "nasdaq_itch" | "cme_mdp3" | "generic_fix" | "generic_wsjson"

    bool recoveryEnabled = true;
    bool abFeedEnabled = false;

    // credential VALUES are never stored here in the control-api database
    // (see crypto.py's Fernet encryption + FeedHandlerInstance.secrets_json)
    // but by the time this struct is built in-process, the engine needs the
    // real values to actually connect - this is where they land, in memory
    // only, never logged, never serialized back out. See admin/ConfigLoader.
    std::map<std::string, std::string> params;   // non-secret knobs (interface, group, port, url, ...)
    std::map<std::string, std::string> secrets;  // decrypted credential values, in-memory only

    std::string get(const std::string& key, const std::string& fallback = "") const {
        auto it = params.find(key);
        return it != params.end() ? it->second : fallback;
    }
};

} // namespace fh
