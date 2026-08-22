#include "test_framework.hpp"

int main() {
    int total = 0;
    for (auto& c : testing::registry()) {
        testing::g_currentTest = c.name;
        ++total;
        c.fn();
    }
    int failedAssertions = testing::g_failures;
    std::printf("%d test case(s) run, %d assertion failure(s)\n", total, failedAssertions);
    return failedAssertions == 0 ? 0 : 1;
}
