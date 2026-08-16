// ConfigLoader.hpp - parses a FeedConfig from JSON text. This is the exact
// shape control-api's admin portal saves per activated feed
// (FeedHandlerInstance.config_json for non-secret params,
// FeedHandlerInstance.secrets_json - Fernet-encrypted at rest, decrypted
// only in memory here - for credentials) - see docs/feedhandler-admin.md
// for the full JSON shape. Kept in core/ rather than admin/ since it's
// pure data transformation with no I/O of its own - the engine's startup
// code fetches or reads the JSON text; this just turns it into the
// FeedConfig struct every transport/decoder/adapter already consumes.
#pragma once

#include <string>
#include "FeedConfig.hpp"

namespace fh {

// Returns true and fills `out` on success. `secretsJson`, if non-empty, is
// merged into out.secrets separately from `configJson`'s params - kept as
// two arguments (not one JSON blob) because the caller (main.cpp) reads
// them from two different sources with different sensitivity (the second
// is decrypted moments before this call and must never be logged/written
// back out anywhere).
bool loadFeedConfig(const std::string& configJson, const std::string& secretsJson, FeedConfig& out);

} // namespace fh
