// Mirrors backend/app/schemas.py. Keep in sync when response shapes change.

export type Role = "admin" | "member" | "viewer";
export type Strategy = "fcfs" | "need_weighted" | "priority";
export type RoundStatus = "draft" | "open" | "closed" | "allocated" | "cancelled";

export interface Organization {
  id: number;
  name: string;
  default_weight: number | null;
  default_priority: number | null;
  region: string | null;
  is_active: boolean | null;
  version: number | null;
}

export interface User {
  id: number;
  email: string;
  display_name: string;
  role: Role;
  organization_id: number | null;
  is_active: boolean;
  version: number;
}

export interface Me {
  user: User;
  organization: Organization | null;
  csrf_token: string;
  session_expires_at: string;
}

export interface Lot {
  id: number;
  name: string;
  description: string;
  unit: string;
  quantity_on_hand: number;
  quantity_reserved: number;
  quantity_available: number;
  best_before: string | null;
  reserved_by_round_id: number | null;
  version: number;
}

export interface RoundLot {
  lot_id: number;
  name: string;
  description: string;
  unit: string;
  best_before: string | null;
  quantity: number;
  status: string;
  allocated_quantity: number | null;
}

export interface RequestItem {
  id: number;
  round_id: number;
  lot_id: number;
  organization_id: number;
  organization_name: string | null;
  quantity: number;
  status: "active" | "withdrawn";
  version: number;
  accepted_seq: number | null;
  created_at: string;
  updated_at: string;
}

export interface Participant {
  organization_id: number;
  organization_name: string;
  organization_active: boolean;
  eligible: boolean;
  weight: number;
  priority: number;
  version: number;
}

export interface RoundSummary {
  id: number;
  name: string;
  description: string;
  status: RoundStatus;
  strategy: Strategy;
  strategy_label: string;
  request_deadline: string;
  deadline_passed: boolean;
  public_version: number;
  version: number | null;
  org_version: number | null;
  lot_count: number;
  total_quantity: number;
  my_request_count: number | null;
  opened_at: string | null;
  closed_at: string | null;
  allocated_at: string | null;
  cancelled_at: string | null;
}

export interface RoundDetail extends RoundSummary {
  lots: RoundLot[];
  requests: RequestItem[];
  participation: Participant | null;
  participants: Participant[] | null;
  snapshot: {
    strategy: Strategy;
    strategy_version: number;
    input_hash: string;
    result_hash: string | null;
    created_at: string;
    finalized_at: string | null;
  } | null;
}

export interface ResultLine {
  request_id: number;
  organization_id: number;
  organization_name: string | null;
  requested: number;
  allocated: number;
  explanation: string;
  details: Record<string, unknown>;
}

export interface LotOutcome {
  lot_id: number;
  name: string;
  unit: string;
  supply: number;
  allocated_total: number;
  unallocated: number;
  lines: ResultLine[];
}

export interface Allocation {
  round_id: number;
  is_preview: boolean;
  strategy: Strategy;
  strategy_label: string;
  strategy_version: number;
  frozen_strategy: Strategy;
  input_hash: string;
  result_hash: string;
  lots: LotOutcome[];
}

export interface PublicRound {
  id: number;
  name: string;
  description: string;
  status: RoundStatus;
  strategy_label: string;
  request_deadline: string;
  public_version: number;
  lots: {
    name: string;
    unit: string;
    quantity: number;
    allocated_total: number | null;
    organizations_served: number | null;
  }[];
}

export interface AuditEntry {
  id: number;
  created_at: string;
  actor_user_id: number | null;
  actor_email: string | null;
  actor_name: string | null;
  action: string;
  target_type: string;
  target_id: string;
  round_id: number | null;
  before: Record<string, unknown> | null;
  after: Record<string, unknown> | null;
  reason: string | null;
  request_id: string | null;
}

export interface VersionInfo {
  round_id: number;
  public_version: number;
  org_version?: number;
  version?: number;
}

