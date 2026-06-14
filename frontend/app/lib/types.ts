export type QuestionType = "bool" | "number" | "choice";

export interface Question {
  id: string;
  text: string;
  type: QuestionType;
  options?: string[];
  default?: string | number | boolean;
  help?: string;
  depends_on?: string;
}

export interface QSection {
  section: string;
  questions: Question[];
}

export interface ChecklistItem {
  doc_type: string;
  title: string;
  required: boolean;
  why: string;
  how_to_get: string[];
  source: string;
}

export interface FormDecision {
  form: string;
  reasons: string[];
}

export interface ExtractedField {
  name: string;
  label: string;
  value: number | string | null;
  confidence: number;
  source_hint?: string | null;
  flagged: boolean;
}

export interface ValidationIssue {
  severity: string;
  message: string;
  fields: string[];
}

export interface DocumentExtraction {
  doc_type: string;
  filename: string;
  fields: ExtractedField[];
  issues: ValidationIssue[];
  overall_confidence: number;
  status: string;
}

export interface ComputeStep {
  key: string;
  label: string;
  amount: number;
  kind: string;
}

export interface RegimeResult {
  regime: string;
  steps: ComputeStep[];
  gross_total_income: number;
  total_deductions: number;
  total_income: number;
  total_tax_liability: number;
  surcharge: number;
  cess: number;
  rebate_87a: number;
  taxes_paid: number;
  refund_or_payable: number;
}

export interface TaxComputation {
  old: RegimeResult;
  new: RegimeResult;
  recommended_regime: string;
  recommended_savings: number;
  verified: boolean;
  verification_note: string;
}

export interface GuideField {
  label: string;
  value: string;
  note: string;
}

export interface GuideSection {
  title: string;
  portal_path: string;
  fields: GuideField[];
  note: string;
}

export interface StreamEvent {
  type: string;
  session_id: string;
  ts: number;
  data: Record<string, any>;
  message?: string | null;
}
