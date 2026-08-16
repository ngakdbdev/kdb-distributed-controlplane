// test_framework.hpp - a tiny self-contained test harness (no third-party
// test framework - one less dependency). Each TEST() registers itself via
// a static initializer; a single main() (see test_main.cpp) runs them all.
// Header-only with `inline` globals so every test_*.cpp translation unit
// shares the same registry without an ODR violation.
#pragma once

#include <cstdio>
#include <functional>
#include <string>
#include <vector>

namespace testing {
struct Case { std::string name; std::function<void()> fn; };
inline std::vector<Case>& registry() { static std::vector<Case> r; return r; }
struct Registrar { Registrar(std::string name, std::function<void()> fn) { registry().push_back({std::move(name), std::move(fn)}); } };
inline int g_failures = 0;
inline std::string g_currentTest;
} // namespace testing

#define TEST(name) \
    void test_##name(); \
    static testing::Registrar registrar_##name(#name, test_##name); \
    void test_##name()

#define CHECK(cond) \
    do { if (!(cond)) { std::fprintf(stderr, "  FAIL [%s] %s:%d: CHECK(%s)\n", \
        testing::g_currentTest.c_str(), __FILE__, __LINE__, #cond); ++testing::g_failures; } } while (0)

#define CHECK_EQ(a, b) \
    do { auto _a = (a); auto _b = (b); if (!(_a == _b)) { \
        std::fprintf(stderr, "  FAIL [%s] %s:%d: CHECK_EQ(%s, %s)\n", \
        testing::g_currentTest.c_str(), __FILE__, __LINE__, #a, #b); ++testing::g_failures; } } while (0)