export const STRATEGY_HELP: Record<Strategy, string> = {
  fcfs: "Requests are filled in the order they were accepted until supply runs out. Editing a quantity keeps your place; withdrawing and resubmitting moves you to the back. Rewards responding early.",
  need_weighted:
    "Supply is shared in proportion to each organization's need weight, never giving more than was requested; anything left over from smaller requests is shared again. Whole units are assigned by rounding down and giving leftover units to the largest remainders. Order of submission does not matter except to break exact ties.",
  priority:
    "Organizations are grouped into priority tiers (tier 1 is served first). Within a tier, requests are filled in the order they were accepted. Lower tiers receive supply only after higher tiers are fully served.",
};

export const STRATEGY_LABEL: Record<Strategy, string> = {
  fcfs: "First come, first served",
  need_weighted: "Need-weighted share",
  priority: "Priority tiers",
};

// ---------------------------------------------------------------- messaging

export type ChannelKind = "global" | "local" | "group" | "direct";

export interface Person {
  id: number;
  display_name: string;
  role: Role;
  organization_name: string | null;
}

export interface ChatMessage {
  id: number;
  channel_id: number;
  kind: "user" | "system";
  sender: Person | null;
  body: string;
  created_at: string;
  deleted: boolean;
  client_id: string | null;
}

export interface Channel {
  id: number;
  kind: ChannelKind;
  title: string;
  subtitle: string;
  region: string | null;
  members: Person[] | null;
  member_count: number;
  last_message: ChatMessage | null;
  last_activity_at: string;
  last_read_message_id: number;
  unread_count: number;
  version: number;
}

export interface MessagePage {
  messages: ChatMessage[];
  has_more: boolean;
}

// ---------------------------------------------------------------- planning

export type Category =
  "produce" | "protein" | "dairy" | "grains" | "canned" | "baby" | "hygiene" | "other";

export interface Preferences {
  households_served: number;
  priority_categories: Category[];
  avoid_categories: Category[];
  dry_storage_capacity: number | null;
  cold_storage_capacity: number | null;
  frozen_storage_capacity: number | null;
  max_share_percent: number;
  dietary_notes: string;
}

export interface PlanningProfile {
  organization_id: number;
  organization_name: string;
  preferences: Preferences;
  version: number;
  updated_at: string | null;
  updated_by_name: string | null;
}

export interface SuggestedProfile {
  preferences: Preferences;
  rationale: Record<string, string>;
  based_on_rounds: number;
}

export interface Need {
  item: string;
  quantity: number | null;
  category: Category;
}

export interface DocumentAnalysis {
  format: "csv" | "xlsx" | "markdown" | "text" | "json" | "binary" | "note";
  columns: string[];
  rows: string[][];
  row_count: number;
  sheets: string[];
  needs: Need[];
  categories: Partial<Record<Category, number>>;
  dietary: string[];
  warnings: string[];
}

export interface PlanningDocument {
  id: number;
  kind: "upload" | "note";
  title: string;
  filename: string | null;
  content_type: string | null;
  size_bytes: number;
  created_at: string;
  created_by_name: string | null;
  analysis: DocumentAnalysis;
}

export interface PlanningDocumentDetail extends PlanningDocument {
  text: string;
}

export interface PlanLine {
  round_id: number;
  lot_id: number;
  lot_name: string;
  unit: string;
  category: Category;
  storage: "dry" | "cold" | "frozen";
  offered: number;
  suggested: number;
  current_quantity: number | null;
  current_request_id: number | null;
  current_request_version: number | null;
  reasons: string[];
}

export interface PlanRound {
  round_id: number;
  round_name: string;
  strategy: Strategy;
  strategy_label: string;
  request_deadline: string;
  tip: string;
  lines: PlanLine[];
}

export interface Plan {
  key: "balanced" | "priorities" | "lean";
  title: string;
  tagline: string;
  summary: string;
  total_units: number;
  line_count: number;
  rounds: PlanRound[];
  warnings: string[];
}

export interface Plans {
  organization_id: number;
  organization_name: string;
  generated_at: string;
  inputs: {
    preferences_version: number;
    documents: number;
    needs: number;
    open_rounds: number;
    lots: number;
  };
  needs: (Need & { source: string })[];
  insights: {
    rounds_participated: number;
    requested_total: number;
    allocated_total: number;
    fill_rate_percent: number | null;
    top_categories: Category[];
  };
  plans: Plan[];
}
