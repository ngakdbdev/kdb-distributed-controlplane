# kx_license.sh - checks whether THIS deploy has a real way to get the KX
# q binary + license before spending time on `docker compose build`, without
# assuming local files are the only valid way to supply either - they
# aren't. See data-plane/docker/kdb-entrypoint.sh for the actual runtime
# resolution order this mirrors:
#   binary:  data-plane/docker/kdbx/<arch>/q or <arch>.zip (local, wins if
#            present) OR KX_INSTALL_SOURCE=kx-portal + KX_BEARER_TOKEN
#            (pulled at container start - .env.example's own DEFAULT)
#   licence: data-plane/docker/kdbx/kc.lic (local) OR KDB_LICENSE_B64
#            (inline, consumed directly by q) OR a KX_LICENSE_PATH pointing
#            somewhere else you've mounted it yourself
# A prior version of this check only ever tested for the two local files,
# so a deploy correctly configured for portal-pull + KDB_LICENSE_B64 (the
# .env.example default) failed here every time, before Docker even started -
# "missing kc.lic" on a box that was never supposed to need one.
# Sourced by deploy/{aws,azure,gcp}/04_deploy_stack.sh AFTER .env is
# confirmed to exist.
check_kx_binary_and_license() {
  local kx_dir="data-plane/docker/kdbx"
  local env_source env_token env_b64 env_lic_path
  env_source="$(grep -E '^KX_INSTALL_SOURCE=' .env 2>/dev/null | tail -1 | cut -d= -f2-)"
  env_token="$(grep -E '^KX_BEARER_TOKEN=' .env 2>/dev/null | tail -1 | cut -d= -f2-)"
  env_b64="$(grep -E '^KDB_LICENSE_B64=' .env 2>/dev/null | tail -1 | cut -d= -f2-)"
  env_lic_path="$(grep -E '^KX_LICENSE_PATH=' .env 2>/dev/null | tail -1 | cut -d= -f2-)"

  local have_local_bin=0
  { [ -f "$kx_dir/q" ] || ls "$kx_dir"/*.zip >/dev/null 2>&1 \
    || ls "$kx_dir"/l64*/q "$kx_dir"/l64arm*/q >/dev/null 2>&1; } && have_local_bin=1
  local have_portal_pull=0
  [ "$env_source" = "kx-portal" ] && [ -n "$env_token" ] && have_portal_pull=1

  local have_local_lic=0
  [ -f "$kx_dir/kc.lic" ] && have_local_lic=1
  local have_inline_lic=0
  [ -n "$env_b64" ] && have_inline_lic=1
  # a KX_LICENSE_PATH pointing anywhere other than the default in-container
  # path implies the license is mounted some other way (an override compose
  # file, a secrets-manager sidecar, ...) - this script can't verify that
  # mount actually exists, so it trusts the explicit override rather than
  # blocking a deploy that intentionally does its own thing here.
  local have_custom_lic_path=0
  [ -n "$env_lic_path" ] && [ "$env_lic_path" != "/kdbx/kc.lic" ] && have_custom_lic_path=1

  if [ "$have_local_bin" = 0 ] && [ "$have_portal_pull" = 0 ]; then
    echo "Missing KDB-X binary: no q/*.zip staged at $kx_dir/, and .env has no"
    echo "working portal-pull configured (KX_INSTALL_SOURCE=kx-portal needs"
    echo "KX_BEARER_TOKEN set too - currently KX_INSTALL_SOURCE=${env_source:-<unset>})."
    echo "Either download KDB-X Community Edition from the KX Developer Center and"
    echo "place the linux 'q' binary (or <arch>.zip) at $kx_dir/, or set both"
    echo "KX_INSTALL_SOURCE=kx-portal and KX_BEARER_TOKEN in .env to pull it"
    echo "automatically at container start."
    return 1
  fi
  if [ "$have_local_lic" = 0 ] && [ "$have_inline_lic" = 0 ] && [ "$have_custom_lic_path" = 0 ]; then
    echo "Missing KDB-X license: no kc.lic staged at $kx_dir/, KDB_LICENSE_B64 is"
    echo "empty in .env, and KX_LICENSE_PATH is unset/still the default."
    echo "Either place kc.lic at $kx_dir/, or set KDB_LICENSE_B64 (base64 license"
    echo "payload) or KX_LICENSE_PATH (pointing at a license mounted some other"
    echo "way) in .env."
    return 1
  fi
  return 0
}
