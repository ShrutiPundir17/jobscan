const API_URL = import.meta.env.VITE_API_URL ?? "http://localhost:8000";
const TOKEN_KEY = "jobagent_token";

export function getToken(): string | null {
  return localStorage.getItem(TOKEN_KEY);
}

export function setToken(token: string): void {
  localStorage.setItem(TOKEN_KEY, token);
}

export function clearToken(): void {
  localStorage.removeItem(TOKEN_KEY);
}

async function request<T>(
  path: string,
  options: RequestInit = {},
  auth = true,
): Promise<T> {
  const headers = new Headers(options.headers);
  if (!(options.body instanceof FormData) && !headers.has("Content-Type")) {
    headers.set("Content-Type", "application/json");
  }
  if (auth) {
    const token = getToken();
    if (!token) throw new Error("Not authenticated");
    headers.set("Authorization", `Bearer ${token}`);
  }

  const res = await fetch(`${API_URL}${path}`, { ...options, headers });
  if (!res.ok) {
    let detail = `Request failed (${res.status})`;
    try {
      const body = (await res.json()) as { detail?: unknown };
      if (typeof body.detail === "string") {
        detail = body.detail;
      } else if (Array.isArray(body.detail) && body.detail.length > 0) {
        const first = body.detail[0] as { msg?: string };
        detail = first.msg?.replace(/^Value error,\s*/i, "") || JSON.stringify(body.detail);
      } else if (body.detail) {
        detail = JSON.stringify(body.detail);
      }
    } catch {
      /* ignore */
    }
    throw new Error(detail);
  }
  if (res.status === 204) return undefined as T;
  return (await res.json()) as T;
}

