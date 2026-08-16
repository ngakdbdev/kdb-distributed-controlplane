// CmeMdp3VenueAdapter.hpp - interprets schema-decoded SBE messages (from
// SequencedSbePacketDecoder + SbeDecoder) into normalized MarketEvents,
// using an illustrative example schema (two templates: Trade and
// AddOrder) shaped like a real MDP3-style feed. See SbeSchema.hpp's header
// comment: this is NOT CME's actual current MDP3 schema - that's CME's
// own published spec, to be sourced from them and loaded here (or a
// config-driven schema file) before this adapter is pointed at a real CME
// feed. What IS real and fully verifiable today: the schema-driven
// decode mechanics themselves, exercised end-to-end by sim/CmeMdp3Simulator
// generating packets against this exact example schema.
#pragma once

#include "../core/VenueAdapter.hpp"
#include "../protocols/sbe/SbeDecoder.hpp"
#include "../protocols/sbe/SbeSchema.hpp"

namespace fh {

// Illustrative template IDs - see file header.
namespace CmeMdp3TemplateId {
constexpr uint16_t Trade = 1;
constexpr uint16_t AddOrder = 2;
}

sbe::Schema buildExampleMdp3Schema();

class CmeMdp3VenueAdapter : public IVenueAdapter {
public:
    CmeMdp3VenueAdapter() : schema_(buildExampleMdp3Schema()), decoder_(schema_) {}

    void configure(const FeedConfig& config, EventSink& sink) override;
    void onMessage(const DecodedMessage& msg) override;
    std::string name() const override { return "cme_mdp3"; }

private:
    sbe::Schema schema_;
    sbe::SbeDecoder decoder_;
    EventSink* sink_ = nullptr;
    FeedConfig config_;
};

} // namespace fh
