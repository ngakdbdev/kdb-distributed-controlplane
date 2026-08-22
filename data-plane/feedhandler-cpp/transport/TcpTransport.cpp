#include "TcpTransport.hpp"

#include <cerrno>
#include <chrono>
#include <cstring>
#include <sstream>

#include <arpa/inet.h>
#include <netdb.h>
#include <netinet/in.h>
#include <netinet/tcp.h>
#include <sys/socket.h>
#include <unistd.h>

namespace fh {

std::string TcpTransport::describe() const {
    std::ostringstream os;
    os << "tcp(" << cfg_.host << ":" << cfg_.port << ")";
    return os.str();
}

bool TcpTransport::connectOnce() {
    addrinfo hints{};
    hints.ai_family = AF_INET;
    hints.ai_socktype = SOCK_STREAM;
    addrinfo* res = nullptr;
    std::string portStr = std::to_string(cfg_.port);
    if (::getaddrinfo(cfg_.host.c_str(), portStr.c_str(), &hints, &res) != 0 || res == nullptr) {
        return false;
    }

    int s = ::socket(res->ai_family, res->ai_socktype, res->ai_protocol);
    if (s < 0) {
        ::freeaddrinfo(res);
        return false;
    }
    int one = 1;
    ::setsockopt(s, IPPROTO_TCP, TCP_NODELAY, &one, sizeof(one)); // low-latency: no Nagle

    bool ok = ::connect(s, res->ai_addr, res->ai_addrlen) == 0;
    ::freeaddrinfo(res);
    if (!ok) {
        ::close(s);
        return false;
    }
    sock_ = s;
    return true;
}

void TcpTransport::run() {
    std::vector<uint8_t> buf(65536);
    while (running_) {
        if (!connectOnce()) {
            std::this_thread::sleep_for(std::chrono::milliseconds(cfg_.reconnectDelayMs));
            continue;
        }
        connected_ = true;
        reconnectCount_++;
        if (onConnected_) onConnected_();
        while (running_) {
            ssize_t n = ::recv(sock_, buf.data(), buf.size(), 0);
            if (n <= 0) break; // 0 = orderly close, <0 = error - either way, reconnect
            if (handler_) handler_(buf.data(), static_cast<size_t>(n), nowNs());
        }
        connected_ = false;
        if (sock_ >= 0) { ::close(sock_); sock_ = -1; }
        if (running_) std::this_thread::sleep_for(std::chrono::milliseconds(cfg_.reconnectDelayMs));
    }
}

void TcpTransport::start() {
    if (running_) return;
    running_ = true;
    thread_ = std::thread([this] { run(); });
}

void TcpTransport::stop() {
    if (!running_) return;
    running_ = false;
    if (sock_ >= 0) {
        ::shutdown(sock_, SHUT_RDWR);
        ::close(sock_);
        sock_ = -1;
    }
    if (thread_.joinable()) thread_.join();
}

bool TcpTransport::send(const uint8_t* data, size_t length) {
    if (!connected_ || sock_ < 0) return false;
    size_t sent = 0;
    while (sent < length) {
        ssize_t n = ::send(sock_, data + sent, length - sent, 0);
        if (n <= 0) return false;
        sent += static_cast<size_t>(n);
    }
    return true;
}

} // namespace fh
