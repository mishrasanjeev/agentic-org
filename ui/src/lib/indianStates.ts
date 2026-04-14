// Indian state codes (2-char) and display names.
// Backend `companies.state_code` column is VARCHAR(2); the UI must send the
// code and render the name.

export interface IndianState {
  code: string;
  name: string;
}

export const INDIAN_STATES: readonly IndianState[] = [
  { code: "AN", name: "Andaman & Nicobar" },
  { code: "AP", name: "Andhra Pradesh" },
  { code: "AR", name: "Arunachal Pradesh" },
  { code: "AS", name: "Assam" },
  { code: "BR", name: "Bihar" },
  { code: "CG", name: "Chhattisgarh" },
  { code: "CH", name: "Chandigarh" },
  { code: "DL", name: "Delhi" },
  { code: "GA", name: "Goa" },
  { code: "GJ", name: "Gujarat" },
  { code: "HP", name: "Himachal Pradesh" },
  { code: "HR", name: "Haryana" },
  { code: "JH", name: "Jharkhand" },
  { code: "JK", name: "Jammu & Kashmir" },
  { code: "KA", name: "Karnataka" },
  { code: "KL", name: "Kerala" },
  { code: "LA", name: "Ladakh" },
  { code: "LD", name: "Lakshadweep" },
  { code: "MH", name: "Maharashtra" },
  { code: "ML", name: "Meghalaya" },
  { code: "MN", name: "Manipur" },
  { code: "MP", name: "Madhya Pradesh" },
  { code: "MZ", name: "Mizoram" },
  { code: "NL", name: "Nagaland" },
  { code: "OD", name: "Odisha" },
  { code: "PB", name: "Punjab" },
  { code: "PY", name: "Puducherry" },
  { code: "RJ", name: "Rajasthan" },
  { code: "SK", name: "Sikkim" },
  { code: "TG", name: "Telangana" },
  { code: "TN", name: "Tamil Nadu" },
  { code: "TR", name: "Tripura" },
  { code: "UK", name: "Uttarakhand" },
  { code: "UP", name: "Uttar Pradesh" },
  { code: "WB", name: "West Bengal" },
] as const;

const BY_CODE: Readonly<Record<string, string>> = Object.freeze(
  INDIAN_STATES.reduce<Record<string, string>>((acc, s) => {
    acc[s.code] = s.name;
    return acc;
  }, {}),
);

/** Resolve a 2-char state code to its display name, falling back to the code. */
export function stateNameFromCode(code: string | null | undefined): string {
  if (!code) return "";
  const upper = code.toUpperCase();
  return BY_CODE[upper] ?? code;
}
