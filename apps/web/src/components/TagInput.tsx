import { useState } from "react";

type Props = {
  label: string;
  values: string[];
  onChange: (next: string[]) => void;
  placeholder: string;
};

export function TagInput({ label, values, onChange, placeholder }: Props) {
  const [draft, setDraft] = useState("");

  function addTag() {
    const value = draft.trim();
    if (!value) return;
    if (values.some((v) => v.toLowerCase() === value.toLowerCase())) {
      setDraft("");
      return;
    }
    onChange([...values, value]);
    setDraft("");
  }

  return (
    <label className="field">
      {label}
      <div className="chip-row" style={{ marginBottom: "0.35rem" }}>
        {values.map((value) => (
          <span className="chip" key={value}>
            {value}
            <button
              type="button"
              aria-label={`Remove ${value}`}
              onClick={() => onChange(values.filter((v) => v !== value))}
            >
              ×
            </button>
          </span>
        ))}
        <button type="button" className="chip chip-add" onClick={addTag}>
          + Add
        </button>
      </div>
      <input
        type="text"
        value={draft}
        placeholder={placeholder}
        onChange={(e) => setDraft(e.target.value)}
        onKeyDown={(e) => {
          if (e.key === "Enter") {
            e.preventDefault();
            addTag();
          }
        }}
      />
    </label>
  );
}
