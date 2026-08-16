// StatusServer.hpp - a minimal embedded HTTP server exposing one GET
// endpoint (/status) returning JSON, so control-api's platform health
// rollup (app/routers/platform_health.py) can poll this engine the same
// way it already polls the gateway/tickerplant q processes' own health
// endpoints - one more component in the SAME unified health view, not a
// separate thing an operator has to check differently. Deliberately not a
// general-purpose HTTP server (no routing, no other verbs, no static
// files) - a feed handler doesn't need one, and a bigger dependency here
// would be scope creep against what this is actually for.
#pragma once

#include <atomic>
#include <functional>
#include <string>
#include <thread>

namespace fh {

// Called once per request; returns the JSON body to send back with a 200.
using StatusJsonProvider = std::function<std::string()>;

class StatusServer {
public:
    StatusServer(uint16_t port, StatusJsonProvider provider) : port_(port), provider_(std::move(provider)) {}
    ~StatusServer() { stop(); }

    void start();
    void stop();

private:
    void run();

    uint16_t port_;
    StatusJsonProvider provider_;
    std::atomic<bool> running_{false};
    std::thread thread_;
    int listenSock_ = -1;
};

} // namespace fh
