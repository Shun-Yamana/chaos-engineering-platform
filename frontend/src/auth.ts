const COGNITO_DOMAIN = import.meta.env.VITE_COGNITO_DOMAIN as string
const CLIENT_ID = import.meta.env.VITE_COGNITO_CLIENT_ID as string
const DEV_AUTH = import.meta.env.VITE_DEV_AUTH === "skip"

function redirectUri(): string {
  return window.location.origin
}

function base64UrlEncode(buf: Uint8Array): string {
  return btoa(String.fromCharCode(...buf))
    .replace(/\+/g, "-")
    .replace(/\//g, "_")
    .replace(/=/g, "")
}

function generateVerifier(): string {
  const buf = new Uint8Array(32)
  crypto.getRandomValues(buf)
  return base64UrlEncode(buf)
}

async function codeChallenge(verifier: string): Promise<string> {
  const buf = await crypto.subtle.digest("SHA-256", new TextEncoder().encode(verifier))
  return base64UrlEncode(new Uint8Array(buf))
}

export async function login(): Promise<void> {
  if (DEV_AUTH) {
    sessionStorage.setItem("access_token", "dev-token")
    window.location.reload()
    return
  }

  const verifier = generateVerifier()
  sessionStorage.setItem("pkce_verifier", verifier)

  const params = new URLSearchParams({
    response_type: "code",
    client_id: CLIENT_ID,
    redirect_uri: redirectUri(),
    scope: "openid email",
    code_challenge_method: "S256",
    code_challenge: await codeChallenge(verifier),
  })

  window.location.href = `${COGNITO_DOMAIN}/oauth2/authorize?${params}`
}

export async function handleCallback(code: string): Promise<void> {
  const verifier = sessionStorage.getItem("pkce_verifier")
  if (!verifier) throw new Error("PKCE verifier missing")

  const res = await fetch(`${COGNITO_DOMAIN}/oauth2/token`, {
    method: "POST",
    headers: { "Content-Type": "application/x-www-form-urlencoded" },
    body: new URLSearchParams({
      grant_type: "authorization_code",
      client_id: CLIENT_ID,
      redirect_uri: redirectUri(),
      code,
      code_verifier: verifier,
    }),
  })

  if (!res.ok) throw new Error("Token exchange failed")

  const { access_token, id_token } = await res.json()
  sessionStorage.setItem("access_token", access_token)
  sessionStorage.setItem("id_token", id_token)
  sessionStorage.removeItem("pkce_verifier")
}

export function getToken(): string | null {
  if (DEV_AUTH) return "dev-token"
  return sessionStorage.getItem("id_token")
}

export function isAuthenticated(): boolean {
  if (DEV_AUTH) return true
  return !!getToken()
}

export function signOut(): void {
  if (DEV_AUTH) {
    sessionStorage.clear()
    window.location.reload()
    return
  }

  sessionStorage.clear()
  const params = new URLSearchParams({
    client_id: CLIENT_ID,
    logout_uri: window.location.origin,
  })
  window.location.href = `${COGNITO_DOMAIN}/logout?${params}`
}
