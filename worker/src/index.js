/**
 * PyMail license server (Cloudflare Worker).
 *
 * Endpoints:
 *   POST /register
 *     Body: {machine_id, email, name, hostname, os_user, version}
 *     Effect: registers (or refreshes) this user, issues a default
 *             trial license, returns the signed license string.
 *
 *   POST /verify
 *     Body: {license_id}
 *     Returns: {valid: bool, expires_at, status: 'active'|'revoked'|'expired'}
 *     Used by the client periodically to refresh license state without
 *     re-registering.
 *
 *   POST /admin/list      (admin auth required)
 *     Returns: array of all users, statuses, and metadata
 *
 *   POST /admin/extend    (admin auth required)
 *     Body: {license_id, days}      // days=0 = perpetual
 *     Effect: bumps expires_at, returns updated user record
 *
 *   POST /admin/revoke    (admin auth required)
 *     Body: {license_id, reason}
 *
 *   POST /admin/restore   (admin auth required)
 *     Body: {license_id}
 *
 *   POST /admin/push-version  (admin auth required)
 *     Body: {version}          // e.g. "1.2.38"
 *     Effect: sets allowed_version on every active user record.
 *             Clients only auto-update when their allowed_version > current.
 *
 * Storage layout in R2 bucket:
 *     users.json            { users: [...] }   - canonical user list
 *     revoked.json          { revoked: [...] } - existing blacklist (kept for compat)
 *
 * Auth model:
 *     - /register and /verify are PUBLIC (any PyMail client can call)
 *     - /admin/* requires header "X-Admin-Token" matching ADMIN_TOKEN secret
 */

const TRIAL_DAYS = 30;

export default {
  async fetch(request, env, ctx) {
    const url = new URL(request.url);
    const path = url.pathname;

    if (request.method === "OPTIONS") {
      return cors(new Response(null, { status: 204 }));
    }

    try {
      if (path === "/register" && request.method === "POST") {
        return cors(await handleRegister(request, env));
      }
      if (path === "/verify" && request.method === "POST") {
        return cors(await handleVerify(request, env));
      }
      if (path.startsWith("/admin/")) {
        const ok = await checkAdmin(request, env);
        if (!ok) {
          return cors(json({ error: "Forbidden" }, 403));
        }
        if (path === "/admin/list" && request.method === "POST") {
          return cors(await handleAdminList(env));
        }
        if (path === "/admin/extend" && request.method === "POST") {
          return cors(await handleAdminExtend(request, env));
        }
        if (path === "/admin/revoke" && request.method === "POST") {
          return cors(await handleAdminRevoke(request, env));
        }
        if (path === "/admin/restore" && request.method === "POST") {
          return cors(await handleAdminRestore(request, env));
        }
        if (path === "/admin/import" && request.method === "POST") {
          return cors(await handleAdminImport(request, env));
        }
        if (path === "/admin/delete" && request.method === "POST") {
          return cors(await handleAdminDelete(request, env));
        }
        if (path === "/admin/push-version" && request.method === "POST") {
          return cors(await handleAdminPushVersion(request, env));
        }
        if (path === "/admin/update-user" && request.method === "POST") {
          return cors(await handleAdminUpdateUser(request, env));
        }
        if (path === "/admin/rebind" && request.method === "POST") {
          return cors(await handleAdminRebind(request, env));
        }
        if (path === "/admin/generate" && request.method === "POST") {
          return cors(await handleAdminGenerate(request, env));
        }
      }
      return cors(json({ error: "Not found", path }, 404));
    } catch (e) {
      return cors(json({ error: String((e && e.message) || e) }, 500));
    }
  },
};

