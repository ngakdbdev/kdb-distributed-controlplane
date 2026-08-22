#include "StatusServer.hpp"

#include <cstring>
#include <netinet/in.h>
#include <sys/socket.h>
#include <unistd.h>

namespace fh {

void StatusServer::start() {
    if (running_) return;
    listenSock_ = ::socket(AF_INET, SOCK_STREAM, 0);
    if (listenSock_ < 0) return;

    int reuse = 1;
    ::setsockopt(listenSock_, SOL_SOCKET, SO_REUSEADDR, &reuse, sizeof(reuse));

    sockaddr_in addr{};
    addr.sin_family = AF_INET;
    addr.sin_addr.s_addr = htonl(INADDR_ANY);
    addr.sin_port = htons(port_);
    if (::bind(listenSock_, reinterpret_cast<sockaddr*>(&addr), sizeof(addr)) < 0) {
        ::close(listenSock_);
        listenSock_ = -1;
        return;
    }
    if (::listen(listenSock_, 16) < 0) {
        ::close(listenSock_);
        listenSock_ = -1;
        return;
    }

    running_ = true;
    thread_ = std::thread([this] { run(); });
}

void StatusServer::run() {
    while (running_) {
        int client = ::accept(listenSock_, nullptr, nullptr);
        if (client < 0) {
            if (!running_) break;
            continue;
        }

        char buf[2048];
        ssize_t n = ::recv(client, buf, sizeof(buf) - 1, 0);
        if (n > 0) {
            buf[n] = '\0';
            // only real requirement: don't crash/hang on anything malformed
            // or not GET /status - reply the same JSON regardless of path,
            // since this process serves exactly one thing.
            std::string body = provider_ ? provider_() : "{}";
            std::string response = "HTTP/1.1 200 OK\r\n"
                                   "Content-Type: application/json\r\n"
                                   "Content-Length: " + std::to_string(body.size()) + "\r\n"
                                   "Connection: close\r\n\r\n" + body;
            ::send(client, response.data(), response.size(), 0);
        }
        ::close(client);
    }
}

void StatusServer::stop() {
    if (!running_) return;
    running_ = false;
    if (listenSock_ >= 0) {
        ::shutdown(listenSock_, SHUT_RDWR);
        ::close(listenSock_);
        listenSock_ = -1;
    }
    if (thread_.joinable()) thread_.join();
}

} // namespace fh
