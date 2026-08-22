#include "UdpTransport.hpp"

#include <cerrno>
#include <cstring>
#include <sstream>

#include <arpa/inet.h>
#include <netinet/in.h>
#include <sys/socket.h>
#include <unistd.h>

namespace fh {

std::string UdpTransport::describe() const {
    std::ostringstream os;
    os << "udp(" << (cfg_.multicastGroup.empty() ? "unicast" : ("multicast " + cfg_.multicastGroup))
       << ":" << cfg_.port << ")";
    return os.str();
}

void UdpTransport::start() {
    if (running_) return;
    lastError_.clear();

    sock_ = ::socket(AF_INET, SOCK_DGRAM, 0);
    if (sock_ < 0) {
        lastError_ = std::string("socket() failed: ") + std::strerror(errno);
        return;
    }

    int reuse = 1;
    ::setsockopt(sock_, SOL_SOCKET, SO_REUSEADDR, &reuse, sizeof(reuse));
    ::setsockopt(sock_, SOL_SOCKET, SO_RCVBUF, &cfg_.recvBufferBytes, sizeof(cfg_.recvBufferBytes));

    sockaddr_in addr{};
    addr.sin_family = AF_INET;
    addr.sin_port = htons(cfg_.port);
    addr.sin_addr.s_addr = htonl(INADDR_ANY);

    if (::bind(sock_, reinterpret_cast<sockaddr*>(&addr), sizeof(addr)) < 0) {
        lastError_ = std::string("bind() failed: ") + std::strerror(errno);
        ::close(sock_);
        sock_ = -1;
        return;
    }

    if (!cfg_.multicastGroup.empty()) {
        ip_mreq mreq{};
        if (::inet_pton(AF_INET, cfg_.multicastGroup.c_str(), &mreq.imr_multiaddr) != 1) {
            lastError_ = "invalid multicast group address: " + cfg_.multicastGroup;
            ::close(sock_);
            sock_ = -1;
            return;
        }
        if (!cfg_.bindInterface.empty()) {
            ::inet_pton(AF_INET, cfg_.bindInterface.c_str(), &mreq.imr_interface);
        } else {
            mreq.imr_interface.s_addr = htonl(INADDR_ANY);
        }
        if (::setsockopt(sock_, IPPROTO_IP, IP_ADD_MEMBERSHIP, &mreq, sizeof(mreq)) < 0) {
            lastError_ = std::string("IP_ADD_MEMBERSHIP failed: ") + std::strerror(errno);
            ::close(sock_);
            sock_ = -1;
            return;
        }
    }

    running_ = true;
    thread_ = std::thread([this] { run(); });
}

void UdpTransport::run() {
    std::vector<uint8_t> buf(65536);
    while (running_) {
        ssize_t n = ::recv(sock_, buf.data(), buf.size(), 0);
        if (n < 0) {
            if (errno == EINTR) continue;
            if (!running_) break; // stop() closed the socket - expected
            continue;             // transient recv error - keep the receive loop alive
        }
        uint64_t ts = nowNs();
        if (n > 0 && handler_) handler_(buf.data(), static_cast<size_t>(n), ts);
    }
}

void UdpTransport::stop() {
    if (!running_) return;
    running_ = false;
    if (sock_ >= 0) {
        ::shutdown(sock_, SHUT_RDWR);
        ::close(sock_);
        sock_ = -1;
    }
    if (thread_.joinable()) thread_.join();
}

} // namespace fh