// ---------- CORS / JSON helpers ----------
function cors(resp) {
  const h = new Headers(resp.headers);
  h.set("Access-Control-Allow-Origin", "*");
  h.set("Access-Control-Allow-Methods", "POST, OPTIONS");
  h.set("Access-Control-Allow-Headers", "Content-Type, X-Admin-Token");
  return new Response(resp.body, { status: resp.status, headers: h });
}
function json(obj, status = 200) {
  return new Response(JSON.stringify(obj), {
    status,
    headers: { "Content-Type": "application/json" },
  });
}

async function checkAdmin(request, env) {
  const token = request.headers.get("X-Admin-Token") || "";
  if (!env.ADMIN_TOKEN) return false;
  return token === env.ADMIN_TOKEN;
}

// True if this user record belongs to the admin/developer. Admins must always
// track the latest manifest version, so they are excluded from "push version
// to all users".
//
// Identification is by the trusted email allow-list ONLY. We deliberately do
// NOT inspect user.note here: the Worker writes the word "admin" into ordinary
// users' notes itself ("issued by admin", "extended by admin to ...",
// "re-bound to new device by admin ..."), so a note substring match would
// wrongly classify normal users as admins and skip them in push-version,
// leaving their allowed_version stuck at "(all)".
function isAdminUser(user, env) {
  if (!user) return false;
  // Fall back to the same emails the client hardcodes in _is_admin() so this
  // works even if the ADMIN_EMAILS secret isn't set.
  const configured = String(env.ADMIN_EMAILS || "")
    .split(",")
    .map((e) => e.trim().toLowerCase())
    .filter(Boolean);
  const adminEmails = configured.length
    ? configured
    : ["khatar@intra.tunasgroup.com", "khatarmalayki21@gmail.com"];
  const email = String(user.email || "").trim().toLowerCase();
  return !!email && adminEmails.includes(email);
}

// ---------- Storage helpers (R2) ----------
async function loadUsers(env) {
  try {
    const obj = await env.BUCKET.get("users.json");
    if (!obj) return { users: [] };
    const text = await obj.text();
    return JSON.parse(text);
  } catch {
    return { users: [] };
  }
}
async function saveUsers(env, data) {
  await env.BUCKET.put("users.json", JSON.stringify(data, null, 2), {
    httpMetadata: { contentType: "application/json" },
  });
}
async function loadRevoked(env) {
  try {
    const obj = await env.BUCKET.get("revoked.json");
    if (!obj) return { revoked: [], notes: {} };
    return JSON.parse(await obj.text());
  } catch {
    return { revoked: [], notes: {} };
  }
}
async function saveRevoked(env, data) {
  await env.BUCKET.put("revoked.json", JSON.stringify(data, null, 2), {
    httpMetadata: { contentType: "application/json" },
  });
}

// ---------- Ed25519 signing ----------
async function signPayload(payload, env) {
  // Private key is stored as a base64 PKCS8 string in env.PRIVATE_KEY_PKCS8
  // Decode and import as a CryptoKey.
  const keyB64 = env.PRIVATE_KEY_PKCS8;
  if (!keyB64) throw new Error("PRIVATE_KEY_PKCS8 secret is not set");
  const keyBytes = base64Decode(keyB64);
  const key = await crypto.subtle.importKey(
    "pkcs8",
    keyBytes,
    { name: "Ed25519" },
    false,
    ["sign"],
  );
  // Canonical JSON (sorted keys, no whitespace) — same as Python signer
  const canonical = canonicalJson(payload);
  const sigBuf = await crypto.subtle.sign("Ed25519", key, canonical);
  return base64Encode(new Uint8Array(sigBuf));
}

function canonicalJson(obj) {
  // Sort keys recursively, no spaces
  const enc = new TextEncoder();
  const text = JSON.stringify(obj, sortKeys(obj));
  return enc.encode(text);
}
function sortKeys(obj) {
  // Replacer function for JSON.stringify to enforce sorted keys at every
  // object level, matching Python's json.dumps(sort_keys=True).
  if (obj === null || typeof obj !== "object") return undefined;
  const keys = Object.keys(obj).sort();
  return (key, value) => {
    if (typeof value !== "object" || value === null || Array.isArray(value)) {
      return value;
    }
    const sorted = {};
    for (const k of Object.keys(value).sort()) sorted[k] = value[k];
    return sorted;
  };
}

