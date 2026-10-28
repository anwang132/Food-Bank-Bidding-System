import type { Category } from "../../api/types";

export const CATEGORIES: Category[] = [
  "produce",
  "protein",
  "dairy",
  "grains",
  "canned",
  "baby",
  "hygiene",
  "other",
];

export const CATEGORY_LABEL: Record<Category, string> = {
  produce: "Fresh produce",
  protein: "Protein",
  dairy: "Dairy",
  grains: "Grains and cereal",
  canned: "Canned goods",
  baby: "Baby care",
  hygiene: "Hygiene",
  other: "Other",
};

export const STORAGE_LABEL = { dry: "Dry", cold: "Refrigerated", frozen: "Frozen" } as const;

export const MAX_UPLOAD_BYTES = 5 * 1024 * 1024;

/** Query-string suffix naming the food bank; members are always scoped server-side. */
export function orgParam(orgId: number | null, joiner: "?" | "&" = "?"): string {
  return orgId === null ? "" : `${joiner}organization_id=${orgId}`;
}

export function planningKey(orgId: number | null, ...rest: string[]) {
  return ["planning", orgId ?? "mine", ...rest] as const;
}
