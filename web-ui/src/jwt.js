// Client-side JWT payload decode - NOT verification (the browser has no way
// to check the signature without the server's secret, and doesn't need to:
// every real API call is re-checked server-side by require_platform_admin
// et al). This is purely so the UI can hide a tab a user can't use anyway -
// a role read this way must never be treated as a security boundary.
export function decodeToken(token) {
  try {
    const payload = token.split(".")[1];
    const json = atob(payload.replace(/-/g, "+").replace(/_/g, "/"));
    return JSON.parse(json);
  } catch {
    return null;
  }
}

export function roleFromToken(token) {
  return decodeToken(token)?.role || null;
}