export const api = {
  register(email: string, password: string, fullName?: string) {
    return request<{
      id: string;
      email: string;
      full_name: string | null;
    }>(
      "/auth/register",
      {
        method: "POST",
        body: JSON.stringify({
          email,
          password,
          full_name: fullName || null,
        }),
      },
      false,
    );
  },
  login(email: string, password: string) {
    return request<{ access_token: string; token_type: string }>(
      "/auth/login",
      { method: "POST", body: JSON.stringify({ email, password }) },
      false,
    );
  },
  forgotPassword(email: string) {
    return request<{
      message: string;
      reset_token?: string | null;
      reset_url?: string | null;
    }>(
      "/auth/forgot-password",
      { method: "POST", body: JSON.stringify({ email }) },
      false,
    );
  },
  resetPassword(token: string, newPassword: string) {
    return request<{ message: string }>(
      "/auth/reset-password",
      {
        method: "POST",
        body: JSON.stringify({ token, new_password: newPassword }),
      },
      false,
    );
  },
  forgotUsername(phone: string) {
    return request<{
      message: string;
      login_email?: string | null;
    }>(
      "/auth/forgot-username",
      { method: "POST", body: JSON.stringify({ phone }) },
      false,
    );
  },
  me() {
    return request<import("./types").UserProfile>("/users/me");
  },
  updatePreferences(payload: {
    full_name?: string | null;
    auto_apply_enabled?: boolean;
    min_match_score?: number;
    target_roles?: string[];
    preferred_locations?: string[];
    phone?: string | null;
    notify_email_enabled?: boolean;
    notify_whatsapp_enabled?: boolean;
  }) {
    return request<import("./types").UserProfile>("/users/me", {
      method: "PATCH",
      body: JSON.stringify(payload),
    });
  },
  listNotifications(unreadOnly = false) {
    const q = unreadOnly ? "?unread_only=true" : "";
    return request<{
      items: Array<{
        id: string;
        type: string;
        title: string;
        body: string;
        read_at: string | null;
        email_status: string | null;
        whatsapp_status: string | null;
        created_at: string;
      }>;
      total: number;
      unread_count: number;
    }>(`/notifications${q}`);
  },
  markNotificationRead(id: string) {
    return request<{ id: string; read_at: string | null }>(
      `/notifications/${id}/read`,
      { method: "PATCH" },
    );
  },
  sendTestNotification() {
    return request<{
      status: string;
      notification_id?: string | null;
      email?: string | null;
      whatsapp?: string | null;
      platform?: { email_configured?: boolean; whatsapp_configured?: boolean } | null;
      hint?: string | null;
      reason?: string | null;
    }>("/notifications/test", { method: "POST" });
  },
  listResumes() {
    return request<import("./types").ResumeListResponse>("/resumes");
  },
  uploadResume(file: File) {
    const form = new FormData();
    form.append("file", file);
    return request<import("./types").ResumeItem>("/resumes/upload", {
      method: "POST",
      body: form,
    });
  },
  parseResume(id: string) {
    return request<import("./types").ResumeItem>(`/resumes/${id}/parse`, {
      method: "POST",
    });
  },
  embedResume(id: string) {
    return request<import("./types").ResumeItem>(`/resumes/${id}/embed`, {
      method: "POST",
    });
  },
  listMatches(minScore?: number) {
    const q = minScore != null ? `?min_score=${minScore}` : "";
    return request<{
      items: import("./types").PersistedMatch[];
      total: number;
      limit: number;
      offset: number;
    }>(`/matches${q}`);
  },
  scoreMatches(body: {
    limit?: number;
    persist?: boolean;
    min_similarity?: number;
    apply_location_prefs?: boolean;
  } = {}) {
    return request<{
      stage: string;
      count: number;
      persisted_count: number;
      min_match_score: number;
      results: Array<{
        score: number;
        verdict: string;
        application_id: string | null;
        persisted: boolean;
        job: import("./types").MatchJob;
      }>;
    }>("/matches/score", {
      method: "POST",
      body: JSON.stringify({
        limit: body.limit ?? 10,
        persist: body.persist ?? true,
        min_similarity: body.min_similarity ?? 0.3,
        apply_location_prefs: body.apply_location_prefs ?? true,
      }),
    });
  },
  tailorMatch(applicationId: string) {
    return request<{
      application_id: string;
      tailored_bullets: import("./types").PersistedMatch["tailored_bullets"];
      tailored_resume_text: string;
      tailored_pitch: string | null;
      job: import("./types").MatchJob;
    }>(`/matches/${applicationId}/tailor`, { method: "POST" });
  },
  listApplications(status?: string) {
    const q = status ? `?status=${encodeURIComponent(status)}` : "";
    return request<{
      items: import("./types").ApplicationItem[];
      total: number;
    }>(`/applications${q}`);
  },
  applyToJob(applicationId: string) {
    return request<{
      id: string;
      status: string;
      applied_at: string | null;
      job_url: string;
      message: string;
    }>(`/applications/${applicationId}/apply`, { method: "POST" });
  },
  updateApplication(
    applicationId: string,
    payload: { status?: string; notes?: string | null },
  ) {
    return request<import("./types").ApplicationItem>(
      `/applications/${applicationId}`,
      { method: "PATCH", body: JSON.stringify(payload) },
    );
  },
  withdrawApplication(applicationId: string) {
    return request<import("./types").ApplicationItem>(
      `/applications/${applicationId}/withdraw`,
      { method: "POST" },
    );
  },
  triggerScan() {
    return request<{ status: string; task_id: string; message: string }>(
      "/scanner/trigger",
      { method: "POST" },
    );
  },
  agentStatus() {
    return request<{
      state: string;
      scanner_enabled: boolean;
      last_scan_at: string | null;
      jobs_scanned_today: number;
      high_match_count: number;
      high_match_threshold: number;
      server_time: string;
    }>("/scanner/status");
  },
  triggerEmbedJobs() {
    return request<{ status: string; task_id: string; message: string }>(
      "/scanner/embed-jobs",
      { method: "POST", body: JSON.stringify({}) },
    );
  },
};
