// FeedRegistry.hpp - the provider registry (design section 14): venue
// adapters are looked up by a string key from FeedConfig.venueAdapter, not
// selected via a switch statement scattered through the engine. Adding a
// new venue becomes: implement IVenueAdapter, register it here under a
// key, add a default config entry (config/providers/) - never touching
// the engine's own orchestration code. This is the single seam that makes
// "buyers won't need external solutions for different data/protocols"
// actually true: any venue whose protocol family this codebase already
// speaks (MoldUDP64+ITCH, SBE, FIX, WebSocket+JSON) becomes reachable by
// adding data + a small adapter, not new plumbing.
#pragma once

#include <functional>
#include <map>
#include <memory>
#include <string>

#include "VenueAdapter.hpp"

namespace fh {

using VenueAdapterFactory = std::function<std::unique_ptr<IVenueAdapter>()>;

class FeedRegistry {
public:
    static FeedRegistry& instance();

    void registerVenueAdapter(const std::string& key, VenueAdapterFactory factory);
    std::unique_ptr<IVenueAdapter> createVenueAdapter(const std::string& key) const;
    std::vector<std::string> registeredVenueAdapters() const;

private:
    std::map<std::string, VenueAdapterFactory> venueAdapters_;
};

// Registers every venue adapter this codebase ships with. Idempotent -
// safe to call more than once (e.g. once from main(), once from a test
// binary's own setup) since it just re-inserts the same factories.
void registerBuiltinVenueAdapters();

} // namespace fh
