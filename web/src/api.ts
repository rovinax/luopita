export type PlatformSnap = { name: string; enabled: boolean };

export type PublicConfig = {
  host: string;
  port: number;
  database_url?: string;
  database?: string;
  redis?: string;
  admin_auth_required?: boolean;
  admin_token?: string;
  log_level: string;
  llm: {
    provider: string;
    model: string;
    vision_model?: string;
    base_url: string;
    api_key: string;
  };
  napcat: {
    enabled: boolean;
    bot_url: string;
    bot_id: string;
    access_token: string;
  };
  tui: { enabled: boolean };
  agent: {
    timeout_sec: number;
    workdir: string;
    command_allowlist: string[];
    short_term_messages?: number;
  };
  persona: {
    name: string;
    owner_address?: string;
    voice?: string;
    taboos?: string;
    relationship?: string;
    system_prompt: string;
  };
  identity?: IdentitySettings;
};

export type IdentityOwner = {
  platform: string;
  user_id: string;
  nickname: string;
};

export type VoiceExample = {
  id: string;
  scene: string;
  mode: string;
  relation: string;
  input: string;
  good: string;
  bad: string;
};

export type SpeakerProfile = {
  platform: string;
  chat_id: string;
  user_id: string;
  display_name: string;
  updated_at: string;
  prompt: string;
  card: {
    address: string;
    familiarity: string;
    reply_pref: string;
    stack: string[];
    taboos: string;
    recent: string;
    evidence: string;
    updated_at: string;
  };
};

export type IdentitySettings = {
  owners: IdentityOwner[];
  group_require_at: boolean;
  wake_keywords?: string[];
  group_tech_chance?: number;
  group_chatty_chance?: number;
  group_chime_cooldown_sec?: number;
  group_engage_sec?: number;
  group_engage_replies?: number;
};

const TOKEN_KEY = "luopita_admin_token";

export function getAdminToken(): string {
  try {
    return (localStorage.getItem(TOKEN_KEY) || "").trim();
  } catch {
    return "";
  }
}

export function setAdminToken(token: string): void {
  try {
    const value = (token || "").trim();
    if (value) {
      localStorage.setItem(TOKEN_KEY, value);
    } else {
      localStorage.removeItem(TOKEN_KEY);
    }
  } catch {
    /* ignore */
  }
}

function authHeaders(extra?: HeadersInit): HeadersInit {
  const token = getAdminToken();
  const headers: Record<string, string> = {};
  if (extra) {
    Object.assign(headers, extra as Record<string, string>);
  }
  if (token) {
    headers.Authorization = `Bearer ${token}`;
  }
  return headers;
}

async function parse<T>(res: Promise<Response> | Response): Promise<T> {
  const resolved = await res;
  if (!resolved.ok) {
    const body = await resolved.text();
    throw new Error(body || resolved.statusText);
  }
  return resolved.json() as Promise<T>;
}

export const api = {
  health: () =>
    parse<{ ok: boolean; provider: string; database: string; db_ok: boolean; redis?: string; redis_ok?: boolean }>(
      fetch("/health"),
    ),
  config: () =>
    parse<{ ok: boolean; config: PublicConfig; platforms: PlatformSnap[] }>(
      fetch("/api/config", { headers: authHeaders() }),
    ),
  saveConfig: (body: Record<string, unknown>) =>
    parse<{ ok: boolean; config: PublicConfig }>(
      fetch("/api/config", {
        method: "PUT",
        headers: authHeaders({ "Content-Type": "application/json" }),
        body: JSON.stringify(body),
      }),
    ),
  persona: () =>
    parse<{ ok: boolean; persona: PublicConfig["persona"] }>(fetch("/api/persona", { headers: authHeaders() })),
  savePersona: (body: PublicConfig["persona"]) =>
    parse<{ ok: boolean; persona: PublicConfig["persona"] }>(
      fetch("/api/persona", {
        method: "PUT",
        headers: authHeaders({ "Content-Type": "application/json" }),
        body: JSON.stringify(body),
      }),
    ),
  identity: () =>
    parse<{ ok: boolean; identity: IdentitySettings }>(fetch("/api/identity", { headers: authHeaders() })),
  saveIdentity: (body: IdentitySettings) =>
    parse<{ ok: boolean; identity: IdentitySettings }>(
      fetch("/api/identity", {
        method: "PUT",
        headers: authHeaders({ "Content-Type": "application/json" }),
        body: JSON.stringify(body),
      }),
    ),
  examples: () =>
    parse<{
      ok: boolean;
      examples: VoiceExample[];
      scenes: string[];
      modes: string[];
      relations: string[];
    }>(fetch("/api/examples", { headers: authHeaders() })),
  saveExamples: (examples: VoiceExample[]) =>
    parse<{
      ok: boolean;
      examples: VoiceExample[];
      scenes: string[];
      modes: string[];
      relations: string[];
    }>(
      fetch("/api/examples", {
        method: "PUT",
        headers: authHeaders({ "Content-Type": "application/json" }),
        body: JSON.stringify({ examples }),
      }),
    ),
  profiles: () =>
    parse<{ ok: boolean; profiles: SpeakerProfile[] }>(fetch("/api/profiles", { headers: authHeaders() })),
  deleteProfile: (platform: string, chat_id: string, user_id: string) =>
    parse<{ ok: boolean }>(
      fetch(
        `/api/profiles?platform=${encodeURIComponent(platform)}&chat_id=${encodeURIComponent(chat_id)}&user_id=${encodeURIComponent(user_id)}`,
        { method: "DELETE", headers: authHeaders() },
      ),
    ),
  togglePlatform: (name: string, enabled?: boolean) =>
    parse<{ ok: boolean; platforms: PlatformSnap[] }>(
      fetch(`/api/platforms/${name}/toggle`, {
        method: "POST",
        headers: authHeaders({ "Content-Type": "application/json" }),
        body: JSON.stringify({ enabled }),
      }),
    ),
  sessions: () =>
    parse<{ ok: boolean; sessions: Array<Record<string, unknown>> }>(
      fetch("/api/sessions", { headers: authHeaders() }),
    ),
  messages: (id: string) =>
    parse<{ ok: boolean; messages: Array<{ role: string; content: string; created_at?: string }> }>(
      fetch(`/api/sessions/${encodeURIComponent(id)}/messages`, { headers: authHeaders() }),
    ),
  chat: (text: string, sessionId?: string) =>
    parse<{ ok: boolean; session_id: string; reply: string }>(
      fetch("/api/chat", {
        method: "POST",
        headers: authHeaders({ "Content-Type": "application/json" }),
        body: JSON.stringify({ text, user_id: "admin", session_id: sessionId }),
      }),
    ),
};
