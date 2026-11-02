async function createLyraClient(site) {
  const response = await fetch("/auth/config?site=" + site, { cache: "no-store" });
  const config = await response.json();
  if (!response.ok) throw new Error(config.detail);
  const origin = new URL(config.redirectUri).origin;
  if (location.origin !== origin) {
    location.replace(origin + location.pathname);
    return;
  }
  const client = new msal.PublicClientApplication({
    auth: { clientId: config.clientId, authority: config.authority, redirectUri: config.redirectUri },
    cache: { cacheLocation: "sessionStorage" },
  });
  await client.initialize();
  return { client, scopes: [config.scope], redirectUri: config.redirectUri };
}
