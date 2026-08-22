// EventSink.hpp - the one interface every decoder/adapter pushes normalized
// events into. Kept abstract (not "just a std::function") so a sink can be
// a book builder, the kdb+ publisher, a unit test's capture vector, or a
// fan-out to several of those at once (see FanOutSink) - the decoder never
// knows or cares which.
#pragma once

#include <vector>
#include "Event.hpp"

namespace fh {

class EventSink {
public:
    virtual ~EventSink() = default;
    virtual void onEvent(const MarketEvent& event) = 0;
};

// Fans every event out to N sinks - e.g. the real kdb+ publisher AND a raw
// capture recorder, at the same time, without either needing to know about
// the other. See section 12/13 of the design: capture must never sit in the
// live decode path as a special case.
class FanOutSink : public EventSink {
public:
    void add(EventSink* sink) { sinks_.push_back(sink); }
    void onEvent(const MarketEvent& event) override {
        for (auto* s : sinks_) s->onEvent(event);
    }
private:
    std::vector<EventSink*> sinks_;
};

// A simple in-memory sink - the backbone of every decoder unit test in this
// codebase: decode known bytes, assert on what landed here.
class CapturingSink : public EventSink {
public:
    void onEvent(const MarketEvent& event) override { events.push_back(event); }
    std::vector<MarketEvent> events;
};

} // namespace fh
