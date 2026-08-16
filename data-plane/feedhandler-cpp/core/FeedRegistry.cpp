#include "FeedRegistry.hpp"

#include "../venues/CmeMdp3VenueAdapter.hpp"
#include "../venues/GenericFixVenueAdapter.hpp"
#include "../venues/GenericWsJsonVenueAdapter.hpp"
#include "../venues/NasdaqItchVenueAdapter.hpp"

namespace fh {

FeedRegistry& FeedRegistry::instance() {
    static FeedRegistry registry;
    return registry;
}

void FeedRegistry::registerVenueAdapter(const std::string& key, VenueAdapterFactory factory) {
    venueAdapters_[key] = std::move(factory);
}

std::unique_ptr<IVenueAdapter> FeedRegistry::createVenueAdapter(const std::string& key) const {
    auto it = venueAdapters_.find(key);
    return it != venueAdapters_.end() ? it->second() : nullptr;
}

std::vector<std::string> FeedRegistry::registeredVenueAdapters() const {
    std::vector<std::string> keys;
    keys.reserve(venueAdapters_.size());
    for (const auto& [k, v] : venueAdapters_) keys.push_back(k);
    return keys;
}

void registerBuiltinVenueAdapters() {
    auto& reg = FeedRegistry::instance();

    // NasdaqItchVenueAdapter has nothing NASDAQ-specific baked in - it only
    // ever reads config_.provider/config_.feed (both passed in from
    // FeedConfig) for tagging, and interprets whatever ITCH-shaped bytes
    // land in DecodedMessage.payload regardless of which protocol decoder
    // produced them. That's true whether those bytes arrived via MoldUDP64
    // (NASDAQ, ASX - ASX Trade is explicitly ITCH-licensed from Nasdaq) or
    // SoupBinTCP (Borsa Istanbul publishes ITCH/OUCH over exactly this
    // framing per its own public technical resources - see
    // SoupBinTcpDecoder.hpp). "itch_style" is the venue-neutral name for
    // exactly the same adapter, registered a second time under the same
    // factory - not a second implementation.
    auto itchAdapterFactory = [] { return std::make_unique<NasdaqItchVenueAdapter>(); };
    reg.registerVenueAdapter("nasdaq_itch", itchAdapterFactory);
    reg.registerVenueAdapter("itch_style", itchAdapterFactory);

    // CmeMdp3VenueAdapter's DECODE MECHANICS (SBE message-header parsing,
    // field extraction against a schema) are venue-neutral SBE - what's
    // CME-specific is the illustrative example SCHEMA it ships with (see
    // SbeSchema.hpp's own header comment). "sbe_generic" is the same
    // adapter under a venue-neutral name for any other SBE-speaking venue
    // (Eurex EOBI, Deutsche Boerse/Xetra T7, HKEX OMD-C are all genuinely
    // SBE-encoded) - going live against any of them means swapping in
    // THAT venue's real schema, not writing a new adapter class.
    auto sbeAdapterFactory = [] { return std::make_unique<CmeMdp3VenueAdapter>(); };
    reg.registerVenueAdapter("cme_mdp3", sbeAdapterFactory);
    reg.registerVenueAdapter("sbe_generic", sbeAdapterFactory);

    reg.registerVenueAdapter("generic_fix", [] { return std::make_unique<GenericFixVenueAdapter>(); });
    reg.registerVenueAdapter("generic_wsjson", [] { return std::make_unique<GenericWsJsonVenueAdapter>(); });
}

} // namespace fh