function base64Encode(bytes) {
  let bin = "";
  for (let i = 0; i < bytes.length; i++) bin += String.fromCharCode(bytes[i]);
  return btoa(bin);
}
function base64Decode(s) {
  const bin = atob(s.replace(/\s+/g, ""));
  const out = new Uint8Array(bin.length);
  for (let i = 0; i < bin.length; i++) out[i] = bin.charCodeAt(i);
  return out;
}

function makeLicenseKey(payload, signature) {
  const wrapper = { payload, signature };
  const text = JSON.stringify(wrapper);
  return base64Encode(new TextEncoder().encode(text));
}

function newLicenseId() {
  // 11-char URL-safe random ID, matching Python admin's secrets.token_urlsafe(8)
  const bytes = new Uint8Array(8);
  crypto.getRandomValues(bytes);
  let s = base64Encode(bytes).replace(/\+/g, "-").replace(/\//g, "_");
  return s.replace(/=+$/, "");
}

// ---------- Endpoints ----------

async function handleRegister(request, env) {
  const body = await request.json().catch(() => ({}));
  const machineId = String(body.machine_id || "").trim();
  const email = String(body.email || "")
    .trim()
    .toLowerCase();
  if (!machineId || !email || !email.includes("@")) {
    return json({ error: "machine_id and email are required" }, 400);
  }

  const data = await loadUsers(env);
  const trialDays = parseInt(env.TRIAL_DAYS_DEFAULT || String(TRIAL_DAYS), 10);
  const now = new Date();

  // Find existing record by machine_id (1 license per machine)
  let user = data.users.find((u) => u.machine_id === machineId);

  if (!user) {
    // Brand-new machine — issue trial
    const expiresAt = new Date(now.getTime() + trialDays * 86400000);
    user = {
      license_id: newLicenseId(),
      machine_id: machineId,
      email,
      name: String(body.name || "").trim(),
      hostname: String(body.hostname || "").trim(),
      os_user: String(body.os_user || "").trim(),
      version: String(body.version || "").trim(),
      issued_at: now.toISOString(),
      expires_at: expiresAt.toISOString(),
      first_seen: now.toISOString(),
      last_seen: now.toISOString(),
      status: "active",
      note: "auto-trial on first run",
    };
    data.users.push(user);
  } else {
    // Returning user — update metadata, do NOT change expiry
    user.last_seen = now.toISOString();
    if (body.email) user.email = email;
    if (body.name) user.name = String(body.name).trim();
    if (body.hostname) user.hostname = String(body.hostname).trim();
    if (body.os_user) user.os_user = String(body.os_user).trim();
    if (body.version) user.version = String(body.version).trim();
  }

  await saveUsers(env, data);

  // Build & sign the license payload (same shape as the Python admin issuer)
  const payload = {
    license_id: user.license_id,
    name: user.name || "",
    email: user.email,
    issued_at: user.issued_at,
    expires_at: user.expires_at || "",
    machine_id_hash: user.machine_id, // already hashed client-side
    note: user.note || "",
  };
  const signature = await signPayload(payload, env);
  const licenseKey = makeLicenseKey(payload, signature);

  return json({
    license_id: user.license_id,
    license_key: licenseKey,
    expires_at: user.expires_at,
    is_new: !!body.is_new_user,
  });
}

async function handleVerify(request, env) {
  const body = await request.json().catch(() => ({}));
  const licenseId = String(body.license_id || "").trim();
  if (!licenseId) return json({ valid: false, error: "no license_id" }, 400);

  const [data, revoked] = await Promise.all([loadUsers(env), loadRevoked(env)]);
  const user = data.users.find((u) => u.license_id === licenseId);
  if (!user) return json({ valid: false, status: "not_found" });
  if ((revoked.revoked || []).includes(licenseId)) {
    return json({
      valid: false,
      status: "revoked",
      expires_at: user.expires_at,
    });
  }
  if (user.expires_at) {
    const exp = new Date(user.expires_at);
    if (exp < new Date()) {
      return json({
        valid: false,
        status: "expired",
        expires_at: user.expires_at,
      });
    }
  }
  // Touch last_seen and update version if provided
  user.last_seen = new Date().toISOString();
  if (body.version) {
    user.version = String(body.version).trim();
  }
  await saveUsers(env, data);
  return json({
    valid: true,
    status: "active",
    expires_at: user.expires_at,
    // Admins always track the latest manifest version, so never hand them
    // an allowed_version pin — this lets a previously-pinned admin install
    // recover and auto-update on the next verify poll, no re-push needed.
    allowed_version: isAdminUser(user, env) ? null : (user.allowed_version || null),
  });
}

async function handleAdminList(env) {
  const [data, revoked] = await Promise.all([loadUsers(env), loadRevoked(env)]);
  const revSet = new Set(revoked.revoked || []);
  const users = data.users.map((u) => {
    const expired = u.expires_at && new Date(u.expires_at) < new Date();
    return {
      ...u,
      status: revSet.has(u.license_id)
        ? "revoked"
        : expired
          ? "expired"
          : "active",
    };
  });
  return json({ users });
}

async function handleAdminExtend(request, env) {
  const body = await request.json().catch(() => ({}));
  const licenseId = String(body.license_id || "").trim();
  const days = parseInt(body.days, 10);
  if (!licenseId) return json({ error: "license_id required" }, 400);

  const data = await loadUsers(env);
  const user = data.users.find((u) => u.license_id === licenseId);
  if (!user) return json({ error: "not found" }, 404);

  if (Number.isFinite(days) && days > 0) {
    // Extend from now (not from old expiry, so user always gets a fresh window)
    const newExp = new Date(Date.now() + days * 86400000);
    user.expires_at = newExp.toISOString();
  } else if (days === 0) {
    user.expires_at = ""; // perpetual
  }
  user.note = `extended by admin to ${user.expires_at || "perpetual"}`;
  await saveUsers(env, data);

  // Regenerate signed license key so the next /verify brings the new expiry
  const payload = {
    license_id: user.license_id,
    name: user.name || "",
    email: user.email,
    issued_at: user.issued_at,
    expires_at: user.expires_at || "",
    machine_id_hash: user.machine_id,
    note: user.note || "",
  };
  const signature = await signPayload(payload, env);
  return json({
    license_id: user.license_id,
    license_key: makeLicenseKey(payload, signature),
    expires_at: user.expires_at,
  });
}

async function handleAdminRevoke(request, env) {
  const body = await request.json().catch(() => ({}));
  const licenseId = String(body.license_id || "").trim();
  if (!licenseId) return json({ error: "license_id required" }, 400);
  const data = await loadRevoked(env);
  const list = data.revoked || [];
  if (!list.includes(licenseId)) list.push(licenseId);
  data.revoked = list;
  data.updated_at = new Date().toISOString();
  data.notes = data.notes || {};
  data.notes[licenseId] = body.reason || "";
  await saveRevoked(env, data);
  return json({ ok: true });
}

async function handleAdminRestore(request, env) {
  const body = await request.json().catch(() => ({}));
  const licenseId = String(body.license_id || "").trim();
  if (!licenseId) return json({ error: "license_id required" }, 400);
  const data = await loadRevoked(env);
  data.revoked = (data.revoked || []).filter((x) => x !== licenseId);
  data.updated_at = new Date().toISOString();
  if (data.notes) delete data.notes[licenseId];
  await saveRevoked(env, data);
  return json({ ok: true });
}

async function handleAdminImport(request, env) {
  // Imports an offline-issued license into the Worker registry. Used to
  // sync the developer's pre-Worker licenses (or any legacy ones) so they
  // show up in the License Manager UI.
  //
  // We trust the admin token here — we don't re-verify the signature
  // server-side. The client (License Manager) verifies first with the
  // embedded public key before calling /admin/import.
  const body = await request.json().catch(() => ({}));
  const payload = body.payload || body;
  const licenseId = String(payload.license_id || "").trim();
  if (!licenseId) return json({ error: "license_id required" }, 400);

  const data = await loadUsers(env);
  const existing = data.users.find((u) => u.license_id === licenseId);
  if (existing) {
    // Refresh metadata but don't reset expiry
    existing.last_seen = new Date().toISOString();
    if (body.machine_id) existing.machine_id = String(body.machine_id);
    if (body.hostname) existing.hostname = String(body.hostname);
    if (body.os_user) existing.os_user = String(body.os_user);
    if (body.version) existing.version = String(body.version);
    await saveUsers(env, data);
    return json({ ok: true, already_existed: true });
  }

  data.users.push({
    license_id: licenseId,
    machine_id: String(body.machine_id || payload.machine_id_hash || ""),
    email: String(payload.email || "").toLowerCase(),
    name: String(payload.name || ""),
    hostname: String(body.hostname || ""),
    os_user: String(body.os_user || ""),
    version: String(body.version || ""),
    issued_at: payload.issued_at || new Date().toISOString(),
    expires_at: payload.expires_at || "",
    first_seen: payload.issued_at || new Date().toISOString(),
    last_seen: new Date().toISOString(),
    status: "active",
    note: payload.note || "imported from offline issue",
  });
  await saveUsers(env, data);
  return json({ ok: true, imported: true });
}

async function handleAdminUpdateUser(request, env) {
  const body = await request.json().catch(() => ({}));
  const licenseId = String(body.license_id || "").trim();
  if (!licenseId) return json({ error: "license_id required" }, 400);

  const data = await loadUsers(env);
  const user = data.users.find((u) => u.license_id === licenseId);
  if (!user) return json({ error: "not found" }, 404);

  if (body.allowed_version !== undefined) {
    user.allowed_version = String(body.allowed_version).trim() || null;
  }
  await saveUsers(env, data);
  return json({
    ok: true,
    license_id: licenseId,
    allowed_version: user.allowed_version,
  });
}

async function handleAdminRebind(request, env) {
  // Move an existing license to a new device. The admin supplies the new
  // machine_id_hash (computed on the target device). We re-sign the license
  // payload bound to the new machine and return a fresh license_key the user
  // pastes via "Enter / replace license key". The license_id is preserved so
  // the registry row, expiry, and revocation history stay intact.
  const body = await request.json().catch(() => ({}));
  const licenseId = String(body.license_id || "").trim();
  const newMachineId = String(body.machine_id || "").trim();
  if (!licenseId) return json({ error: "license_id required" }, 400);
  if (!newMachineId) return json({ error: "machine_id required" }, 400);

  const data = await loadUsers(env);
  const user = data.users.find((u) => u.license_id === licenseId);
  if (!user) return json({ error: "not found" }, 404);

  const oldMachineId = user.machine_id || "";
  user.machine_id = newMachineId;
  if (body.hostname !== undefined) user.hostname = String(body.hostname).trim();
  if (body.os_user !== undefined) user.os_user = String(body.os_user).trim();
  user.last_seen = new Date().toISOString();
  user.note = `re-bound to new device by admin (was ${oldMachineId || "unbound"})`;
  await saveUsers(env, data);

  // Re-sign the license bound to the NEW machine so validate_license() passes
  // on the target device.
  const payload = {
    license_id: user.license_id,
    name: user.name || "",
    email: user.email,
    issued_at: user.issued_at,
    expires_at: user.expires_at || "",
    machine_id_hash: newMachineId,
    note: user.note || "",
  };
  const signature = await signPayload(payload, env);
  return json({
    ok: true,
    license_id: user.license_id,
    license_key: makeLicenseKey(payload, signature),
    machine_id: newMachineId,
    old_machine_id: oldMachineId,
  });
}

async function handleAdminGenerate(request, env) {
  // Create a brand-new license from scratch and add it to the registry.
  // Body: {name, email, days (0 = perpetual), machine_id ("" = floating),
  //        note}. Returns the signed license_key for the user to paste.
  const body = await request.json().catch(() => ({}));
  const email = String(body.email || "").trim().toLowerCase();
  const name = String(body.name || "").trim();
  if (!email || !email.includes("@")) {
    return json({ error: "valid email required" }, 400);
  }

  const machineId = String(body.machine_id || "").trim(); // "" = floating
  const days = parseInt(body.days, 10);
  const now = new Date();
  let expiresAt = "";
  if (Number.isFinite(days) && days > 0) {
    expiresAt = new Date(now.getTime() + days * 86400000).toISOString();
  } // days == 0 or missing → perpetual (empty expiry)

  const licenseId = newLicenseId();
  const issuedAt = now.toISOString();
  const note = String(body.note || "").trim() || "issued by admin";

  const data = await loadUsers(env);
  data.users.push({
    license_id: licenseId,
    machine_id: machineId,
    email,
    name,
    hostname: String(body.hostname || "").trim(),
    os_user: String(body.os_user || "").trim(),
    version: "",
    issued_at: issuedAt,
    expires_at: expiresAt,
    first_seen: issuedAt,
    last_seen: issuedAt,
    status: "active",
    note,
  });
  await saveUsers(env, data);

  const payload = {
    license_id: licenseId,
    name,
    email,
    issued_at: issuedAt,
    expires_at: expiresAt,
    machine_id_hash: machineId, // "" = floating (any machine)
    note,
  };
  const signature = await signPayload(payload, env);
  return json({
    ok: true,
    license_id: licenseId,
    license_key: makeLicenseKey(payload, signature),
    expires_at: expiresAt,
    floating: machineId === "",
  });
}

async function handleAdminPushVersion(request, env) {
  const body = await request.json().catch(() => ({}));
  const version = String(body.version || "").trim();
  if (!version) return json({ error: "version required" }, 400);

  const data = await loadUsers(env);
  let updated = 0;
  let skippedAdmins = 0;
  for (const user of data.users) {
    if (isAdminUser(user, env)) {
      // Never pin the admin/developer — they always track the latest
      // manifest version. Clear any stale pin so a previously-pinned
      // admin install recovers and can auto-update again.
      if (user.allowed_version) user.allowed_version = null;
      skippedAdmins++;
      continue;
    }
    user.allowed_version = version;
    updated++;
  }
  await saveUsers(env, data);
  return json({ ok: true, version, updated, skipped_admins: skippedAdmins });
}

async function handleAdminDelete(request, env) {
  // Permanently removes a user from the registry. For cleanup of test
  // accounts and confirmed-departed users. Note: this is different from
  // /admin/revoke which keeps the row but marks it blacklisted.
  const body = await request.json().catch(() => ({}));
  const licenseId = String(body.license_id || "").trim();
  if (!licenseId) return json({ error: "license_id required" }, 400);

  const data = await loadUsers(env);
  const before = data.users.length;
  data.users = data.users.filter((u) => u.license_id !== licenseId);
  if (data.users.length === before) {
    return json({ ok: true, not_found: true });
  }
  await saveUsers(env, data);

  // Also remove from revoked list if present
  const rev = await loadRevoked(env);
  rev.revoked = (rev.revoked || []).filter((x) => x !== licenseId);
  if (rev.notes) delete rev.notes[licenseId];
  await saveRevoked(env, rev);

  return json({ ok: true, deleted: true });
}
