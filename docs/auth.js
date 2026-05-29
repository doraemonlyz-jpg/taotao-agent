(function () {
  const SESSION_KEY = "taotao-docs-auth";
  const LOGIN_FILE = "login.html";
  const SESSION_TTL_MS = 7 * 24 * 60 * 60 * 1000;

  const CREDENTIALS = {
    usernameHash: "963c59e8f58bc8a52274b63f8354c4f399eb705c3e370b28439a6cb41c52b9e3",
    passwordHash: "49a92ee809c2dbf9e3890bffcdcda00e9fd8a7e87df6a1ccb0f83be476b5957d",
  };

  function isLoginPage() {
    return window.location.pathname.split("/").pop() === LOGIN_FILE;
  }

  function loginUrl() {
    return new URL(LOGIN_FILE, window.location.href);
  }

  function safeNextUrl(rawNext) {
    if (!rawNext) return new URL("index.html", window.location.href).href;

    try {
      const next = new URL(rawNext, window.location.href);
      if (next.origin !== window.location.origin) {
        return new URL("index.html", window.location.href).href;
      }
      if (next.pathname.endsWith(`/${LOGIN_FILE}`)) {
        return new URL("index.html", window.location.href).href;
      }
      return next.href;
    } catch (_error) {
      return new URL("index.html", window.location.href).href;
    }
  }

  function readSession() {
    try {
      const raw = window.localStorage.getItem(SESSION_KEY);
      if (!raw) return null;
      return JSON.parse(raw);
    } catch (_error) {
      return null;
    }
  }

  function isAuthenticated() {
    const session = readSession();
    return Boolean(session && session.ok === true && session.expiresAt > Date.now());
  }

  function rememberLogin() {
    window.localStorage.setItem(
      SESSION_KEY,
      JSON.stringify({ ok: true, expiresAt: Date.now() + SESSION_TTL_MS })
    );
  }

  async function sha256(value) {
    if (!window.crypto || !window.crypto.subtle) {
      throw new Error("当前浏览器环境不支持安全登录校验，请使用 HTTPS 或 localhost 访问。");
    }

    const data = new TextEncoder().encode(value);
    const digest = await window.crypto.subtle.digest("SHA-256", data);
    return Array.from(new Uint8Array(digest))
      .map((byte) => byte.toString(16).padStart(2, "0"))
      .join("");
  }

  async function login(username, password) {
    const [usernameHash, passwordHash] = await Promise.all([
      sha256(username.trim()),
      sha256(password),
    ]);

    const ok =
      usernameHash === CREDENTIALS.usernameHash &&
      passwordHash === CREDENTIALS.passwordHash;

    if (ok) rememberLogin();
    return ok;
  }

  function logout() {
    window.localStorage.removeItem(SESSION_KEY);
    window.location.href = loginUrl().href;
  }

  function requireAuth() {
    if (isAuthenticated()) return;

    const nextLogin = loginUrl();
    nextLogin.searchParams.set("next", window.location.href);
    window.location.replace(nextLogin.href);
  }

  window.taotaoAuth = {
    isAuthenticated,
    login,
    logout,
    safeNextUrl,
    requireAuth,
  };

  if (!isLoginPage()) {
    requireAuth();
  }
})();
