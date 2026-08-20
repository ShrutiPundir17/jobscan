export type UserProfile = {
  id: string;
  email: string;
  full_name: string | null;
  auto_apply_enabled: boolean;
  min_match_score: number;
  target_roles: string[];
  preferred_locations: string[];
  phone: string | null;
  notify_email_enabled: boolean;
  notify_whatsapp_enabled: boolean;
  created_at: string;
};

export type ResumeItem = {
  id: string;
  filename: string | null;
  raw_text: string | null;
  parsed_data: ParsedResumeData | Record<string, unknown> | null;
  is_primary: boolean;
  has_embedding: boolean;
  embedding_model: string | null;
  embedded_at: string | null;
  created_at: string;
};

export type ParsedExperience = {
  title?: string | null;
  company?: string | null;
  start_date?: string | null;
  end_date?: string | null;
  description?: string | null;
};

export type ParsedEducation = {
  institution?: string | null;
  degree?: string | null;
  field_of_study?: string | null;
  end_date?: string | null;
};

export type ParsedProject = {
  name?: string | null;
  description?: string | null;
  technologies?: string[];
};

export type ParsedCertification = {
  name?: string | null;
  issuer?: string | null;
  date_issued?: string | null;
};

export type ParsedResumeData = {
  full_name?: string | null;
  email?: string | null;
  phone?: string | null;
  summary?: string | null;
  seniority?: string | null;
  total_years_experience?: number | null;
  skills?: string[];
  experience?: ParsedExperience[];
  education?: ParsedEducation[];
  projects?: ParsedProject[];
  certifications?: Array<string | ParsedCertification>;
  languages?: string[];
};

export type ResumeListResponse = {
  items: ResumeItem[];
  total: number;
};

export type MatchJob = {
  id: string;
  source: string;
  title: string;
  company: string;
  location: string | null;
  url: string;
  employment_type: string | null;
  salary_min: number | null;
  salary_max: number | null;
  currency: string | null;
  posted_at: string | null;
  description?: string | null;
};

export type PersistedMatch = {
  id: string;
  status: string;
  match_score: number | null;
  verdict: string | null;
  match_reasoning: string | null;
  skill_gaps: string[];
  tailored_pitch: string | null;
  tailored_bullets: {
    summary?: string | null;
    experience?: Array<{
      title?: string | null;
      company?: string | null;
      bullets?: string[];
    }>;
    projects?: Array<{
      name?: string | null;
      bullets?: string[];
      technologies?: string[];
    }>;
  } | null;
  tailored_resume_text: string | null;
  resume_id: string | null;
  job: MatchJob;
  created_at: string;
  updated_at: string;
};

export type ApplicationItem = {
  id: string;
  status: string;
  match_score: number | null;
  match_verdict: string | null;
  match_reasoning: string | null;
  skill_gaps: string[];
  match_pitch: string | null;
  tailored_resume_text: string | null;
  notes: string | null;
  applied_at: string | null;
  resume_id: string | null;
  job: MatchJob;
  created_at: string;
  updated_at: string;
};
