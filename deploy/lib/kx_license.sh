# kx_license.sh - checks whether THIS deploy has a real way to get the KX
# q binary + license before spending time on `docker compose build`. See
# data-plane/docker/kdb-entrypoint.sh for the actual runtime resolution:
#   binary:  ALWAYS pulled from the KX portal at container start using
#            KX_BEARER_TOKEN - there is no local-file fallback anymore (a
#            prior version of this repo let you stage a downloaded zip/
#            binary under data-plane/docker/kdbx/, which only ever worked
#            on the one machine a human had done that on by hand and left
#            every other target - a fresh clone, a fresh VM, a real cloud
#            deploy - with nothing to find; removed for exactly that reason).
#   licence: KDB_LICENSE_B64 (inline, base64) OR KX_LICENSE_PATH (a file
#            mounted some other way - a Kubernetes Secret, a secrets-manager
#            sidecar). No local-file default either, same reasoning.
# Sourced by deploy/{aws,azure,gcp}/04_deploy_stack.sh AFTER .env is
# confirmed to exist.
check_kx_binary_and_license() {
  local env_token env_b64 env_lic_path
  # `tr -d '\r'` guards against a .env saved with CRLF line endings (a
  # Windows text editor, or a checkout of this repo that predates
  # .gitattributes) - without it, a correctly-configured KX_BEARER_TOKEN
  # comes back with a trailing \r, `[ -n "$env_token" ]` still passes (any
  # non-empty string does), but the token curl actually sends inside the
  # container would carry the same \r and fail auth at the KX portal with a
  # confusing error far away from this check. Confirmed live on a
  # Windows-hosted deploy for the equivalent KX_INSTALL_SOURCE issue this
  # script used to check for.
  env_token="$(grep -E '^KX_BEARER_TOKEN=' .env 2>/dev/null | tail -1 | cut -d= -f2- | tr -d '\r')"
  env_b64="$(grep -E '^KDB_LICENSE_B64=' .env 2>/dev/null | tail -1 | cut -d= -f2- | tr -d '\r')"
  env_lic_path="$(grep -E '^KX_LICENSE_PATH=' .env 2>/dev/null | tail -1 | cut -d= -f2- | tr -d '\r')"

  if [ -z "$env_token" ]; then
    echo "Missing KX_BEARER_TOKEN in .env: the KX/q binary is pulled from the"
    echo "KX portal (https://portal.dl.kx.com) at container start - get a bearer"
    echo "token there and set KX_BEARER_TOKEN in .env before deploying."
    return 1
  fi
  if [ -z "$env_b64" ] && [ -z "$env_lic_path" ]; then
    echo "Missing KDB-X license in .env: set KDB_LICENSE_B64 (base64-encoded"
    echo "license payload, inline - the simplest path) or KX_LICENSE_PATH"
    echo "(pointing at a license file mounted some other way - a secrets-manager"
    echo "sidecar, a manually-mounted volume) before deploying."
    return 1
  fi
  return 0
}
