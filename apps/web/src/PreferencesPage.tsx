import { FormEvent, useEffect, useState } from "react";
import { api } from "./api";
import { TagInput } from "./components/TagInput";
import type { UserProfile } from "./types";

type Props = {
  onMessage: (msg: string | null) => void;
  onError: (msg: string | null) => void;
};

export function PreferencesPage({ onMessage, onError }: Props) {
  const [profile, setProfile] = useState<UserProfile | null>(null);
  const [fullName, setFullName] = useState("");
  const [roles, setRoles] = useState<string[]>([]);
  const [locations, setLocations] = useState<string[]>([]);
  const [autoApply, setAutoApply] = useState(false);
  const [minScore, setMinScore] = useState(70);
  const [phone, setPhone] = useState("");
  const [notifyEmail, setNotifyEmail] = useState(true);
  const [notifyWhatsapp, setNotifyWhatsapp] = useState(false);
  const [saving, setSaving] = useState(false);
  const [loading, setLoading] = useState(true);

  useEffect(() => {
    void (async () => {
      try {
        const me = await api.me();
        setProfile(me);
        setFullName(me.full_name ?? "");
        setRoles(me.target_roles ?? []);
        setLocations(me.preferred_locations ?? []);
        setAutoApply(me.auto_apply_enabled);
        setMinScore(me.min_match_score);
        setPhone(me.phone ?? "");
        setNotifyEmail(me.notify_email_enabled ?? true);
        setNotifyWhatsapp(me.notify_whatsapp_enabled ?? false);
      } catch (err) {
        onError(err instanceof Error ? err.message : "Failed to load preferences");
      } finally {
        setLoading(false);
      }
    })();
  }, []);

  async function save(e: FormEvent) {
    e.preventDefault();
    setSaving(true);
    onError(null);
    try {
      const updated = await api.updatePreferences({
        full_name: fullName.trim() || null,
        auto_apply_enabled: autoApply,
        min_match_score: minScore,
        target_roles: roles,
        preferred_locations: locations,
        phone: phone.trim() || null,
        notify_email_enabled: notifyEmail,
        notify_whatsapp_enabled: notifyWhatsapp,
      });
      setProfile(updated);
      onMessage("Preferences saved.");
    } catch (err) {
      onError(err instanceof Error ? err.message : "Save failed");
    } finally {
      setSaving(false);
    }
  }

  if (loading) return <p className="muted">Loading preferences…</p>;

  return (
    <form className="fade-in stack" style={{ maxWidth: 560 }} onSubmit={save}>
      <h1 style={{ fontSize: "1.35rem" }}>Preferences</h1>
      <p className="muted" style={{ marginBottom: "0.5rem" }}>
        Signed in as {profile?.email}
      </p>

      <label className="field">
        Full name
        <input value={fullName} onChange={(e) => setFullName(e.target.value)} />
      </label>

      <TagInput
        label="Target roles"
        values={roles}
        onChange={setRoles}
        placeholder="e.g. Backend Engineer"
      />
      <TagInput
        label="Preferred locations"
        values={locations}
        onChange={setLocations}
        placeholder="e.g. Hyderabad"
      />

      <div className="slider-row">
        <div className="row space-between">
          <span className="field" style={{ margin: 0 }}>
            Minimum match score
          </span>
          <span className="mono">{minScore}</span>
        </div>
        <input
          type="range"
          min={0}
          max={100}
          value={minScore}
          onChange={(e) => setMinScore(Number(e.target.value))}
        />
      </div>

      <div className="toggle-row">
        <span style={{ fontSize: "0.875rem", color: "var(--text-muted)" }}>
          Auto-apply when match score is high enough
        </span>
        <button
          type="button"
          className={`toggle ${autoApply ? "on" : ""}`}
          onClick={() => setAutoApply((v) => !v)}
        />
      </div>

      <label className="field">
        Phone (WhatsApp)
        <input
          value={phone}
          onChange={(e) => setPhone(e.target.value)}
          placeholder="+91…"
        />
      </label>

      <div className="toggle-row">
        <span style={{ fontSize: "0.875rem", color: "var(--text-muted)" }}>Email alerts</span>
        <button
          type="button"
          className={`toggle ${notifyEmail ? "on" : ""}`}
          onClick={() => setNotifyEmail((v) => !v)}
        />
      </div>
      <div className="toggle-row" style={{ borderBottom: "1px solid var(--border)" }}>
        <span style={{ fontSize: "0.875rem", color: "var(--text-muted)" }}>
          WhatsApp alerts
        </span>
        <button
          type="button"
          className={`toggle ${notifyWhatsapp ? "on" : ""}`}
          onClick={() => setNotifyWhatsapp((v) => !v)}
        />
      </div>

      <button type="submit" className="btn btn-primary" disabled={saving}>
        {saving ? "Saving…" : "Save preferences"}
      </button>
    </form>
  );
}
