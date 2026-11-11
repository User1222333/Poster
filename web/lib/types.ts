/* eslint-disable @typescript-eslint/no-explicit-any */
// Odpowiedzi API są luźno typowane — kształt definiuje backend (Pydantic + SQL).
export type Rec = Record<string, any>;

export type Step = { type: string } & Rec;
export type Rule = { target_column: string; steps: Step[] };

export type Meta = {
  canonical_fields: { id: string; label: string }[];
  step_types: string[];
  ops: string[];
  patterns: { id: string; label: string }[];
  units: string[];
  requirement_types: string[];
  row_target: string;
};
