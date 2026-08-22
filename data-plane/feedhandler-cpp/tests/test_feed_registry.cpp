// test_feed_registry.cpp - covers the "all exchanges/protocols for future
// integration" registry expansion: NasdaqItchVenueAdapter and
// CmeMdp3VenueAdapter are venue-neutral (only ever read config_.provider/
// config_.feed), so "itch_style"/"sbe_generic" are the same classes
// registered under venue-neutral keys, not new implementations - this
// confirms both the neutral keys AND the original venue-specific keys
// still resolve, and that they're wired to real (non-null) adapters.
#include "test_framework.hpp"
#include "../core/FeedRegistry.hpp"

TEST(registry_has_all_builtin_keys) {
    fh::registerBuiltinVenueAdapters();
    auto keys = fh::FeedRegistry::instance().registeredVenueAdapters();
    auto has = [&](const char* k) {
        for (auto& x : keys) if (x == k) return true;
        return false;
    };
    CHECK(has("nasdaq_itch"));
    CHECK(has("itch_style"));
    CHECK(has("cme_mdp3"));
    CHECK(has("sbe_generic"));
    CHECK(has("generic_fix"));
    CHECK(has("generic_wsjson"));
}

TEST(itch_style_alias_produces_a_real_adapter) {
    fh::registerBuiltinVenueAdapters();
    auto a = fh::FeedRegistry::instance().createVenueAdapter("itch_style");
    CHECK(a != nullptr);
}

TEST(sbe_generic_alias_produces_a_real_adapter) {
    fh::registerBuiltinVenueAdapters();
    auto a = fh::FeedRegistry::instance().createVenueAdapter("sbe_generic");
    CHECK(a != nullptr);
}

TEST(unknown_key_returns_null_not_a_crash) {
    fh::registerBuiltinVenueAdapters();
    auto a = fh::FeedRegistry::instance().createVenueAdapter("no_such_venue_adapter");
    CHECK(a == nullptr);
}

TEST(itch_style_and_nasdaq_itch_are_independent_instances) {
    // same factory, but each createVenueAdapter() call must produce its
    // own instance - two feeds using the alias must not share state.
    fh::registerBuiltinVenueAdapters();
    auto a = fh::FeedRegistry::instance().createVenueAdapter("nasdaq_itch");
    auto b = fh::FeedRegistry::instance().createVenueAdapter("itch_style");
    CHECK(a != nullptr);
    CHECK(b != nullptr);
    CHECK(a.get() != b.get());
}
