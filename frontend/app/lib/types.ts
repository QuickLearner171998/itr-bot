export type QuestionType = "bool" | "number" | "choice";

export interface Question {
  id: string;
  text: string;
  type: QuestionType;
  options?: string[];
  default?: string | number | boolean;
  help?: string;
  depends_on?: string;
  extractable?: boolean;
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
  covered_by?: string | null;
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
  display_only?: boolean;
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

export interface Discrepancy {
  field: string;
  label: string;
  sources: { doc: string; value: number }[];
  chosen: number;
  note: string;
}

export interface SalaryComponent {
  employer_name: string;
  gross_salary: number;
  exempt_allowances: number;
  professional_tax: number;
  tds: number;
}

// Mirrors backend TaxInput; only the fields the review screen edits are typed
// explicitly, the rest pass through untouched.
export interface TaxInput {
  age: number;
  filing_regime: string;
  salaries: SalaryComponent[];
  let_out_annual_rent: number;
  let_out_municipal_taxes: number;
  hra_received: number;
  hra_rent_paid: number;
  hra_basic_da: number;
  hra_is_metro: boolean;
  savings_interest: number;
  fd_interest: number;
  dividend: number;
  family_pension: number;
  other_income: number;
  agricultural_income: number;
  brought_forward_loss: number;
  capital_gains: {
    stcg_111a: number;
    ltcg_112a: number;
    stcg_other: number;
    ltcg_other: number;
    vda_gain: number;
  };
  deductions: {
    amount_80c: number;
    amount_80ccd1b: number;
    amount_80ccd2: number;
    amount_80d_self: number;
    amount_80d_parents: number;
    home_loan_interest: number;
    amount_80e: number;
    amount_80g?: number;
    [key: string]: any;
  };
  tds_total: number;
  advance_tax: number;
  self_assessment_tax: number;
  relief_89: number;
  relief_90_91: number;
  [key: string]: any;
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
  result: RegimeResult;
  regime: string;
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
