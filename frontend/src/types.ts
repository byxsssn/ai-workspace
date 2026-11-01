export interface User {
  id: string;
  email: string;
  is_active: boolean;
  created_at: string;
}

export interface TokenResponse {
  access_token: string;
  token_type: string;
}

export interface Credential {
  provider: string;
  configured: boolean;
  created_at: string;
  updated_at: string;
}

export interface Conversation {
  id: string;
  title: string;
  created_at: string;
  updated_at: string;
  learning_topic?: "fastapi" | null;
  learning_source?: LearningSource | null;
}

export interface Message {
  id: string;
  role: string;
  content: string;
  created_at: string;
}

export interface ChatResponse {
  content: string;
  model: string;
  usage: {
    prompt_tokens: number | null;
    completion_tokens: number | null;
    total_tokens: number | null;
  } | null;
  source?: LearningSource;
  status?: "answered" | "needs_clarification";
  sources?: LearningCitation[];
  tool_calls?: LearningToolExecution[];
}

export type LearningSource = "notion" | "demo";

export interface LearningContext {
  learning_topic: "fastapi";
  learning_source: LearningSource;
}

export interface LearningRecord {
  id: string;
  topic_id: "fastapi" | null;
  topic_name: string;
  category: string | null;
  priority: string | null;
  source: LearningSource;
  source_url: string | null;
  source_updated_at: string | null;
  fetched_at: string;
  learning_status: string | null;
  current_progress: string | null;
  next_step: string | null;
  proficiency: string | null;
  project_practice: boolean | null;
  official_docs_url: string | null;
  evidence_url: string | null;
  warnings: string[];
}

export interface LearningRecordDetail extends LearningRecord {
  notes_markdown: string;
  notes_complete: boolean;
}

export interface LearningRecordList {
  source: LearningSource;
  items: LearningRecord[];
  fetched_at: string;
  has_more: boolean;
  warnings: string[];
}

export interface LearningCitation {
  source_id: string;
  kind: "official_document" | "learning_record";
  title: string;
  url: string | null;
  fetched_at: string;
  content_sha256: string | null;
  truncated: boolean;
}

export interface LearningToolExecution {
  tool: "get_learning_state" | "read_official_doc";
  section_id: string | null;
  status: "success" | "error";
  error_code: string | null;
  cached: boolean;
}
